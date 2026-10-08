# RunPod account resource controls

Pitwall exposes raw RunPod account resources for operator administration while
keeping broker-managed leases and `pitwall serve` as the preferred paths for paid
compute. Raw pod controls do not create a Pitwall lease, enforce a lease TTL, or
replace broker reconciliation. Use them for inspecting and deliberately
administering account state.

All transports call `RunPodControlPlaneService`; none reimplements provider
requests. The service accepts strict REST v2 names, bounds provider calls,
validates resource identifiers, sanitizes results and errors, and requires an
audit store before a live mutation. Unknown fields and deprecated v1 names are
rejected before a provider write.

## Operation matrix

| Resource | Shared operations | REST under `/v1/admin/runpod` | CLI under `runpod` | TUI Resources |
|---|---|---|---|---|
| Raw pods | list, get, create, update, start/stop/restart/reset, terminate | `GET/POST /pods`, `GET/PATCH/DELETE /pods/{id}`, `POST /pods/{id}/action` | `pods list|get|create|update|action|terminate` | tables, get, create, update, action, terminate |
| Serverless endpoints | list, get, create, update, delete | `GET/POST /endpoints`, `GET/PATCH/DELETE /endpoints/{id}` | `endpoints list|get|create|update|delete` | tables, get, create, update, delete |
| Account templates | list, get, create, update, delete | `GET/POST /templates`, `GET/PATCH/DELETE /templates/{id}` | `templates list|get|create|update|delete` | tables, get, create, update, delete |
| Network volumes | list, get, create, grow, delete | `GET/POST /volumes`, `GET/PATCH/DELETE /volumes/{id}` | `volumes list|get|create|grow|delete` | tables, get, create, grow, delete |
| Registry auth | list, get, create, replace, delete | `GET/POST /registry-auths`, `GET/DELETE /registry-auths/{id}`, `POST /registry-auths/{id}/replace` | `registry-auths list|get|create|replace|delete` | tables, get, create, replace, delete |
| Public Hub templates | list, get, search | `GET /hub/templates`, `/hub/templates/{id}`, `/hub/templates/search` | `hub list|get|search` | table, get, search |

The isolated MCP manifest contains the same 29 operations with
`pitwall_runpod_*` names. Hub has no create, update, publish, deploy, or delete
operation on any transport. Account templates and public Hub templates are
different resource types and different listings.

Hub listings are paged: `hub list` takes `--limit` (default 50) and `--offset`
(default 0), and `hub search QUERY` takes `--limit` (default 50). The MCP tools
`pitwall_runpod_list_hub_templates` (`limit`, `offset`) and
`pitwall_runpod_search_hub_templates` (`query`, `limit`) use the same defaults.

Endpoint create and update preserve the REST v2 nested controls:

```json
{
  "workers": {"minimum": 1, "maximum": 4, "idle_timeout_seconds": 120},
  "scaling": {"type": "REQUEST_COUNT", "value": 2},
  "gpu": {
    "pools": ["ADA_24", "AMPERE_48"],
    "excluded_type_ids": ["GPU-X"]
  },
  "flashboot": true
}
```

Updates replace the supplied worker/scaling configuration. A GPU selection is
optional on update; omitting it preserves the provider selection. Fields such
as `workersMin`, `gpuTypeIds`, and other v1 spellings are not accepted.

## Mutation safety

Direct REST and MCP mutation bodies must contain both:

- `intent`: `preview` or `apply`;
- `idempotency_key`: an operator-generated 8–128 character key.

A preview validates the request and returns the operation, target, effect,
estimated ceiling when available, and irreversible flag without a provider or
audit write. Previews never use the idempotency key.

An apply records its key in a journal of `config_audit` rows before it changes
anything at RunPod. Reusing a key has four possible outcomes:

- **Same key, same request, earlier apply finished:** the stored result comes back
  with `replayed: true`, and RunPod is not called. A lost response is therefore
  safe to retry with the same key, including for creates, restarts, resets, and
  volume grows.
- **Same key, different request:** refused with `idempotency_conflict` (HTTP 409).
  Changing an environment value, a process argument, or the registry username
  counts as a different request. Pitwall compares those values through a keyed
  digest derived from the RunPod API key and never stores them, so rotating the
  API key also turns a pending same-key retry into a conflict.
