# RunPod live findings — Phase 0 and Phase 1 (zero credits spent)

Executed 2026-08-30 against the live RunPod API with an operator-supplied key. No credential or
account identifier is recorded here. Spend for everything below: **$0.00**.

## Verified correct

| Check | Result |
| --- | --- |
| Canonical GPU table (`runpod_client/gpu.py`) vs the live catalogue | 46 of 47 ids correct; **no** stale entries and **no** VRAM mismatches |
| GraphQL client (`runpod_client/graphql.py`) against production | 48 rows, no null `memoryInGb`, no null prices, `lowestPrice` present on every row |
| GraphQL prices vs REST v2 catalogue prices | identical for every shared id (community and secure) |
| Cloud-filtered price snapshot (`models/prices.py`) | 32 secure / 37 community rows, `source=live`; correctly drops the API's `unknown` sentinel row (0 GB, $0, both cloud flags false) |
| `models list` / `models fit` end to end on live prices | works; cheapest catalogue target is a 24 GB GGUF variant at $0.27/hr |
| Template identity (migration 0023 `config_sha`) | identical config reused one template; changing a single identity input produced a distinct one |

## Defects

### 1. `delete_template` cannot delete anything (high)

`deleteTemplate(id: ...)` is rejected: *Unknown argument "id" on field "Mutation.deleteTemplate"*.
Every template Pitwall creates leaks permanently — an earlier Pitwall run's template
(`pitwall-pod.nginx-prov_pod-…`) is still in the account. Cleanup during this session had to go
through a different client.

**Fix:** `DELETE /v2/templates/{id}` (REST v2), which returns 204.

### 2. `get_template` queries a field that does not exist (high)

*Cannot query field "template" on type "Query"* — template lookup fails for every id.

**Fix:** `GET /v2/templates/{id}`.

### 3. `ensure_template(ports=…)` is silently ignored (medium)

`_save_template_mutation` hard-codes `ports: ""`, so the argument is accepted and discarded: both
templates created during this test came back with `ports: []`, while templates created by other
tooling in the same account carry `["8000/http", "22/tcp"]`. The main serve path is masked because
`runpod_client/pods.py` sends ports again at pod-creation time, but any consumer that relies on the
template's own ports (a pod created from the template without explicit ports, or a serverless
endpoint bound to it) gets a container with nothing exposed.

**Fix:** send `ports` as the array the API expects. `BaseContainerConfig.ports` is
`array<string>` formatted `port/protocol`, e.g. `["8888/http", "22/tcp"]`.

### 4. `NVIDIA GeForce RTX 4080 SUPER` is missing from the canonical table (low)

16 GB, $0.28 community / $0.50 secure. A provider on that card cannot be registered and the
dossier validator rejects it in `recommended_gpu_classes`.

**Fix:** add the id and its VRAM to `GPU_VRAM_GB`.

## Recommended remedy for 1–3

Move template create/get/delete off the legacy GraphQL mutations onto REST v2, which the rest of
the pod path already uses: `POST /v2/templates`, `GET|PATCH|DELETE /v2/templates/{id}`, with
`image`, `env`, `args`, `disk` and `ports` from `BaseContainerConfig`. That fixes all three in one
change and removes the last GraphQL-only write path.

## Note for future runs

The account contains 33 templates from unrelated projects. Cleanup must delete only what a test
created — never the whole template list.

---

# Live compute findings (Phase 2 and Phase 5)

Spend for this section: six pods, all terminated — a 27-second CPU-class probe at $0.12/hr, a
crash-looping pod for ~22 minutes, a successful serve held ~8 minutes, a pod that could never start
for 15.4 minutes, a 25-second fast-abort, and the full end-to-end lifecycle run at ~7.5 minutes (the
last five on RTX 4090 at $0.74/hr) — **approximately $0.66**, against the plan's $1.00 cap. Measured
from pod uptime, not from the billing API, for the reason in defect 14.

## Verified correct

