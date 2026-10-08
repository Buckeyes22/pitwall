#!/bin/bash
# run-user-journeys.sh — executable user-journey suite for Pitwall.
#
# Walks every hermetic journey from docs/operator/user-journey-catalog.md the
# way a new user would: real CLI invocations, real servers on local ports,
# real HTTP calls. Never uses a real RunPod key; never creates paid resources.
#
# Required env vars:
#   DATABASE_URL   — disposable local Postgres (the suite resets its schema)
#   REDIS_URL      — local Redis
#
# Optional env vars:
#   PITWALL_JOURNEYS_UNIT_LANE=covered — J27 skips the README unit lane (logs
#       "README unit lane: covered by the test job") and still runs the README
#       security lane. CI sets this because its test job already gates the unit
#       lane. Unset (local, release, and final runs) J27 runs both lanes.
#
# Exit codes: 0 all journeys pass; 1 one or more failed.

set -uo pipefail

JOURNEY_FILTER="${1:-}"
# In-process hermetic journeys: no Postgres, Redis, or live provider. J39-J41 use only
# loopback fixtures their own tests start and stop.
IN_PROCESS_JOURNEYS="J23 J24 J25 J27 J28 J32 J33 J37 J38 J39 J40 J41"
in_process() { case " ${IN_PROCESS_JOURNEYS} " in *" $1 "*) return 0 ;; esac; return 1; }
if [ "$#" -gt 1 ] || { [ -n "${JOURNEY_FILTER}" ] && ! in_process "${JOURNEY_FILTER}"; }; then
    printf 'usage: %s [%s]\n' "$0" "${IN_PROCESS_JOURNEYS// /|}" >&2
    exit 2
fi

# J23, J24, J25, and J27 are in-process hermetic journeys: they have no
# Postgres, Redis, network listener, or live RunPod dependency. Every other
# journey uses the disposable local test infrastructure below.
if ! in_process "${JOURNEY_FILTER}"; then
    : "${DATABASE_URL:?DATABASE_URL is required (disposable local database)}"
    : "${REDIS_URL:?REDIS_URL is required (local redis)}"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

export RUNPOD_API_KEY="${RUNPOD_API_KEY:-local-dry-run-key}"
# Opt in to the harness-only journeys (tests/release/conftest.py deselects them elsewhere).
export PITWALL_JOURNEY_HARNESS=1
export PITWALL_ADMIN_SECRET="${PITWALL_ADMIN_SECRET:-journey-admin-secret}"
# The auth journeys set their own token per process. An inherited token, such as the
# README quick-start export, would turn every other journey's call into a 401.
unset PITWALL_API_TOKEN PITWALL_API_SCOPED_TOKENS

API_PORT=18080
WEBHOOK_PORT=18082
AUTH_API_PORT=18083
RATE_API_PORT=18084
BUDGET_API_PORT=18085
EXPORTER_PORT=18090

# Fail fast if any journey port is already bound — otherwise the suite would
# silently exercise a stale server from a previous (crashed) run.
if ! in_process "${JOURNEY_FILTER}"; then
    for port in ${API_PORT} ${WEBHOOK_PORT} ${AUTH_API_PORT} ${RATE_API_PORT} ${BUDGET_API_PORT} ${EXPORTER_PORT}; do
        if ss -tln 2>/dev/null | grep -q ":${port} "; then
            printf '[journeys] FATAL: port %s already in use — kill the stale process first\n' "${port}" >&2
            exit 2
        fi
    done
fi

ARTIFACTS="$(mktemp -d "${TMPDIR:-/tmp}/pitwall-journeys.XXXXXX")"
PIDS=()
FAILED=0
PASSED=0
STEP=0
declare -a RESULTS=()

cleanup() {
    for pid in "${PIDS[@]:-}"; do
        [ -n "${pid}" ] && kill "${pid}" >/dev/null 2>&1
    done
    wait >/dev/null 2>&1
}
trap cleanup EXIT

log()  { printf '[journeys] %s\n' "$*" >&2; }

journey_pass() { PASSED=$((PASSED+1)); RESULTS+=("PASS  $1"); log "PASS  $1"; }
journey_fail() { FAILED=$((FAILED+1)); RESULTS+=("FAIL  $1 — $2"); log "FAIL  $1 — $2"; }

# check <journey-id> <description> <command...>  — records first failure per call
check() {
    local id="$1" desc="$2"; shift 2
    STEP=$((STEP+1))
    local output="${ARTIFACTS}/${id}-${STEP}.out"
    if "$@" >"${output}" 2>&1; then
        printf '%s\tPASS\t%s\t%s\n' "${id}" "${desc}" "${output}" >>"${ARTIFACTS}/steps.tsv"
        return 0
    fi
    printf '%s\tFAIL\t%s\t%s\n' "${id}" "${desc}" "${output}" >>"${ARTIFACTS}/steps.tsv"
    journey_fail "${id}" "${desc} (see below)"
    sed 's/^/    /' "${output}" | tail -15 >&2
    return 1
}

