# Pitwall personal-first design

Status: approved by the maintainer on 2026-09-02; implementation plan at
`docs/superpowers/plans/2026-09-02-pitwall-personal-first.md`.

## 1. Purpose

Pitwall exists to make one hard thing easy: spin up a GPU pod, serve an open-weight model on
it, and use that model from a coding agent, without learning the provider console, container
flags, readiness checks, and shutdown hygiene yourself. Today that path only works when Postgres,
Redis, the Pitwall API, and the reconciler are all running, because the serve flow was built as
the operator surface of a hosted broker.

This design makes the personal path the default. A user with nothing but a RunPod credential
opens `pitwall`, walks through a wizard, watches the pod come up, and has the model attached to
the Claude Code routing plugin. The hosted capabilities remain, and light up automatically when a
database is configured. Nothing is called a mode; the tool adapts to what is present.

## 2. Principles

- One name. The command is `pitwall`, the plugin is `pitwall`, and there are no compatibility
  aliases.
- Personal first. Bare `pitwall` opens the console; `pitwall serve` launches; both work with only
  a RunPod credential.
- Adaptive, not modal. Behaviour follows configuration. The tool states in one line which backend
  it is using and never asks the user to choose.
- One engine. Plan, launch, verify, attach, and stop are one sequence shared by CLI, console, and
  MCP. Only where state lives differs.
- Safe by default. Every personal pod carries its own deadline, is protected by an endpoint key,
  and is torn down on any failure after it exists.
- Nothing reflected. Provider error bodies and credentials never reach output or logs.

## 3. Naming

| Today | After |
| --- | --- |
| console script `pitwall-gpu-broker` | `pitwall` (only script) |
| `pitwall-gpu-broker serve-model` | `pitwall serve` |
| `pitwall-gpu-broker dashboard` | bare `pitwall` |
| container images `pitwall-gpu-broker/<service>` | `pitwall/<service>` |
| plugin `subagent-model-routing-claude` | `pitwall` |
| plugin `subagent-model-routing-codex` | `pitwall-codex` |
| plugin `subagent-model-routing-copilot` | `pitwall-copilot` |
| marketplace `subagent-model-routing` | `pitwall` |
| agent types `subagent-model-routing-claude:<shim>` | `pitwall:<shim>` |
| plugin scripts `model-routing` and `pitwall-agent-routing` | `pitwall-agent-routing` only |
| setting default `pitwall_routing_cli = "model-routing"` | `"pitwall-agent-routing"` |

All existing command groups keep their names under `pitwall` (`db`, `leases`, `models`, `runpod`,
`runpod-onboard`, `provider-ops`, `volume-files`, `routing`, `cost`, `burn-rate`, `guardrails`,
`init`, `config`, `seed`, `register-template`, `register-endpoint`, `set-provider-health`,
`terminate-pod`, `warm-volume`, `retention`, `mcp`). The plugin packages move to a new minor
version because a renamed namespace breaks anyone holding the old name; the routing skill already
documents that a stale namespace fails loudly rather than misrouting.

## 4. User experience

### 4.1 First run

`pitwall setup` runs automatically the first time `pitwall` or `pitwall serve` finds nothing
configured, and can be re-run at any time. Each step is skippable and idempotent:

1. RunPod credential: use `RUNPOD_API_KEY` if set; otherwise read `~/.runpod/config.toml`;
   otherwise offer to run `runpodctl doctor`, which prompts for and saves the key. Pitwall never
   writes the RunPod key anywhere.
2. If `~/.runpod/config.toml` is group- or world-readable, warn and offer to make it owner-only.
3. Endpoint key: generate a 32-byte URL-safe token into `$XDG_STATE_HOME/pitwall/endpoint.key`
   (default `~/.local/state/pitwall/`), owner-only, only if absent.
4. Offer to append `export PITWALL_ENDPOINT_KEY="$(cat …/endpoint.key)"` to the user's shell
   profile; otherwise print the line.
5. Check that `pitwall-agent-routing` is on `PATH`; if not, print the plugin install command.
6. Report the backend that will be used: "personal (local state file)" when `DATABASE_URL` is
   unset, "registry (database)" when it is set.