- `create_pod_with_fallback` allocated on its **first** choice (RTX A2000, $0.12/hr) in 25 s,
  applied `ports: ["80/http"]` at pod level, passed its readiness probe through
  `…-80.proxy.runpod.net`, and terminated cleanly (204). Pod-level ports work, which is what masks
  defect 3 above.
- The serve path built the launch correctly end to end: the pod carried our exact llama.cpp argv,
  `ports: ["8000/http"]`, and every `PITWALL_*` environment variable; the template was created and
  keyed by `config_sha`.
- `--plan-only` priced the run from the live snapshot ($0.0675 for a 15-minute TTL, `source=live`)
  and performed no writes.
- **The served endpoint honours the routing contract.** Once the dossier's filename was corrected
  (defect 8), the pod loaded and `/health` returned 200 within 30 s of the container starting.
  `GET /v1/models` returned exactly `["Ornith-1.5-35B-A3B"]` — string-identical to the
  `served_model_id` a routing consumer compares against, which is the contract the whole
  self-hosted path depends on. `POST /v1/chat/completions` answered correctly with well-formed
  usage accounting (`prompt_tokens`, `completion_tokens`, `finish_reason: stop`), and the model's
  reasoning content arrived in a separate `reasoning_content` field rather than polluting
  `content`. A first call capped at `max_tokens: 12` returned empty `content` with
  `finish_reason: length`: expected for a thinking model, but worth noting as a trap for any
  caller that sets a small token ceiling and treats empty content as a failure.

## Defects

### 5. `models fit` recommends GPUs that cannot be launched (high)

Fit ranked `NVIDIA RTX A5000` cheapest and reported `fits`. The launch then failed with
`launch_failed: all GPU types exhausted` because that card has no secure-cloud capacity — it is
absent from the live availability-filtered catalogue. Fit consults price and VRAM but never stock,
so its cheapest recommendation is regularly unbuyable.

**Fix:** fold the live availability signal (`availability`, `maxCount.{community,secure}`) into
fit, or at least mark rows with no current stock.

### 6. A 500 from pod create is read as "no capacity" (medium)

`POST /v1/pods` returned **500 Internal Server Error** and the client logged
`no capacity for gpu_types=… , trying next`. A genuine server fault is therefore indistinguishable
from exhausted stock, and the fallback chain burns through every GPU type on what may be a
transient outage.

**Fix:** treat 5xx as a retryable transport fault distinct from an out-of-capacity signal.

### 7. A crash-looping container burns the whole startup budget (high)

The launched pod could not load its model and exited repeatedly. Pitwall kept polling readiness for
the full `startup_timeout_s` (1800 s), paying $0.74/hr the entire time; roughly **$0.27** of the
spend above bought nothing. Nothing in the readiness wait notices that the container has restarted
several times or that its logs end in `exiting due to model loading error`.

**Fix:** abort the readiness wait when the container exits repeatedly — poll the pod's restart
count or log tail and fail fast with the container's own error, rather than waiting out the clock.

### 8. A dossier named a GGUF file that does not exist (high)

`ornith-ai/Ornith-1.5-35B-A3B-GGUF` declared `Ornith-1.5-35B-A3B-Q4_K_M.gguf`; the repository
actually publishes `Ornith-1.5-35B-Q4_K_M.gguf` (no `A3B` segment). Every launch of that variant
was guaranteed to fail after paying for the pod. The engine smoke could not catch it: it validates
argv against real parsers but never resolves the download.

Checked all six declared `repo`/`file` pairs in the catalogue against the HuggingFace API: this was
the only wrong one; the other five, including both `mmproj` companions, resolve. Fixed in this
commit.

**Fix (prevention):** an opt-in catalogue check that resolves every `repo`/`file` pair against the
HuggingFace API, run like the other env-gated live tests. It is free, takes seconds, and would have
caught this before any pod was created.

### 9. `serve-model` demands a rate it already knows (low)

`--plan-only` prints a live-sourced cost estimate, but launching without `--rate-per-second` fails
with `rate_required`. The price is already in hand from the same snapshot.