# expect_fail <journey-id> <description> <command...> — inverted check
expect_fail() {
    local id="$1" desc="$2"; shift 2
    STEP=$((STEP+1))
    local output="${ARTIFACTS}/${id}-${STEP}.out"
    if "$@" >"${output}" 2>&1; then
        printf '%s\tFAIL\t%s\t%s\n' "${id}" "${desc}" "${output}" >>"${ARTIFACTS}/steps.tsv"
        journey_fail "${id}" "${desc}: expected non-zero exit, got success"
        return 1
    fi
    printf '%s\tPASS\t%s\t%s\n' "${id}" "${desc}" "${output}" >>"${ARTIFACTS}/steps.tsv"
    return 0
}

http_code() { curl -s -o "${ARTIFACTS}/body.json" -w '%{http_code}' "$@"; }

# json_assert <file> <python-expression over parsed `d`>
json_assert() {
    JOURNEY_JSON="$1" JOURNEY_EXPR="$2" uv run python -c "
import json, os
d = json.load(open(os.environ['JOURNEY_JSON']))
expr = os.environ['JOURNEY_EXPR']
assert eval(expr), 'assertion failed: ' + expr + ' on ' + json.dumps(d)[:400]
"
}

db_query() { # db_query <sql> — prints rows as python tuples
    JOURNEY_SQL="$1" uv run python -c "
import asyncio, asyncpg, os
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    try:
        for row in await conn.fetch(os.environ['JOURNEY_SQL']):
            print(tuple(row))
    finally:
        await conn.close()
asyncio.run(main())
"
}

db_expect() { db_query "$1" | grep -q "$2"; }

wait_for_url() { # wait_for_url <url> <seconds>
    local url="$1" deadline=$(( $(date +%s) + $2 ))
    while [ "$(date +%s)" -lt "${deadline}" ]; do
        if curl -sf "${url}" >/dev/null 2>&1; then return 0; fi
        sleep 0.5
    done
    return 1
}

# start_bg <logfile> <command...> — records the PID in the parent shell's PIDS
# array so the EXIT trap can kill it. Never call this inside $(...) — command
# substitution forks a subshell and the PID registration would be lost,
# leaking the server past cleanup.
start_bg() {
    local logfile="$1"; shift
    "$@" >"${ARTIFACTS}/${logfile}" 2>&1 &
    PIDS+=($!)
}

fresh_db() {
    uv run pitwall db reset --force >/dev/null 2>&1
    uv run pitwall db migrate >/dev/null 2>&1
}

log "artifacts: ${ARTIFACTS}"

# ---------------------------------------------------------------- J01 quickstart
j01() {
    local id=J01 ok=1
    fresh_db
    check ${id} "pitwall init --non-interactive" \
        uv run pitwall init --non-interactive || ok=0
    PITWALL_API_PORT=${API_PORT} start_bg api.log uv run pitwall-api
    check ${id} "API becomes healthy" \
        wait_for_url "http://127.0.0.1:${API_PORT}/healthz" 30 || ok=0
    local code
    code=$(http_code -X POST "http://127.0.0.1:${API_PORT}/v1/inference" \
        -H 'Content-Type: application/json' \
        -d '{"capability":"embedding.demo","texts":["hello"],"dry_run":true}')
    if [ "${code}" != "200" ]; then
        journey_fail ${id} "dry-run inference HTTP ${code}: $(head -c 300 "${ARTIFACTS}/body.json")"; ok=0
    else
        check ${id} "dry-run response fields" json_assert "${ARTIFACTS}/body.json" \
            "d['result']['dry_run'] is True and d['result']['plan']['selected_provider_id'] == 'prov_demo_runpod_lb'" || ok=0
    fi
    [ ${ok} -eq 1 ] && journey_pass "${id} README quick start"
}

# ------------------------------------------------- J02 init variants
j02() {
    local id=J02 ok=1
    fresh_db
    check ${id} "init --from-seed" \
        uv run pitwall init --non-interactive --from-seed seed || ok=0
    check ${id} "init manual flags" \
        uv run pitwall init --non-interactive --manual \
            --capability-name embedding.manual --capability-class embedding \
            --cost-mode per_second --provider-name manual-lb \
            --endpoint-id eptest00000009 --provider-type serverless_lb \
            --region US-EXAMPLE-1 --gpu-class "NVIDIA L4" --per-second-active 0.001 || ok=0
    check ${id} "manual capability row exists" \
        db_expect "SELECT name FROM pitwall.capabilities WHERE name='embedding.manual'" embedding.manual || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} init variants"
}

# ------------------------------------------------- J03 seed command
j03() {
    local id=J03 ok=1
    fresh_db
    check ${id} "seed with --mark-healthy" \
        uv run pitwall seed seed/capabilities.yaml seed/providers.yaml --mark-healthy || ok=0
    check ${id} "seeded provider is healthy" \
        db_expect "SELECT health_status FROM pitwall.providers WHERE id='prov_demo_runpod_lb'" healthy || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} seed command"
}