- **Same key, earlier attempt has an unknown outcome:** refused with
  `mutation_outcome_ambiguous` (HTTP 409), and never re-applied. Causes include a
  timeout, a 5xx or transport error, a partial failure, or a crash after the call
  started. Inspect the named resource in RunPod, delete it or adopt it, then retry
  with a new key.
- **Same key, earlier attempt provably changed nothing:** the key is released and
  the apply runs again. This covers a failed precondition read, a refusal such as
  `resource_name_conflict` or `volume_grow_only`, or a RunPod 4xx. A pod create is
  the exception: a create that has reached RunPod never releases its key, because
  its fallback attempts can leave a pod behind before the final error.

A second request with a key that is still in flight waits for it, up to the
service timeout, and then fails with the retryable `mutation_in_progress`
(HTTP 409). Onboarding compensation, and the MCP pod-create lease rollback,
release the create key of a resource they delete, so a resume or retry creates
it again; a released key accepts a new request only for the same operation and
resource. An MCP pod create that failed before reaching RunPod closes its budget
workload at $0 and frees its key, so a retry passes the budget check again. One
whose outcome is unknown keeps its workload open and its budget reserved, and a
same-key retry is refused. If the error named the pod, the tool records the
pod's lease, and the reconciler closes the workload at the accrued cost once
RunPod reports the pod gone or its TTL teardown runs. Otherwise the reconciler
looks for the pod. It never touches a create that is still running, and it waits
about six minutes after the create's last journal record so a new pod can appear.

Every pod Pitwall creates carries the environment variable
`PITWALL_CREATE_ATTEMPT`, set to a marker derived from the create's idempotency
key. The marker is unique per key, not per attempt: a retry with the same key,
for example after a lease rollback, carries the same marker. That reuse is still
safe. The rollback terminated the earlier pod, and a terminated pod that an
earlier attempt with the key recorded is never matched. If two pods ever carry
the marker, the reconciler holds instead of choosing.

- If it finds one pod with the key's marker, it leases that pod, and the pod is
  torn down at the end of its TTL. A marked pod that is already terminated is
  leased too, because it existed and billed; the lease is then settled at its
  accrued cost, never $0.
- If it finds no pod with the marker and no running pod with the pod's name, it
  closes the workload at $0.
- If the lookup fails, several pods carry the marker, or a pod has the name but
  not the marker, it holds the workload and tries again on the next pass, every
  five minutes. Pitwall never adopts or terminates a pod without its marker; the
  log names such pods.

If a create completed but Pitwall stopped before recording its lease, the
reconciler leases the pod by the ID RunPod returned, the same way. It does the
same when Pitwall could not record the lease and also could not terminate the new
pod, or could not even tell whether a lease exists (its database was down). It never
terminates a pod it cannot prove is unleased: the workload stays open instead of
closing at $0, and the call fails with the retryable `audit_unavailable`. An MCP client sees
`retryable: true`, the pod id, and a remedy telling it to retry with the same
idempotency key once the database is back. A new key would create a second pod. If the reconciler's lease
lands while Pitwall is still recording its own, Pitwall returns the reconciler's
lease and keeps the pod.

A workload still unresolved after about an hour is charged its reserved ceiling;
check RunPod for the pod by name and terminate it if it is still running. A
retried pod create whose pod has since been terminated is refused rather than
leased again; start over with a new key. If that pod belonged to a workload Pitwall
had kept open, Pitwall records the pod's lease and settles it at once, so the
reconciler charges it instead of closing it at $0. A lease Pitwall records late,
here or when the reconciler adopts a pod, is charged from the create attempt's
start until the reconciler settles it. The rate is `max_cost_per_hour` when you
set one, otherwise the pod's price as RunPod reported it when Pitwall created it
(`costPerHr`), otherwise the $0.50/hour the budget reserved for it; it is never $0.
A lease's `max_usd_per_hour` is only the cap you set; for an uncapped pod it stays
empty, and RunPod's price is not shown there. If the pod stopped
early, that overstates its cost in Pitwall's budget, and the $0.50/hour fallback
can be above or below the pod's real price; check the pod's real runtime and price
in RunPod billing and reconcile the workload's cost by hand if it matters. When onboarding stops on
`runpod_mutation_outcome_ambiguous`, inspect the named resource in RunPod,
delete it or select it as an existing resource, then resume. A resume releases
an unknown outcome only once the attempt is more than six minutes old (the
300 s call ceiling plus a margin); before that it refuses with the same remedy.
A resume re-creates a completed step's resource only when a lookup by its
recorded ID says it is gone (an HTTP 404; an empty or unreadable reply is a
provider error and the resume stops), so a list that has not caught up does not
cause a duplicate.