**Fix:** default the rate from the live price snapshot, keeping the flag as an override.

### 10. The pod client targets a deprecated API version (medium, deferred)

Nine call sites default to `https://rest.runpod.io/v1`, which RunPod documents as deprecated in
favour of `api.runpod.io/v2`.

**This is not a base-URL change.** Both OpenAPI documents were compared on 2026-08-30 and v2 is a
different API, not the same API on a different host:

| | v1 (what the client speaks) | v2 |
| --- | --- | --- |
| pod create | `imageName`, `cloudType`, `gpuTypeIds`, `containerDiskInGb` | `image`, `cloud`, `gpu: {id, count}`, `disk`, `mounts` — and `unevaluatedProperties: false`, so v1's field names are **rejected** |
| pod list | bare array | `{"pods": [...]}` envelope; our parser returns an empty list for it |
| pod lifecycle | `POST /pods/{id}/start`, `/stop`, `/reset` | one `POST /v2/pods/{id}/action` with `{"action": "start\|stop\|restart\|terminate"}` |
| template create | `imageName`, `containerDiskInGb`, `isServerless`, `containerRegistryAuthId`, `volumeInGb`, `volumeMountPath` | `image`, `disk`, `serverless`, `registry`, structured `mounts` |
| network volumes | `/networkvolumes` | present, **renamed** `/v2/network-volumes` |
| serverless endpoints | `/endpoints` | present, **renamed** `/v2/serverless` |

v2 publishes 34 paths, v1 publishes 23.

**Correction (2026-08-30).** An earlier revision of this entry claimed network volumes and
serverless endpoints were absent from v2. That was wrong: it came from searching the v2 path list
for the v1 spellings. Both exist under new names — `/v2/network-volumes` and `/v2/serverless`. The
blocker below is the pod payload, not missing paths.

Changing the default host alone would break pod creation, listing and lifecycle silently, and 404
every volume and serverless call under its v1 spelling.

**The real blocker is that four v1 pod fields have no v2 equivalent at all**, checked by walking
`CreatePodRequest` and `CreateGpuConfig` (both `unevaluatedProperties: false`):

- `gpuTypePriority` and `dataCenterPriority` — the placement-ordering policy.
- `minVCPUPerGPU` and `minRAMPerGPU` — the per-GPU resource floors.
- `gpuTypeIds` is a **list** in v1; v2's `gpu.id` is a single string, so the multi-GPU fallback
  becomes N calls rather than one.
- `dockerStartCmd` is an **array** in v1; v2 offers only `args`, a **string**. The serve path passes
  llama.cpp argv as an array, so a migration must flatten and requote it — a quoting hazard on
  precisely the command lines this system exists to launch.

Those are capability losses, not renames, and no amount of field mapping recovers them.

**Deferred deliberately.** v1 is deprecated but functional: the entire live campaign ran on it.
Nothing here is urgent, and the cost of a half-done migration is far higher than staying on a
deprecated-but-working surface.

**Worth noting for finding 15:** v2's `CreateGpuConfig` carries both `allowedCudaVersions` (exact
set) and `minCudaVersion` (an open-ended floor, compared numerically so 12.11 sorts above 12.2).
The floor is the better fit for a dossier's CUDA requirement, and it does not exist on v1.

### 11. Every lease audit write violates its own table constraints (critical)

`serve-model` created and paid for a pod, then failed:

```
serve_failed: new row for relation "config_audit" violates check constraint
"config_audit_action_check"
```

The lease lifecycle writes audit rows through `insert_audit`, and **none** of its values were
admitted by `pitwall.config_audit`:

| Call site | actor | action |
| --- | --- | --- |
| `api/leases/launch.py:572` (arm) | `system:lease` | `lease_ready` |
| `api/leases/teardown.py:230` (disarm) | `system:lease` | `lease_closed` |
| `api/leases/teardown.py:141` (stop) | `system:lease-controller` / `system:lease` | `stop` |