# ------------------------------------------------- J04 manual onboarding
j04() {
    local id=J04 ok=1
    fresh_db
    check ${id} "create-capability" \
        uv run pitwall create-capability --name embedding.manual2 --class embedding --cost-mode per_second || ok=0
    local capid
    capid=$(db_query "SELECT id FROM pitwall.capabilities WHERE name='embedding.manual2'" | tr -d "(',)")
    if [ -z "${capid}" ]; then journey_fail ${id} "capability id not found"; ok=0; fi
    check ${id} "register-endpoint" \
        uv run pitwall register-endpoint --endpoint-id eptest00000008 \
            --provider-type serverless_lb --capability-id "${capid}" \
            --name manual-endpoint --gpu-class "NVIDIA L4" --region US-EXAMPLE-1 \
            --cost-mode per_second --per-second-active 0.001 || ok=0
    local provid
    provid=$(db_query "SELECT id FROM pitwall.providers WHERE name='manual-endpoint'" | tr -d "(',)")
    check ${id} "set-provider-health healthy" \
        uv run pitwall set-provider-health "${provid}" healthy || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} manual onboarding"
}

# ------------------------------------------------- J05 config check
j05() {
    local id=J05 ok=1
    check ${id} "config check with full env" uv run pitwall config check || ok=0
    expect_fail ${id} "config check fails closed without DATABASE_URL" \
        env -u DATABASE_URL uv run pitwall config check || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} config check"
}

# ------------------------------------------------- J06 db lifecycle
j06() {
    local id=J06 ok=1
    check ${id} "db status" uv run pitwall db status || ok=0
    check ${id} "db migrate idempotent" uv run pitwall db migrate || ok=0
    expect_fail ${id} "reset refused without --force" uv run pitwall db reset || ok=0
    expect_fail ${id} "reset refused for remote host even with --force" \
        env DATABASE_URL='postgresql://u:p@db.example.com:5432/prod' uv run pitwall db reset --force || ok=0
    check ${id} "reset allowed locally with --force" uv run pitwall db reset --force || ok=0
    check ${id} "re-migrate after reset" uv run pitwall db migrate || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} db lifecycle guardrails"
}

# ------------------------------------------------- J07 API discovery (uses J01 API)
j07() {
    local id=J07 ok=1 base="http://127.0.0.1:${API_PORT}"
    fresh_db
    uv run pitwall init --non-interactive >/dev/null 2>&1
    for path in /healthz /health /v1/health /v1/capabilities /v1/providers /openapi.json /docs; do
        local code; code=$(http_code "${base}${path}")
        if [ "${code}" != "200" ]; then journey_fail ${id} "GET ${path} -> ${code}"; ok=0; fi
    done
    local code
    code=$(http_code "${base}/v1/capabilities/embedding.demo")
    [ "${code}" = "200" ] || { journey_fail ${id} "capability by name -> ${code}"; ok=0; }
    local provid="prov_demo_runpod_lb"
    code=$(http_code "${base}/v1/providers/${provid}")
    [ "${code}" = "200" ] || { journey_fail ${id} "provider by id -> ${code}"; ok=0; }
    code=$(http_code "${base}/v1/providers/${provid}/health")
    [ "${code}" = "200" ] || { journey_fail ${id} "provider health -> ${code}"; ok=0; }
    [ ${ok} -eq 1 ] && journey_pass "${id} API discovery"
}

# ------------------------------------------------- J08 inference error shapes
j08() {
    local id=J08 ok=1 base="http://127.0.0.1:${API_PORT}"
    local code
    code=$(http_code -X POST "${base}/v1/inference" -H 'Content-Type: application/json' \
        -d '{"capability":"does.not.exist","texts":["x"],"dry_run":true}')
    [ "${code}" = "404" ] || { journey_fail ${id} "unknown capability -> ${code} (want 404)"; ok=0; }
    code=$(http_code -X POST "${base}/v1/inference" -H 'Content-Type: application/json' -d '{"nope":1}')
    [ "${code}" = "422" ] || { journey_fail ${id} "malformed body -> ${code} (want 422)"; ok=0; }
    [ ${ok} -eq 1 ] && journey_pass "${id} inference error shapes"
}

# ------------------------------------------------- J09 TUI boot
j09() {
    local id=J09 ok=1
    local boot_status=0
    script -qec "timeout --foreground 6 uv run pitwall dashboard" "${ARTIFACTS}/tui.typescript" \
        >/dev/null 2>&1 || boot_status=$?
    # timeout's 124 proves the dashboard survived the entire boot window.
    # An early clean exit or a non-Python startup failure is not a boot pass.
    if [ "${boot_status}" -ne 124 ]; then
        journey_fail ${id} "TUI exited before boot window (status ${boot_status}; expected 124)"
        ok=0
    fi
    if [ ! -s "${ARTIFACTS}/tui.typescript" ]; then
        journey_fail ${id} "TUI boot produced no terminal transcript"
        ok=0
    elif grep -q "Traceback" "${ARTIFACTS}/tui.typescript"; then
        journey_fail ${id} "TUI crashed: $(grep -m1 -A2 Traceback "${ARTIFACTS}/tui.typescript" | tr '\n' ' ' | head -c 200)"
        ok=0
    fi
    [ ${ok} -eq 1 ] && journey_pass "${id} TUI dashboard boots"
}