### 4.2 Serve

```bash
pitwall serve --model ornith-ai/Ornith-1.5-35B-A3B-GGUF --gpu-class "NVIDIA GeForce RTX 3090" \
  --ttl-minutes 45 --max-usd-per-hour 1.00 --route ornith
```

With no database: plan from the catalogue, refuse if unpriced without `--rate-per-second` or if
the live price exceeds the cap, create one pod with an in-pod deadline, wait for the model to
appear in `/v1/models`, attach the route to the plugin, and print the route name, the served model
id, the endpoint, the deadline, and the exact `route-shim.sh <route> prompt.md` line to try.

With a database: today's `serve-model` behaviour, unchanged, under the new name and flags. The
`--route` flag keeps its current meaning there (`routes add --from-pitwall`).

`--max-usd-per-hour` is required on the personal backend. `--ttl-minutes` defaults to 60.

### 4.3 Status and stop

`pitwall status` lists personal leases from the state file, reconciled against the RunPod API on
every read: name, model, GPU, state, time remaining, endpoint, route. With a database it lists
registry leases through the existing `leases list` path. `pitwall stop <route>` terminates the pod,
removes the plugin route, and marks the record stopped; `pitwall stop --all` does so for every
personal lease that still has a pod.

### 4.4 Console

Bare `pitwall` opens the console. Without a database it shows Models, Fit, Serve, Pods, and
Routes. With a database the existing Overview, Providers, Leases, Cost, Resources, and Operations
views appear as well, and Serve, Pods, and Routes read from the registry backend.

- Serve: a wizard. Model, variant, GPU class (each row shows live price, stock, and fit), cloud,
  TTL, hourly cap, route name. The confirmation page shows the exact pod request (image, argv with
  the endpoint key redacted, port, disk, GPU, cloud), the maximum spend (cap times TTL), and the
  deadline, and requires the route name to be typed.
- Pods: one row per personal lease with state, time remaining, GPU, price, and endpoint; a detail
  pane with the last log lines from the bounded pod-log client; a Stop action with typed
  confirmation.
- Routes: the route attached to each pod, the last probe result, and the `route-shim.sh` line.

Screens talk to source protocols. All work happens in `pitwall.personal`, so the existing
no-business-logic guard for TUI widgets continues to hold.

## 5. Architecture

### 5.1 Package layout

```text
src/pitwall/personal/
  __init__.py
  backend.py     LeaseBackend protocol, LocalBackend, RegistryBackend, select_backend()
  state.py       PersonalLease model, StateStore (atomic JSON, owner-only)
  keys.py        endpoint key generation and lookup, RunPod credential resolution
  deadline.py    wrap_start_command(): the in-pod timer entrypoint
  service.py     PersonalServeService: plan, launch, wait, attach, stop, status
  routes.py      attach_route, probe_route, remove_route over pitwall-agent-routing
  setup.py       the setup steps
src/pitwall/cli_personal.py   pitwall serve|status|stop|setup verbs
src/pitwall/tui/personal.py   PersonalServeSource protocol and the three screens
```

### 5.2 Engine and backends

`PersonalServeService` is the one engine. Its steps are `plan`, `launch`, `wait_ready`,
`attach`, and `stop`, plus `status`. It depends on functions that already exist and are
database-free: `plan_catalogue_model` and `launch_shape` in `pitwall.serve`,
`verify_served_model` in `pitwall.serve`, `load_gpu_price_snapshot` in `pitwall.models.prices`,
`fit_options` in `pitwall.models.fit`, and the RunPod client functions
`create_pod_with_fallback`, `get_pod`, and `terminate_pod` in `pitwall.runpod_client.pods`, plus
`BoundedPodLogClient` in `pitwall.runpod_client.pod_logs`.

`LeaseBackend` is chosen by `select_backend(settings)`: `RegistryBackend` when `DATABASE_URL`
is set, `LocalBackend` otherwise. `RegistryBackend` wraps today's `serve_model` and lease
repository paths without changing them. `LocalBackend` uses `StateStore`. The CLI verbs, the
console, and the MCP `pitwall_serve_model` tool all go through the service.

### 5.3 State