`config_audit_action_check` allowed 13 actions, none of them these three;
`config_audit_actor_check` allowed 7 actors, neither of these two. So the serve path could never
have recorded a single lease event against a real database.

Every gate was green because these paths are exercised only through fakes, which accept any string.
`tests/leases/test_launch.py:175` asserts `audits[0]["action"] == "lease_ready"` — the test
enshrines the exact value Postgres rejects. The DB constraint is the real contract and no test
consulted it.

**Fixed in this commit:** migration `0027_config_audit_lease_actions.sql` admits the three actions
and two actors, verified by inserting all four real (actor, action) pairs against the live schema.
`tests/db/test_config_audit_action_contract.py` is the durable gate: it parses the allow-lists out
of the newest defining migration and AST-scans every `insert_audit` call in `src/pitwall`, so a new
actor or action that lacks a migration now fails hermetically in CI, with no database required. It
resolves `a if cond else b` so the ternary actor at `teardown.py:141` is checked on both branches.

### 12. A serve that fails after creating the pod strands the pod and splits the database (critical)

Defect 11 was survivable; how the code failed was not. In `launch.py`, `arm_serve_provider` (line
1127) runs **after** `_persist_ready_lease` and **outside** the `try/except` that handles
pod-creation failures. When the audit write raised, three things happened at once:

1. The exception propagated and the caller was told `serve_failed`.
2. **The pod kept running and billing.** Nothing terminated it. It was still `RUNNING` when checked
   manually, and had to be deleted by hand.
3. The database was left contradicting the caller: the lease row was `active`, and the provider was
   `healthy` with `active_pod_id` set — because `repo.patch` and `insert_audit` are not in one
   transaction, so the provider mutation committed and only its audit record failed.

After the pod was terminated, the provider stayed `healthy` pointing at a pod that no longer
existed — a state routing would happily dispatch into. An earlier run of the same bug left the
stale lease that blocked a later relaunch.

**Fix:** any failure after pod creation must terminate the pod it created before returning, and the
provider patch plus its audit row must share one transaction so the record cannot desync from the
mutation. Reporting `serve_failed` while a paid pod runs and the lease reads `active` is the worst
available outcome.

### 13. The test suite marks unapplied migrations as applied in a real database (critical)

Adding migration 0027 and running the test suite once caused `db migrate` to report *"All
migrations already applied"* while the DDL had never run — the constraint was unchanged.

`tests/_migration_ledger.py:20` `restore_migration_ledger` inserts a `pitwall.schema_migrations`
row for **every migration file on disk**, regardless of whether its SQL was executed, and a pytest
*session finalizer* calls it against whatever real database is reachable. Its docstring states the
assumption — *"so the migration CLI sees no pending DDL"* — which holds only for a database the
harness built by raw-applying every file. It breaks the moment a file exists that was never
raw-applied, which is precisely the add-a-migration workflow.

The result is silent, permanent schema drift: the migration is stamped applied, `db migrate` skips
it forever, and the only symptom is a constraint failing in production. Recovery required deleting
the ledger row by hand.

**Fix:** the finalizer must only record migrations it actually applied, or must verify the schema
objects exist before stamping. A test fixture must never write to the ledger that production
correctness depends on.

### 14. Billing cannot reconcile a same-day run, and `granularity` is ignored (medium)

Phase 6 planned to reconcile measured spend against RunPod's own billing. That is not possible:

- A default query returned 7 daily buckets ending **2026-08-07**, 23 days before the run, with
  `podGpuAmount: 0` across the whole window — no pod spend at all, only storage.
- An explicit `2026-08-30` window returned `recordCount: 0`, though three pods demonstrably ran and
  were billed that day.
- `granularity: "hourly"` was **silently downgraded**: the echoed query reports
  `bucketSize: "day"`. The parameter is neither honoured nor rejected.

**Consequence:** cost reconciliation must be computed from pod uptime and the catalogue rate, not
from the billing API. Any Pitwall feature that plans to verify spend against RunPod billing needs
to account for the reporting lag, and must not trust a `granularity` it passes.