# ------------------------------------------------- J10 MCP stdio
j10() {
    local id=J10
    if check ${id} "MCP stdio session" uv run python - <<'PY'
import asyncio, json, sys

async def main():
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    import os
    params = StdioServerParameters(command="uv", args=["run", "pitwall", "mcp", "serve", "broker"], env=dict(os.environ))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        info = await s.initialize()
        assert info.server_info.name == "pitwall", info.server_info
        tools = (await s.list_tools()).tools
        assert len(tools) >= 20, f"only {len(tools)} tools"
        caps = await s.call_tool("pitwall_list_capabilities", {})
        assert not caps.is_error, caps
        caps_text = "".join(c.text for c in caps.content if hasattr(c, "text"))
        assert "embedding.demo" in caps_text, caps_text[:300]
        res = await s.call_tool(
            "pitwall_submit_inference",
            {"capability_id": "cap_embedding_demo", "dry_run": True, "payload": {"texts": ["hello"]}},
        )
        assert not res.is_error, res
        text = "".join(c.text for c in res.content if hasattr(c, "text"))
        assert "dry_run" in text, text[:300]

asyncio.run(asyncio.wait_for(main(), timeout=60))
print("mcp stdio journey ok")
PY
    then journey_pass "${id} MCP stdio"; fi
}

# ------------------------------------------------- J11 network MCP rejected
j11() {
    local id=J11
    if PITWALL_MCP_TRANSPORT=sse uv run pitwall mcp serve broker >"${ARTIFACTS}/mcp-network-rejected.log" 2>&1; then
        journey_fail "${id}" "unauthenticated MCP network transport unexpectedly started"
        return
    fi
    if grep -Eq "network MCP transports are unavailable|Input should be 'stdio'" \
        "${ARTIFACTS}/mcp-network-rejected.log"; then
        journey_pass "${id} network MCP fails closed"
    else
        journey_fail "${id}" "network refusal was not explicit"
    fi
}

# ------------------------------------------------- J12 admin API
j12() {
    local id=J12 ok=1 base="http://127.0.0.1:${API_PORT}"
    local code
    code=$(http_code -X POST "${base}/v1/admin/capabilities" -H 'Content-Type: application/json' -d '{}')
    case "${code}" in 401|403) : ;; *) journey_fail ${id} "no secret -> ${code} (want 401/403)"; ok=0 ;; esac
    code=$(http_code -X POST "${base}/v1/admin/capabilities" \
        -H "X-Pitwall-Secret: wrong-secret" -H 'Content-Type: application/json' -d '{}')
    case "${code}" in 401|403) : ;; *) journey_fail ${id} "wrong secret -> ${code} (want 401/403)"; ok=0 ;; esac
    local body='{"name":"embedding.adminj","version":"1.0.0","class":"embedding","cost_mode":"per_request"}'
    code=$(http_code -X POST "${base}/v1/admin/capabilities" \
        -H "X-Pitwall-Secret: ${PITWALL_ADMIN_SECRET}" -H 'Content-Type: application/json' -d "${body}")
    [ "${code}" = "201" ] || { journey_fail ${id} "create capability -> ${code}: $(head -c 200 "${ARTIFACTS}/body.json")"; ok=0; }
    local capid
    capid=$(uv run python -c "import json;print(json.load(open('${ARTIFACTS}/body.json')).get('id',''))" 2>/dev/null)
    if [ -n "${capid}" ]; then
        code=$(http_code -X POST "${base}/v1/admin/capabilities/${capid}/disable" \
            -H "X-Pitwall-Secret: ${PITWALL_ADMIN_SECRET}")
        [ "${code}" = "200" ] || { journey_fail ${id} "disable -> ${code}"; ok=0; }
        code=$(http_code -X POST "${base}/v1/admin/capabilities/${capid}/enable" \
            -H "X-Pitwall-Secret: ${PITWALL_ADMIN_SECRET}")
        [ "${code}" = "200" ] || { journey_fail ${id} "enable -> ${code}"; ok=0; }
    fi
    code=$(http_code -X POST "${base}/v1/admin/audit-capability/embedding.demo" \
        -H "X-Pitwall-Secret: ${PITWALL_ADMIN_SECRET}")
    [ "${code}" = "200" ] || { journey_fail ${id} "audit-capability -> ${code}"; ok=0; }
    [ ${ok} -eq 1 ] && journey_pass "${id} admin API"
}

# ------------------------------------------------- J13 whole-API auth
j13() {
    local id=J13 ok=1
    PITWALL_API_PORT=${AUTH_API_PORT} PITWALL_API_TOKEN=journey-token \
        start_bg api-auth.log uv run pitwall-api
    check ${id} "auth API healthy (health is public)" \
        wait_for_url "http://127.0.0.1:${AUTH_API_PORT}/healthz" 30 || ok=0
    local code
    code=$(http_code "http://127.0.0.1:${AUTH_API_PORT}/v1/capabilities")
    [ "${code}" = "401" ] || { journey_fail ${id} "no bearer -> ${code} (want 401)"; ok=0; }
    code=$(http_code -H "Authorization: Bearer journey-token" "http://127.0.0.1:${AUTH_API_PORT}/v1/capabilities")
    [ "${code}" = "200" ] || { journey_fail ${id} "with bearer -> ${code} (want 200)"; ok=0; }
    [ ${ok} -eq 1 ] && journey_pass "${id} whole-API token auth"
}