Model-serving and pod-lease launches (`pitwall_serve_model`, `pitwall_lease_pod`,
`pitwall serve`, `POST /v1/serve`, `POST /v1/leases`) take an optional
`idempotency_key` with the same rule for a lost response: retry with the same key.
The retry returns the first launch's lease (`replayed: true` on a lease,
`created: false` on a serve) without a second pod or a second budget reservation.
The same key with a different request is refused with `idempotency_mismatch`. A
retry while the first launch has not recorded its lease yet, or (for a serve)
while that lease is not yet active and serving the model, gets the retryable
`mutation_in_progress`; retry with the same key. A launch that failed before any
pod was attempted (for example no capacity) frees its key, so the same-key retry
launches. A key whose launch ended without a lease after a pod attempt, or with
an unknown outcome the reconciler closed, is refused with `idempotency_conflict`,
and a serve key whose lease has stopped with `lease_state_conflict`; start over
with a new key. A new key alone does not replace a serving pod: while the
provider has an active lease, a serve with a never-used key finds no keyed
launch, then checks that the lease's pod lists the model and returns that lease
with `created: false`, with no second pod. If the pod does not list the model,
the serve is refused with `lease_not_serving` and does not tear the lease down.
To get a replacement pod, end the lease first (`pitwall_stop_lease` or
`pitwall leases stop <lease-id>`), then serve again with a new key. Lambda Cloud
and Vast leases follow the same rules.

Deletes and pod termination check current state. With a new key they return
`already_absent: true`, without a second delete, once the resource is gone.

The CLI requires `--idempotency-key` on every mutation. Add `--dry-run` for a
preview. A live command also requires `--confirm` with the exact resource ID, or
the exact name for a create:

```console
pitwall runpod pods create \
  --request-json '{"name":"debug-pod","image":"example/image:1","gpu_type_ids":["NVIDIA L4"]}' \
  --idempotency-key operator-20260901-0001 --dry-run --json

pitwall runpod endpoints update ep_123 \
  --request-json '{"workers":{"minimum":1,"maximum":4,"idle_timeout_seconds":120},"scaling":{"type":"REQUEST_COUNT","value":2},"gpu":{"pools":["ADA_24"],"excluded_type_ids":[]}}' \
  --idempotency-key operator-20260901-0002 --confirm ep_123 --json
```

The Textual Resources view is read-only by default. “Prepare mutation” opens a
strict request editor, always calls the preview operation first, displays the
provider, target, effect, ceiling, and consequences, then enables Apply only
after the target is typed exactly. It refreshes all resource sections after a
successful or already-absent result. Refresh failures are isolated by section;
previous data is retained and labelled stale when possible.

## Provider limits and credentials

- A network volume may grow only. Equal-size and shrink requests fail before a
  provider write. Deleting a volume deletes its data and is irreversible.
- RunPod has no registry-auth update. Replace explicitly deletes the old auth
  and creates a new one, so the identifier changes. If recreation fails, the
  response reports `registry_replace_partial_failure` with `changed: true`.
- Registry create/replace accepts `password_env`, not a credential value. The
  named environment variable is resolved only at the strict client boundary.
  Passwords and usernames are never returned or written to audit metadata.
- Pod and template environment values may be sent to RunPod, but sanitized
  resource projections return no values. Template projections expose keys only.
- Public Hub access is read-only catalogue discovery. It cannot deploy or
  publish a template.

REST integration grants `read` scope to `GET` operations in the admin namespace
and `server:admin` to mutations. MCP resource mutations are admin tools. Every
successful live write records the provider, operation, safe resource identity,
changed flag, and idempotency key through the existing audit convention.

## Failures and hermetic verification

Stable failures distinguish invalid input, not found, name conflict, grow-only
violations, unset credential references, provider timeout, provider error,
malformed provider response, unavailable audit storage, and registry replacement
partial failure. If the provider write completes but audit persistence fails, the
error is `audit_write_failed` with `changed: true`, so callers refresh rather than
retrying an unsafe create blindly. Provider response excerpts are bounded and redacted. Caller
cancellation propagates instead of being converted into a provider error.

Tests use strict recorded fixtures, `httpx.MockTransport`/RESPX, fake services,
and Textual Pilot. They do not call RunPod, read live credentials, mutate provider
resources, or perform Hub publication/deployment.