---

# Full lifecycle countersign (Phase 5, after the defect 11 fix)

With migration 0027 applied, `serve-model` completed a live end-to-end run and **every audit write
that was previously impossible succeeded**. This is the countersign the routing side was owed.

## Verified correct

- **The complete serve lifecycle works.** One command took a pod from nothing to a served model:
  template created and keyed by `config_sha`, pod allocated, model downloaded, readiness passed at
  **4.5 minutes**, provider armed, lease `active`. The CLI returned `created: true` with
  `lease_id`, `workload_id`, `template_id` and `proxy_base_url`.
- **All three lease audit rows landed**, written by production code against the real database:
  `system:lease | lease_ready | provider`, `system:lease | stop | lease`, and
  `system:lease | lease_closed | provider`. Before migration 0027 not one of them could be written.
- **The Pitwall OpenAI proxy serves the model end to end.** Through
  `/v1/openai/<capability>/v1`, `GET /models` returned the served id and `POST /chat/completions`
  answered correctly with full usage accounting — client → Pitwall → RunPod pod → llama.cpp.
- **Streaming works through the proxy**: 122 SSE chunks with well-formed delta frames and a proper
  `[DONE]` terminator.
- **Teardown is clean.** `POST /v1/leases/{id}/stop` moved the lease to `stopped`, disarmed the
  provider (`unhealthy`, `active_pod_id` cleared), and terminated the pod. Nothing was left running.
- **The budget gate refuses before spending.** A launch with `PITWALL_MONTHLY_BUDGET_USD` unset
  failed in 2 seconds having created no pod.
- **Route registration degrades safely.** With the routing CLI absent, `--route` reported
  `ok: false` with the exact remedy command and left the successful serve intact rather than
  failing or stranding the pod.
- **Arm and disarm were also driven directly against the live database** outside any pod, through
  `arm_serve_provider` and `disarm_serve_provider`, confirming the fix independently of RunPod.

## Defects

### 15. The image's CUDA floor is never pinned, so a launch fails on the luck of the draw (high)

Two of four RTX 4090 launches were doomed the moment they were allocated. The container never
started:

```
nvidia-container-cli: requirement error: unsatisfied condition: cuda>=12.8,
please update your driver to a newer version, or use an earlier cuda container
```

`ghcr.io/ggml-org/llama.cpp:server-cuda` requires CUDA ≥ 12.8; both pods landed on **CUDA 12.4**
hosts and retried the container every ~16 seconds for the entire lease. The runs that worked landed
on 13.0 — the difference was chance, not configuration.

This is entirely preventable with data RunPod already publishes. `GET /gpuTypes/{id}` reports
exactly which driver versions are on offer:

```
12.2: false   12.4: true   12.6: false   12.8: true   12.9: false   13.0: true   13.2: true
```

Pitwall already queries that endpoint for its catalogue and ignores `cudaVersions`, and it already
plumbs `allowed_cuda_versions` from provider config into the pod payload
(`runpod_client/pods.py:682`). The serve path simply never populates it.

**Fix:** record each image's minimum CUDA in the model dossier and pass the satisfying versions as
`allowedCudaVersions` at pod create. The wiring exists; only the value is missing.

### 16. `--datacenter` is accepted and silently dropped (medium)

`serve-model --datacenter EU-RO-1` logged `create_pod attempt: … dc=any` and then
`pod created: … dc=?`. The flag never reached the create payload; the pod was placed wherever RunPod
chose. An operator pinning a region for latency, residency, or driver reasons gets no error and no
placement.

### 17. `--idle-timeout-min` and `--max-usd-per-hour` never reach the lease row (high)

Both flags are parsed, validated, and carried through the serve request — and then dropped. The
lease row after a successful live serve had `idle_timeout_min` NULL and no hourly cap, and
`leases list` rendered `Idle —` and `Max $/h —`.