# ------------------------------------------------- J14 rate limit
j14() {
    local id=J14 ok=1
    PITWALL_API_PORT=${RATE_API_PORT} PITWALL_INBOUND_RATE_LIMIT=3/60s \
        start_bg api-rate.log uv run pitwall-api
    check ${id} "rate-limited API healthy" \
        wait_for_url "http://127.0.0.1:${RATE_API_PORT}/healthz" 30 || ok=0
    local got429=0
    for _ in 1 2 3 4 5 6; do
        local code; code=$(http_code "http://127.0.0.1:${RATE_API_PORT}/v1/capabilities")
        if [ "${code}" = "429" ]; then
            got429=1
            grep -qi 'retry-after' <(curl -s -D- -o /dev/null "http://127.0.0.1:${RATE_API_PORT}/v1/capabilities") \
                || { journey_fail ${id} "429 without Retry-After"; ok=0; }
            break
        fi
    done
    [ ${got429} -eq 1 ] || { journey_fail ${id} "never saw 429 in a 6-request burst at 3/60s"; ok=0; }
    [ ${ok} -eq 1 ] && journey_pass "${id} inbound rate limit"
}

# ------------------------------------------------- J15 budget gate 402
j15() {
    local id=J15 ok=1
    PITWALL_API_PORT=${BUDGET_API_PORT} PITWALL_MONTHLY_BUDGET_USD=0.000001 \
        start_bg api-budget.log uv run pitwall-api
    check ${id} "budget API healthy" \
        wait_for_url "http://127.0.0.1:${BUDGET_API_PORT}/healthz" 30 || ok=0
    local code
    code=$(http_code -X POST "http://127.0.0.1:${BUDGET_API_PORT}/v1/inference" \
        -H 'Content-Type: application/json' \
        -d '{"capability":"embedding.demo","texts":["hello"]}')
    [ "${code}" = "402" ] || { journey_fail ${id} "exhausted budget -> ${code} (want 402): $(head -c 200 "${ARTIFACTS}/body.json")"; ok=0; }
    [ ${ok} -eq 1 ] && journey_pass "${id} budget gate rejects pre-spend"
}

# ------------------------------------------------- J16 openai proxy safety
j16() {
    local id=J16 ok=1
    local code
    code=$(http_code -X POST \
        "http://127.0.0.1:${API_PORT}/v1/openai/embedding.demo/v1/https://169.254.169.254/latest/meta-data" \
        -H 'Content-Type: application/json' -d '{}')
    case "${code}" in 4*) : ;; *) journey_fail ${id} "SSRF path -> ${code} (want 4xx)"; ok=0 ;; esac
    code=$(http_code -X POST \
        "http://127.0.0.1:${BUDGET_API_PORT}/v1/openai/embedding.demo/v1/chat/completions" \
        -H 'Content-Type: application/json' \
        -d '{"model":"m","messages":[{"role":"user","content":"hi"}]}')
    [ "${code}" = "402" ] || { journey_fail ${id} "proxy with exhausted budget -> ${code} (want 402)"; ok=0; }
    [ ${ok} -eq 1 ] && journey_pass "${id} OpenAI proxy safety"
}

# ------------------------------------------------- J17 job error shapes
j17() {
    local id=J17 ok=1 base="http://127.0.0.1:${API_PORT}"
    for pathspec in "GET /v1/jobs/wkl_doesnotexist/status" "GET /v1/jobs/wkl_doesnotexist/result" "POST /v1/jobs/wkl_doesnotexist/cancel"; do
        local method="${pathspec%% *}" path="${pathspec#* }"
        local code; code=$(http_code -X "${method}" "${base}${path}")
        [ "${code}" = "404" ] || { journey_fail ${id} "${method} ${path} -> ${code} (want 404)"; ok=0; }
    done
    [ ${ok} -eq 1 ] && journey_pass "${id} job error shapes"
}