`$XDG_STATE_HOME/pitwall/leases.json` holds a list of `PersonalLease` records:

| Field | Meaning |
| --- | --- |
| `route` | route name; unique key |
| `pod_id` | RunPod pod id |
| `model` | catalogue model id requested |
| `served_model_id` | alias the server reports in `/v1/models` |
| `engine`, `variant`, `image` | from the plan |
| `gpu_class`, `gpu_count`, `cloud` | as launched |
| `price_per_hour_usd` | live price at launch, as a decimal string |
| `endpoint_url` | `https://<pod_id>-8000.proxy.runpod.net/v1` |
| `key_env` | always `PITWALL_ENDPOINT_KEY` |
| `launched_at`, `deadline_at` | ISO 8601 UTC |
| `state` | `launching`, `ready`, `stopped`, `failed`, `gone` |
| `failure` | short stable code when `failed` |

Writes are atomic: write to a temporary file in the same directory, then rename. The file and
directory are created owner-only. The endpoint key lives beside it in `endpoint.key`.

## 6. The personal serve flow

1. **Plan.** `plan_catalogue_model` produces image, argv, served model id, and startup timeout.
   `load_gpu_price_snapshot` plus `fit_options` produce price, stock, and fit for the chosen GPU
   class and cloud. Refuse, before any write, when: the class does not fit, the price is unknown
   and no `--rate-per-second` was given, the price exceeds `--max-usd-per-hour`, or the route name
   already exists in the state file or in the plugin.
2. **Launch.** One `create_pod_with_fallback` call with `wait_for_readiness=False`,
   `max_cost_per_hr` set to the cap, `WorkloadConfig(gpu_types=[gpu_class], gpu_count,
   container_disk_gb=50, ports="8000/http", cloud_type=<cloud>, allowed_cuda_versions=<from the
   dossier when present>)`, `env` limited to `HF_TOKEN` when the user has one and the dossier is
   gated, `docker_entrypoint=["sh", "-c"]`, and `docker_start_cmd=[wrapped]` where `wrapped` is
   produced by `wrap_start_command(server_argv, ttl_seconds)`:

   ```sh
   ( sleep <ttl_seconds>; curl -fsS -X DELETE \
       -H "Authorization: Bearer $RUNPOD_API_KEY" \
       "https://api.runpod.io/v2/pods/$RUNPOD_POD_ID" ) >/dev/null 2>&1 &
   exec <server binary> <catalogue argv> --api-key "$PITWALL_ENDPOINT_KEY"
   ```

   The endpoint key is passed to the pod as the env var `PITWALL_ENDPOINT_KEY`, so it never appears
   in the argv the console or logs show. `RUNPOD_API_KEY` and `RUNPOD_POD_ID` are injected by
   RunPod into every pod. The engine binary comes from the dossier: `/app/llama-server` for
   llama.cpp images, `vllm serve` for vLLM images. Because `dockerEntrypoint` is only available on
   the legacy v1 body, the existing client's v1 path is used; its reasons list gains
   `dockerEntrypoint`, which it already recognises.
3. **Record.** The lease is written as `launching` with the pod id immediately after create
   returns, so an interrupted process still knows what to clean up.
4. **Wait.** `verify_served_model("<endpoint_url>/models", served_model_id)` with the dossier's
   startup timeout. The console shows elapsed time and the last log lines meanwhile. On timeout:
   terminate, mark `failed` with `readiness_timeout`.
5. **Attach.** `pitwall-agent-routing routes add <route> --base-url <endpoint_url> --model
   <served_model_id> --api-key-env PITWALL_ENDPOINT_KEY --seat local`, then `routes probe
   <route>`. On failure: terminate, mark `failed` with `route_attach_failed`, print the manual
   remedy line. On success: mark `ready`.
6. **Stop.** `terminate_pod`, `routes remove <route>`, mark `stopped`. A pod that is already gone
   is not an error.
7. **Reconcile.** `status`, the console's Pods view, and every `serve` call `get_pod` for each
   record in `launching` or `ready`. A missing pod becomes `gone`. A record past `deadline_at`
   whose pod still exists is terminated on the spot and reported as terminated late.

## 7. Registry backend