`_upsert_initial_lease` (`api/leases/launch.py:589`) inserts twelve columns and none of them are
`idle_timeout_min`, `max_usd_per_hour`, or `last_traffic_at` — the very columns migration 0024
added for this feature. `--max-usd-per-hour` still works as a pre-launch price ceiling, but nothing
bounds spend once the lease is running.

The consequence compounds with defect 18: migration 0024's idle index is partial
(`WHERE idle_timeout_min IS NOT NULL`), so a serve-created lease can never match the idle sweep.

### 18. The API never attaches Redis, so activity renewal and idle stop are inert (critical)

After three successful requests through the Pitwall proxy, the lease's `last_traffic_at` was still
NULL and **Redis held no keys at all**.

The proxy does call `stamp_lease_traffic` on every 2xx (`api/routes/openai.py:588`), but it passes
`getattr(request.app.state, "redis", None)` — and the main API app never sets `app.state.redis`.
Its lifespan is `db_lifespan`, which attaches Postgres only; the single assignment in the
repository lives in the **webhook receiver**, a different ASGI app. Five call sites in the API read
that attribute and all silently receive `None`:
`routes/serve.py:54`, `routes/leases.py:91`, `routes/openai.py:540`, `routes/openai.py:589`,
`app.py:556`. `stamp_lease_traffic` opens with `if redis is None: return`, so the failure is
completely silent — no warning is ever logged.

Nothing therefore records traffic; the reconciler's write-through finds no key, treats the lease as
busy, and `last_traffic_at` stays NULL forever. **Activity-based renewal and the idle-timeout stop
— the two controls the serve design rests on — cannot fire on the live path.**

Two things hid this. The tests assign `app.state.redis` themselves (for example
`tests/test_api_security_middleware.py:363`), supplying the exact attribute production leaves
unset — the same mock-fidelity failure as defect 11. And `/health` reports Redis healthy regardless:
`_redis_health` sees `app.state.redis` is `None` and builds a throwaway client from `_REDIS_URL`
just to ping it, so the health probe exercises a path no feature uses.

**Fix:** attach a Redis client to `app.state.redis` in the API lifespan, and make the health check
report the client the request path actually uses rather than one it constructs for the occasion.

### 19. The CLI cannot stop or renew a lease it launched (low)

`POST /v1/leases/{id}/renew` accepts `extends_minutes` (default 60). The live 422 came from sending
`{"ttl_minutes": 10}`, a field that does not exist; the model's `extra=forbid` correctly rejected it.
Separately, `pitwall-gpu-broker
leases` exposes only `list` — an operator who launched with `serve-model` has no CLI path to stop or
renew what they started and must call the API directly.

---

# Serverless and network volume findings (Phases 3 and 4)

Spend: **$0.00 of compute.** No serverless worker was ever started (`workersMin: 0`, no job
submitted) and the network volumes existed for seconds. Every resource was deleted.

## Verified correct

- `get_endpoint` and `list_endpoints` work against the live API.
- Network volume `create`, `get`, `list`, and `update` (grow) all work: a 10 GB volume in `EU-RO-1`
  was created, read back, and grown to 20 GB.
- `GET` on a deleted volume correctly returns **404**.

## Defects

### 20. `create_endpoint` sends a body the API rejects, so no endpoint can be created (high)

Pitwall's `create_endpoint` nests scaling settings under a `scaling` object. RunPod refuses it:

```
POST endpoints failed with HTTP 400: {"error":"Extra input keys provided in request body",
"problems":["key provided in request body which is not in input schema: 'scaling'"]}
```

The published schema for `POST /endpoints` takes these fields **flat**, not nested:

```
allowedCudaVersions, computeType, cpuFlavorIds, dataCenterIds, executionTimeoutMs, flashboot,
gpuCount, gpuTypeIds, idleTimeout, minCudaVersion, name, networkVolumeId, networkVolumeIds,
scalerType, scalerValue, templateId, vcpuCount, workersMax, workersMin
```