# ------------------------------------------------- J18 webhook receiver
j18() {
    local id=J18 ok=1
    PITWALL_WEBHOOK_RECEIVER_PORT=${WEBHOOK_PORT} PITWALL_WEBHOOK_SECRET=journey-webhook-secret \
        start_bg webhook.log uv run pitwall-webhook
    check ${id} "webhook receiver healthy" \
        wait_for_url "http://127.0.0.1:${WEBHOOK_PORT}/healthz" 30 || ok=0
    local payload='{"id":"job-journey-1","status":"COMPLETED"}'
    local code
    code=$(http_code -X POST "http://127.0.0.1:${WEBHOOK_PORT}/webhooks/runpod" \
        -H 'Content-Type: application/json' -d "${payload}")
    [ "${code}" = "401" ] || { journey_fail ${id} "unsigned -> ${code} (want 401)"; ok=0; }
    local sig
    sig=$(uv run python -c "
from pitwall.webhook_dispatcher.signer import sign
print(sign(b'${payload}', 'journey-webhook-secret'))
")
    code=$(http_code -X POST "http://127.0.0.1:${WEBHOOK_PORT}/webhooks/runpod" \
        -H 'Content-Type: application/json' -H "X-Pitwall-Webhook-Signature: ${sig}" -d "${payload}")
    [ "${code}" = "200" ] || { journey_fail ${id} "signed -> ${code} (want 200): $(head -c 200 "${ARTIFACTS}/body.json")"; ok=0; }
    code=$(http_code -X POST "http://127.0.0.1:${WEBHOOK_PORT}/webhooks/runpod" \
        -H 'Content-Type: application/json' -H "X-Pitwall-Webhook-Signature: ${sig}" -d "${payload}")
    if [ "${code}" = "200" ]; then
        check ${id} "replay flagged duplicate" json_assert "${ARTIFACTS}/body.json" \
            "d.get('duplicate') is True" || ok=0
    else
        journey_fail ${id} "replay -> ${code} (want 200)"; ok=0
    fi
    [ ${ok} -eq 1 ] && journey_pass "${id} webhook receiver"
}

# ------------------------------------------------- J19 reconciler
j19() {
    local id=J19 ok=1
    check ${id} "reconciler check (valid REDIS_URL)" \
        uv run python -m pitwall.reconciler check || ok=0
    expect_fail ${id} "reconciler check fails on invalid REDIS_URL" \
        env REDIS_URL='not-a-dsn' uv run python -m pitwall.reconciler check || ok=0
    timeout 8 uv run pitwall-reconciler >"${ARTIFACTS}/reconciler.log" 2>&1
    local rc=$?
    if [ ${rc} -ne 124 ]; then
        journey_fail ${id} "worker exited early rc=${rc}: $(tail -3 "${ARTIFACTS}/reconciler.log" | tr '\n' ' ' | head -c 250)"
        ok=0
    fi
    if grep -q "Traceback" "${ARTIFACTS}/reconciler.log"; then
        journey_fail ${id} "worker boot traceback: $(grep -m1 -A2 Traceback "${ARTIFACTS}/reconciler.log" | tr '\n' ' ' | head -c 250)"
        ok=0
    fi
    [ ${ok} -eq 1 ] && journey_pass "${id} reconciler check + worker boot"
}

# ------------------------------------------------- J20 cost exporter
j20() {
    local id=J20 ok=1
    PITWALL_COST_EXPORTER_PORT=${EXPORTER_PORT} PITWALL_MONTHLY_BUDGET_USD=${PITWALL_MONTHLY_BUDGET_USD:-1000} \
        start_bg exporter.log uv run pitwall-cost-exporter
    check ${id} "exporter healthy" \
        wait_for_url "http://127.0.0.1:${EXPORTER_PORT}/metrics" 30 || ok=0
    if ! curl -s "http://127.0.0.1:${EXPORTER_PORT}/metrics" | grep -q "^pitwall_"; then
        journey_fail ${id} "no pitwall_ metrics in /metrics"; ok=0
    fi
    [ ${ok} -eq 1 ] && journey_pass "${id} cost exporter"
}

# ------------------------------------------------- J21 kill switch (dev, no pods)
j21() {
    local id=J21 ok=1 base="http://127.0.0.1:${API_PORT}"
    local code
    code=$(http_code -X POST "${base}/v1/admin/kill-switch" -H 'Content-Type: application/json' \
        -d '{"reason":"journey drill"}')
    case "${code}" in 401|403) : ;; *) journey_fail ${id} "unauthenticated kill -> ${code}"; ok=0 ;; esac
    code=$(http_code -X POST "${base}/v1/admin/kill-switch" \
        -H "X-Pitwall-Secret: ${PITWALL_ADMIN_SECRET}" -H 'Content-Type: application/json' \
        -d '{"reason":"journey drill","terminate_compute":false}')
    [ "${code}" = "200" ] || { journey_fail ${id} "kill drill -> ${code}: $(head -c 250 "${ARTIFACTS}/body.json")"; ok=0; }
    check ${id} "API healthy after drill" \
        wait_for_url "${base}/healthz" 10 || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} kill switch drill"
}

# ------------------------------------------------- J22 warm-volume guardrails
j22() {
    local id=J22 ok=1
    local model=ornith-ai/Ornith-1.5-35B-A3B-GGUF
    check ${id} "warm-volume --dry-run (no spend)" \
        uv run pitwall warm-volume --model "${model}" --volume-id vol-journey --dry-run || ok=0
    expect_fail ${id} "warm-volume with fake key fails cleanly" \
        timeout 120 uv run pitwall warm-volume --model "${model}" --volume-id vol-journey || ok=0
    local leaked
    leaked=$(db_query "SELECT count(*) FROM pitwall.leases WHERE state NOT IN ('stopped','failed','expired')" | tr -d "(,)")
    if [ "${leaked}" != "0" ]; then
        journey_fail ${id} "warm-volume left ${leaked} non-terminal lease(s)"; ok=0
    fi
    [ ${ok} -eq 1 ] && journey_pass "${id} warm-volume guardrails"
}

# ------------------------------------------------- J23 hermetic serve lifecycle
j23() {
    local id=J23 ok=1
    check ${id} "seeded serve dry-run, fake pod, proxy, and teardown" \
        env DATABASE_URL='' REDIS_URL='' uv run --frozen pytest -q -m release \
        tests/release/test_serve_journey.py::test_j23_seeded_serve_dry_run_launch_proxy_and_teardown \
        -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} hermetic serve lifecycle"
}