Unchanged behaviour behind the new names. `pitwall serve` with `DATABASE_URL` set calls the
existing `serve_model` path; `pitwall status` calls the existing lease listing; `pitwall stop`
calls the existing lease stop. The reconciler keeps enforcing TTL and idle stop. The in-pod timer
is not added on this backend. Unifying the two launch paths further is out of scope.

## 8. Plugin

The plugin needs no behavioural change for attachment: `routes add --base-url` routes, `routes
probe`, and `routes remove` already exist. The rename is the plugin work: package ids, the
marketplace manifest, the agent-type and skill namespaces in every agent, skill, command, hook,
and doc, the `install.sh` aliases, the shim `ENTRYPOINT`, and the removal of the `model-routing`
script. Pitwall's `pitwall_routing_cli` default changes to `pitwall-agent-routing`.

## 9. Security

- The RunPod credential is read at call time from the environment or `runpodctl`'s config and is
  never written by Pitwall.
- The endpoint key is generated once per machine, stored owner-only, passed to pods as an env
  var, and referenced by routes through its name only. Argv shown anywhere has the key redacted.
- Pods expose only `8000/http`. No volume is attached, so a stopped pod bills nothing.
- Provider error bodies, keys, and tokens never appear in output or logs, matching the existing
  redaction helpers.
- `setup` warns about a world-readable `~/.runpod/config.toml` and offers to fix it.

## 10. Errors

| Situation | Behaviour |
| --- | --- |
| No credential | `setup` runs; `serve` exits 2 with the remedy |
| GPU class does not fit, unpriced without rate, or over cap | exit 1 before any API write |
| Route name already exists | exit 1 before any API write |
| Create returned a pod but readiness never came | terminate, record `failed`, exit 3 |
| Route attach failed | terminate, record `failed`, print remedy, exit 3 |
| Ctrl-C during `serve` after a pod exists | terminate, record `failed`, exit 130 |
| `stop` on a pod that is already gone | record `gone`, exit 0 |
| Record past deadline with a live pod | terminate now, report "terminated late" |

## 11. Testing

- Unit tests with fakes for: plan refusals; the exact v1 request body including the wrapped
  entrypoint; readiness timeout teardown; attach and remove command lines and exit codes; state
  store atomicity, permissions, and reconciliation transitions; setup steps against a temporary
  home; key redaction in rendered argv.
- Pilot tests for the three console screens and for the view set with and without a database.
- The no-business-logic guard, route inventory, MCP registry, and OpenAPI tests extend to the
  new verbs and stay green.
- One opt-in live test under the existing `live` marker and `PITWALL_RUN_LIVE` gate: launch the
  cheapest catalogue model with a 15-minute TTL and a low cap, verify `/v1/models`, probe the
  route, wait for the in-pod timer, and assert the pod is gone. It also settles whether the
  pod-scoped key may terminate its own pod; if only `stop` is permitted, the timer falls back to
  `stop`, which costs nothing because there is no volume.

## 12. Delivery order

1. Rename: script, usage, docs, tests, Makefile, compose, CI and release image tags, plugin ids,
   namespaces, scripts, and the `pitwall_routing_cli` default. No behaviour change.
2. Personal backend and verbs: `pitwall.personal`, `serve`, `status`, `stop`, `setup`, the live
   test.
3. Console: the Serve wizard, Pods and Routes views, and the backend-dependent view set.

## 13. Risks and open points

- Whether the pod-scoped `RUNPOD_API_KEY` may `DELETE` its own pod is documented for `stop` but
  not for terminate. The live test decides; the fallback is coded.
- The RunPod v1 API is marked deprecated. It is used only because `dockerEntrypoint` is not on v2.
  If v2 gains an entrypoint field, the client switches without changing this design.
- Renaming published image tags changes what the release workflow publishes. The release notes
  must say so.
- A user who declines the shell-profile edit must export the endpoint key before Claude Code can
  use a route; `pitwall status` shows whether the current shell has it.

## 14. Out of scope

Idle stop on the personal backend, multi-pod routing policies, a second provider for personal
pods, merging the two launch paths into one implementation, and any change to the hosted proxy,
budget, or guardrail behaviour.