The same call succeeds once the payload is flattened — an endpoint was created with
`workersMin: 0, workersMax: 1, idleTimeout: 5` and then deleted. Pitwall also sends `gpuIds`, while
the schema names the field `gpuTypeIds`.

This is the same failure mode as defects 1-3: a client method written against a schema the API does
not have, with unit tests driving fakes that accept anything.

**Fix:** flatten `EndpointScalingConfig.to_request_json()` into the top-level body and rename
`gpuIds` to `gpuTypeIds`. Note the schema also exposes `allowedCudaVersions` and `minCudaVersion` —
the same controls defect 15 needs on the pod path.

### 21. Network volume `delete` is not idempotent against the real API (medium)

`delete` documents itself as "Idempotent: silent on 404" and swallows only `404`. RunPod answers a
delete of a nonexistent volume with **500**:

```
DELETE /networkvolumes/{id} failed with HTTP 500:
{"error":"delete network volume: Tried to delete nonexistent network volume","status":500}
```

So the guard never fires and a repeated delete raises — exactly what a cleanup or retry path does.
The API is inconsistent here rather than Pitwall: `GET` on the same deleted volume returns a proper
404. A 500 is also indistinguishable from a genuine server fault, so a retry loop cannot tell
"already gone" from "try again".

### 22. Network volume `update` silently shrinks a volume (medium)

`update` documents "size must be larger than current". It is not enforced anywhere, and RunPod
accepts a shrink: a volume grown to 20 GB was resized to 5 GB, and a re-read confirmed
`size: 5`. Shrinking a volume that holds cached weights is a data-loss operation available through
a method whose contract says it cannot happen.

**Fix:** reject a size smaller than the current one in the client, where the docstring already
claims the invariant holds.

### RunPod returns 500 for client errors, repeatedly

Three separate cases in this campaign returned **HTTP 500 for a caller mistake or a permanent
condition**: pod create with no capacity (defect 6), deleting a nonexistent volume (defect 21), and
creating a volume in an unknown datacenter (`{"error":"create network volume: Data center
\"NOT-A-DC-1\" not found or does not support network volumes"}`). Pitwall's retry and fallback
logic assumes conventional status semantics, so it reads a permanent client-side error as a
transient server fault and retries or falls through the whole GPU list instead of failing fast.

**Fix:** treat RunPod 500s as ambiguous — inspect the error body before deciding whether a failure
is retryable.

---

# Open, unconfirmed

## A schemathesis run reported a 500 on `/v1/serve` that has not reproduced

While implementing the remediation plan, one hermetic run of
`tests/security/test_schemathesis_fuzz.py` failed with an invalid Docker image input escaping
`ServeRequest.model_validate` as a 500 rather than a 422 on `POST /v1/serve`.

It has not reproduced since: five subsequent runs of that module were clean, on two different
trees, and a direct probe of ten malformed image strings (empty, whitespace, uppercase, 300
characters, bare tag, bare repo, embedded space, trailing newline, path traversal, mixed case) was
rejected cleanly as `ValidationError` every time.

Recorded because a hypothesis-driven suite finding an input once is evidence of a real input class,
not noise, and the seed is gone. **This is not a confirmed defect** — no failing input is in hand.
If it recurs, capture the generated payload from the hypothesis output before rerunning; that
payload is the whole finding.


---

# Disposition (2026-09-23)

Every defect above, with where its fix lives and the test that holds it. Fixes before the
public history began are in the sanitized snapshot `a89a1b3`; per-defect commits from
that period are not available, so the regression test is the durable evidence.