# ------------------------------------------------- J24 hermetic serve automation
j24() {
    local id=J24 ok=1
    check ${id} "idle stop, activity renewal, and auto-serve revival" \
        env DATABASE_URL='' REDIS_URL='' uv run --frozen pytest -q -m release \
        tests/release/test_serve_automation_journey.py::test_j24_idle_stop_and_auto_serve_revival \
        -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} hermetic serve automation"
}

# ------------------------------------------------- J25 hermetic self-hosted lifecycle
j25() {
    local id=J25 ok=1
    check ${id} "self-hosted probe, warm, proxy, eviction, and re-warm" \
        env DATABASE_URL='' REDIS_URL='' uv run --frozen pytest -q -m release \
        tests/release/test_selfhosted_journey.py::test_j25_probe_warm_chat_evict_and_rewarm \
        -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} hermetic self-hosted lifecycle"
}

# ------------------------------------------------- J26 canonical deployment + env docs
j26() {
    local id=J26 ok=1
    check ${id} "canonical compose validates" \
        env \
            POSTGRES_PASSWORD=journey-postgres-password \
            REDIS_PASSWORD=journey-redis-password \
            RUNPOD_API_KEY=journey-runpod-key \
            PITWALL_API_TOKEN=journey-api-token-0001 \
            PITWALL_ADMIN_SECRET=journey-admin-secret-0001 \
            PITWALL_WEBHOOK_SECRET=journey-webhook-secret-0001 \
            PITWALL_WEBHOOK_ENCRYPTION_KEYS='{"v1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}' \
            PITWALL_ARCHIVE_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
            docker compose -f docker-compose.yml config -q || ok=0
    for var in DATABASE_URL REDIS_URL RUNPOD_API_KEY PITWALL_ADMIN_SECRET PITWALL_WEBHOOK_SECRET PITWALL_MONTHLY_BUDGET_USD; do
        grep -q "^${var}=" .env.example || { journey_fail ${id} ".env.example missing ${var}"; ok=0; }
    done
    [ ${ok} -eq 1 ] && journey_pass "${id} canonical deployment + env docs"
}

# ------------------------------------------------- J27 README testing commands
j27() {
    local id=J27 ok=1
    if [ "${PITWALL_JOURNEYS_UNIT_LANE:-}" = covered ]; then
        echo "README unit lane: covered by the test job"
    else
        check ${id} "README unit lane" \
            uv run pytest -q -n auto -m "not integration and not slow" || ok=0
    fi
    check ${id} "README security lane" \
        uv run pytest -q -m "security and not fuzz" tests/security || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} README testing commands"
}

# ---------------------------------------------------------------- run
# ------------------------------------------------- J28 personal serving (J28-J31, no database)
j28() {
    local id=J28 ok=1
    check ${id} "personal setup, refusals, lifecycle, and reconciliation (J28-J31)" \
        env DATABASE_URL='' REDIS_URL='' uv run --frozen pytest -q -m release \
        tests/release/test_personal_journeys.py -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} personal serving (J28-J31)"
}

# ------------------------------------------------- J32 supervised free-tier gateway (Python, no database)
j32() {
    local id=J32 ok=1
    check ${id} "gateway routes providers to their own upstreams" \
        env DATABASE_URL='' REDIS_URL='' uv run --frozen pytest -q -m release \
        tests/release/test_gateway_journey.py -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} supervised free-tier gateway"
}

# ------------------------------------------------- J33 Claude-native /v1/messages tool round trip
j33() {
    local id=J33 ok=1
    check ${id} "tool_use and tool_result round trip through /v1/messages" \
        env DATABASE_URL='' REDIS_URL='' uv run --frozen pytest -q -m release \
        tests/release/test_messages_journey.py -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} Claude-native messages tool round trip"
}

# ------------------------------------------------- J34 every MCP tool over stdio (database)
j34() {
    local id=J34 ok=1
    check ${id} "all 81 MCP tools: valid and invalid input over real stdio" \
        uv run --frozen pytest -q -m release tests/release/test_mcp_all_tools_journey.py \
        -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} every MCP tool over stdio"
}

# ------------------------------------------------- J35 every REST operation and scope (database)
j35() {
    local id=J35 ok=1
    check ${id} "all REST operations: success path and scope boundary on a real server" \
        uv run --frozen pytest -q -m release tests/release/test_rest_all_operations_journey.py \
        -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} every REST operation and its scope"
}

# ------------------------------------------------- J36 every CLI command and argument (database)
j36() {
    local id=J36 ok=1
    check ${id} "all CLI command paths and 485 argument surfaces through the entry points" \
        uv run --frozen pytest -q -m release tests/release/test_cli_all_commands_journey.py \
        -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} every CLI command and argument"
}

# ------------------------------------------------- J37 every configuration key (in-process)
j37() {
    local id=J37 ok=1
    check ${id} "all 208 configuration surfaces: env, TOML, consumer, and config check" \
        env DATABASE_URL='' REDIS_URL='' uv run --frozen pytest -q -m release \
        tests/release/test_config_keys_journey.py -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} every configuration key"
}

# ------------------------------------------------- J38 every console view and binding (in-process)
j38() {
    local id=J38 ok=1
    check ${id} "ten console views x three source states x two sizes, plus the binding sweep" \
        env DATABASE_URL='' REDIS_URL='' uv run --frozen pytest -q -m release \
        tests/release/test_console_views_journey.py -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} every console view and binding"
}

# ------------------------------------------------- J42 backup and restore drill (database)
j42() {
    local id=J42 ok=1
    check ${id} "pg_dump/pg_restore drill into a fresh database; counts and checksums survive" \
        env PITWALL_TEST_DATABASE_URL="${DATABASE_URL}" PITWALL_TEST_REDIS_URL="${REDIS_URL}" \
        uv run --frozen pytest -q -m integration -p no:randomly \
        tests/integration/test_backup_restore_drill.py || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} backup and restore drill"
}

# ------------------------------------------------- J43 upgrade from the last release (database)
j43() {
    local id=J43 ok=1
    check ${id} "v0.1.0a2 database upgrades in place; later migrations apply once; rows intact" \
        env PITWALL_TEST_DATABASE_URL="${DATABASE_URL}" PITWALL_TEST_REDIS_URL="${REDIS_URL}" \
        uv run --frozen pytest -q -m integration -p no:randomly \
        tests/integration/test_upgrade_from_release.py || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} upgrade from the last release"
}

# ------------------------------------------------- J44 installed wheel and sdist (database)
j44() {
    local id=J44 ok=1 dist="${ARTIFACTS}/dist"
    check ${id} "uv build wheel and sdist" uv build --out-dir "${dist}" || ok=0
    [ ${ok} -eq 1 ] && { check ${id} "install each artifact outside the checkout and exercise it" \
        env PITWALL_TEST_DATABASE_URL="${DATABASE_URL}" PITWALL_TEST_REDIS_URL="${REDIS_URL}" \
        uv run --frozen python scripts/release/smoke_artifacts.py "${dist}" || ok=0; }
    [ ${ok} -eq 1 ] && journey_pass "${id} installed wheel and sdist"
}

# ------------------------------------------------- J39-J41 Agent Routing (in-process)
agent_routing_journey() {
    local id=$1 name=$2 summary=$3 ok=1
    check ${id} "${summary}" \
        bash -c 'HOME="$(mktemp -d)" .venv/bin/python -m unittest -q "tests.agents.test_journeys.$0"' \
        "${name}" || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} ${summary}"
}
j39() { agent_routing_journey J39 J39ProfilesJourney "profiles add, list, probe, doctor, replace, refuse, remove"; }
j40() { agent_routing_journey J40 J40ShimsJourney "all fourteen shims: argv, sentinel, receipt, failing exit"; }
j41() {
    local id=J41 ok=1
    check ${id} "scripted channel and workflow exits, a Pi workflow, and the mailbox verbs" \
        bash -c 'HOME="$(mktemp -d)" .venv/bin/python -m unittest -q tests.agents.test_journeys.J41ChannelAndWorkflowsJourney tests.agents.test_journeys.Tier1IntegrationTests tests.agents.test_journeys.SteerIntegrationTests tests.agents.test_journeys.WorkflowChannelEndToEndTests' \
        || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} channel, workflows, and mailbox verbs"
}

case "${JOURNEY_FILTER}" in
    J23) j23 ;;
    J24) j24 ;;
    J25) j25 ;;
    J27) j27 ;;
    J28) j28 ;;
    J32) j32 ;;
    J33) j33 ;;
    J37) j37 ;;
    J38) j38 ;;
    J39) j39 ;;
    J40) j40 ;;
    J41) j41 ;;
    *)
        j01; j02; j03; j04; j05; j06
        j07; j08; j09; j10; j11; j12; j13; j14; j15; j16; j17
        j18; j19; j20; j21; j22; j23; j24; j25; j26; j27
        j28; j32; j33; j34; j35; j36; j37; j38; j39; j40; j41
        j42; j43; j44
        ;;
esac

# ------------------------------------------------- matrix gate (full run only)
# Every discovered surface must be bound to a test that runs here and passes.
if [ -z "${JOURNEY_FILTER}" ]; then
    matrix_run="${ARTIFACTS}/matrix"
    if check MATRIX "run every test bound in reviewed-bindings.json" \
        uv run --frozen python scripts/release/run_bound_tests.py "${matrix_run}" \
        && check MATRIX "assemble the matrix and require every surface bound and passing" \
        env PITWALL_JOURNEY_RESULTS="${matrix_run}" uv run --frozen pytest -q -s -m release \
        -p no:randomly tests/release/test_matrix_complete.py; then
        journey_pass "MATRIX $(grep -ho 'matrix: .*' "${ARTIFACTS}"/MATRIX-*.out | tail -1)"
    fi
fi

log ""
log "==== journey summary ===="
for r in "${RESULTS[@]}"; do log "  ${r}"; done
log "${PASSED} passed, ${FAILED} failed"
[ ${FAILED} -eq 0 ] || exit 1
exit 0