| # | Defect | Fix | Regression test |
| --- | --- | --- | --- |
| 1 | `delete_template` rejected | Template delete moved to REST v2 (`runpod_client/templates.py::delete_template`), `a89a1b3` | `tests/runpod_control_plane/test_backend.py` |
| 2 | `get_template` queried a missing field | REST v2 `get_template`, `a89a1b3` | `tests/runpod_control_plane/test_service.py` |
| 3 | Template `ports` dropped | REST v2 `create_template_rest` sends `ports`, `a89a1b3` | `tests/runpod_control_plane/test_backend.py` |
| 4 | RTX 4080 SUPER missing | Added to `GPU_VRAM_GB`, `a89a1b3` | `tests/fixtures/runpod_gputypes_2026-08-27.json` via `tests/test_runpod_gpu_validator.py` |
| 5 | Fit recommended unbuyable GPUs | `models/fit.py` reads live `maxCount` stock, `a89a1b3` | `tests/models/test_fit_availability.py` |
| 6 | 500 read as "no capacity" | `classify_runpod_failure` separates capacity, permanent, and transient, `a89a1b3` | `tests/runpod_client/test_capacity_classification.py` |
| 7 | Crash loop burned the startup budget | Uptime-regression restart detection in both serve paths (`CONTAINER_RESTART_LIMIT`), `8891834` | `tests/runpod_client/test_readiness_abort.py::test_a_container_that_keeps_restarting_fails_fast_with_its_own_error`, `tests/personal/test_service.py::test_a_crash_looping_container_is_terminated_before_the_startup_budget` |
| 8 | Dossier named a missing GGUF | Dossier corrected at discovery; opt-in resolver lane | `tests/live/test_catalogue_weights_resolve.py` (`PITWALL_HF_CATALOGUE_CHECK`) |
| 9 | Rate demanded though known | Rate defaults from the live price snapshot (`serve.py`, "defaulted rate_per_second"), `a89a1b3` | `tests/serve/test_serve_model.py` |
| 10 | Deprecated v1 control plane | REST v2 migration per ADR 0006, `cd9f5bb` | `tests/runpod_client/test_v2_contract.py` |
| 11 | Lease audit writes violated constraints | Migration `0027` | `tests/db/test_config_audit_action_contract.py` |
| 12 | Failed serve stranded the pod and split the database | Terminate after any post-create failure; provider patch and audit in one transaction, `a89a1b3` | `tests/leases/test_launch_failure_cleanup.py`, `tests/integration/test_provisioning_budget_lifecycle.py::test_arm_provider_rolls_back_when_audit_insert_is_rejected` |
| 13 | Tests stamped unapplied migrations | `restore_migration_ledger(applied_versions=...)` records only raw-applied versions | `tests/_migration_ledger.py` and the integration fixtures that use it |
| 14 | Billing lags; `granularity` ignored | Reconciliation uses pod uptime × rate; documented | `docs/operations/cost-reconciliation.md`, `tests/test_cost_reconciliation_docs.py` |
| 15 | CUDA floor never pinned | `min_cuda` per variant; `cuda_allow_list` allows every version at or above it on both paths (`b44ac1c`) | `tests/serve/test_cuda_allow_list.py`, `tests/models/test_catalogue_integrity.py` |
| 16 | `--datacenter` dropped | `datacenter` maps to `data_center_id` in the provider config, `a89a1b3` | `tests/leases/test_data_center_selection.py` |
| 17 | Idle and hourly caps not persisted | `_upsert_initial_lease` writes `idle_timeout_min` and `max_usd_per_hour`, `a89a1b3` | `tests/integration/test_lease_acceptance.py` |
| 18 | API never attached Redis | `api/lifespan.py` sets `app.state.redis` | `tests/test_webhook_duplicate_delivery_stress.py` and the lifespan tests |
| 19 | CLI could not stop or renew | `pitwall leases stop` and `renew`, `a89a1b3` | `tests/cli/test_leases_commands.py` |
| 20 | Endpoint create body rejected | v2 nested serverless shape (ADR 0006), `cd9f5bb` | `tests/runpod_client/test_endpoint_payload_contract.py` |
| 21 | Volume delete not idempotent on 500 | `already_gone` recognises the nonexistent-volume body, `a89a1b3` | `tests/runpod_client/test_volume_contract.py` |
| 22 | Update shrank a volume | Client and service refuse sizes at or below the current size, `a89a1b3` | `tests/runpod_client/test_volume_contract.py`, `tests/runpod_control_plane/test_service.py` |
