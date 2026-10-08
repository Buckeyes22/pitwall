# Testing Strategy

Pitwall uses layered verification for unit behavior, database and Redis integration,
concurrency, security, API contracts, mutation resistance, packaging, containers, and
public-alpha release readiness. GitHub Actions is the only supported public CI platform.

## 1. Markers and isolation

Markers are registered in `pyproject.toml`.

| Marker | Purpose | External dependency |
| --- | --- | --- |
| *(none)* | Hermetic unit and contract tests | None |
| `integration` | Real PostgreSQL/Redis behavior | Test Compose stack |
| `property` | Hypothesis invariants | None |
| `chaos` | Fault-injection and degraded paths | None unless also `live` |
| `security` | Authentication and security regressions | None |
| `fuzz` | Schemathesis/Hypothesis fuzzing | None |
| `benchmark` | Micro-benchmarks | None |
| `slow` | Long-running load or mutation-related work | Varies |
| `release` | Public-alpha release envelopes | Exact `-m release` or `-m "release and not live"` selector |
| `live` | Real external-service calls | Explicit target selection and credentials |

Hermetic tests set placeholder values through `tests/conftest.py`; they must not call RunPod or
require local infrastructure. Integration tests are skipped unless their test DSNs are present. The canonical GPU
constraint SQL test (`tests/test_canonical_gpu_constraints.py`) is marked
`integration` and requires `PITWALL_TEST_DATABASE_URL`; it must never use the
application `DATABASE_URL` or enter the hermetic lane.
After callers select `-m live`, `--run-live`, a complete self-hosted live configuration, or an
explicit RunPod-live environment gate makes those selected items eligible to run.

`tests/conftest.py` adds a DNS guard for the real RunPod control-plane hosts. General
`--run-live` selection and the `PITWALL_SELFHOSTED_BASE_URL` /
`PITWALL_SELFHOSTED_API_KEY_ENV` pair do not lift it. Only a truthy `RUNPOD_LIVE`,
`PITWALL_RUN_LIVE`, or test-only `PITWALL_RUNPOD_LIVE` value authorizes RunPod control-plane
egress; accepted truthy values are `1`, `true`, `yes`, and `on`, case-insensitively. Recognized
false values such as `0`, `false`, `no`, and `off` retain the guard. V2 contract tests use minimal
schema facts captured from the official OpenAPI source and fake transports to cover strict request
fields, collection envelopes, every migrated resource/action, GPU/cloud fallback, argv quoting,
v1-only handling, errors, safe retries, redaction, and validation failures before a write. A
provider hostname escaping its fake fails before DNS resolution. No paid production countersign is
part of the hermetic suite.

The release fixture in `tests/release/conftest.py` enables release envelopes under either exact
marker expression, `-m release` or `-m "release and not live"`. It is not a broader selector:
`live` alone and compound expressions such as `release or live` still skip the tier. The CI
readiness job uses `-m "release and not live"` to deselect live cases. With `-m release`,
live cases remain subject to the explicit live-enablement fixture.

## 2. Verification tracks

| Track | What it proves |
| --- | --- |
| Unit and contract | Deterministic behavior, API envelopes, CLI dispatch, and regressions |
| Property | Pure-logic invariants across generated inputs |
| Integration | Migrations, repositories, transactions, Redis, backup/restore, and concurrency |
| API security | Auth scope matrix, body bounds, SSRF defenses, webhook controls, and redaction |
| API fuzz | Every generated OpenAPI operation avoids unexpected server errors |
| Chaos | Retry, outage, termination, and safe-degradation behavior |
| Mutation | High-risk cost, rate-limit, and lease-state oracles reject behavioral mutants |
| Release | Artifacts, operational journeys, strict audit, and publication policy |
| Operator live | Optional real-provider validation using operator-owned credentials and resources |

Production bug fixes require a regression test at the lowest meaningful layer. Database
correctness, locking, migrations, and restore behavior require real-PostgreSQL integration tests;
mock-only evidence is insufficient for those paths.

## 3. Local infrastructure

`docker-compose.testinfra.yml` provides PostgreSQL 16 on `127.0.0.1:5444` and Redis 7 on
`127.0.0.1:6380`. The integration fixtures apply the packaged SQL migrations in discovery order.

```bash
make up
make test-int
make down
```

Use a unique `COMPOSE_PROJECT_NAME` when multiple checkouts or CI jobs share one Docker daemon.

## 4. Security and API compatibility

The security chain includes:

- Bandit with a reviewed baseline;
- pip-audit over the frozen runtime dependency graph;
- deterministic detect-secrets review and drift detection;
- license allow/deny/review policy over the runtime graph;
- Semgrep registry rules plus the repository-local policy;
- security-marked regression tests and all-operation Schemathesis fuzzing;
- OpenAPI compatibility comparison against `docs/api/openapi-baseline.json`.

The Schemathesis test derives its operation set from the application schema. It exercises all 104
current operations, including protected and health routes, and fails on unexpected 5xx responses.
A second pass sends the admin secret to every secret-gated `/v1/admin/*` operation so their
handlers run, not only the 401 gate. Both passes use `derandomize=True`, so a run is
reproducible. See `14-security.md` section 12 for the doubles each handler runs against.
The committed OpenAPI compatibility gate separately rejects removal or incompatible mutation of
existing methods, parameters, request bodies, or successful response schemas.

```bash
make sec
make sec-semgrep
make sec-test
make sec-fuzz
make openapi-check
```

## 5. Mutation and performance

`make mutation-gate` runs mutmut over the configured high-risk pure-logic modules, exports CI
statistics, and requires at least an 85% kill score among covered mutants. `make bench` runs
micro-benchmarks; `make load-smoke` validates the load profile without calling a deployed service.
Actual load execution requires an operator-supplied `PITWALL_HOST` and remains an explicit action.

## 6. Coverage

CI maintains two coverage floors:

- the hermetic `test` job enforces 74%;
- `coverage-combined` appends the hermetic and integration lanes and enforces 77%.

These are minimum ratchets, not quality claims. Increase them as meaningful branch coverage grows;
do not lower them to land a change.

The combined lane also evaluates weighted line and branch floors for authorization, spending,
webhooks, leases, retention/deletion, R2 cleanup, and the worker boundary. The versioned policy is
`tools/ci/risk-coverage-policy.json`; a missing source match fails rather than silently dropping a
risk domain.

## 7. CI and release readiness

`.github/workflows/ci.yml` runs `lint`, `format`, `typecheck`, `docs`, `container-build`,
`compose-config`, `security-sast`, `security-secrets`, `security-fuzz`, `mutation-smoke`, `test`,
`dependency-compatibility`, `integration`, and `coverage-combined`. The mutation smoke lane is
diagnostic; the blocking mutation floor runs in
`release-readiness.yml`.

`.github/workflows/release-readiness.yml` provides blocking security, mutation, and hermetic
jobs. The hermetic job validates the strict RunPod audit, exact release-marker suite,
candidate policy, OpenAPI compatibility, artifacts, and combined coverage. The workflow neither
requests provider credentials nor enables live execution. Live tests are separate operator-side
checks using credentials and resources owned by the deploying operator.

The release workflow publishes only after readiness succeeds. It rebuilds Python artifacts twice,
checks byte identity, validates wheel/sdist contents, signs artifact/image provenance through
GitHub attestations, generates SBOMs, scans images, and promotes the exact tested digests. The
PyPI and TestPyPI are outside the current release workflow.

## 8. Commands

```bash
# Fast hermetic lane
uv run pytest -q -m "not integration and not slow"

# Real PostgreSQL/Redis integration lane
make up
make test-int
make down

# Security and mutation
make sec-test
make sec-fuzz
make mutation-gate

# Exact release envelopes and strict audit
uv run pytest -q -m release tests/release
uv run python -m pitwall.audit.checks --strict

# Full local public-alpha rehearsal (requires DATABASE_URL and REDIS_URL)
scripts/release/run-alpha-readiness.sh
```

See `docs/operator/release-testing-checklist.md` for the human-run checks and
`docs/release/external-release-gates.md` for the short publication checklist.

## 9. Production routing evidence

ROUTE-01 uses fake static adapters for all execution tests. Unit/property cases prove canonical
plan bytes, priority compatibility, weighted selection, tie-breaking independent of repository
order, capability/model/stream rejection, missing-signal policy, guardrail and budget ordering,
synchronous fallback reservation and attempt audit, atomic admission/plan initialization,
single-connection submit/cancel serialization, ambiguous async-write safety, provider-neutral
status and bounded results/events, exact-zero truth-up only before provider invocation or after a
proven success chain containing only already-paid active-lease attempts, and concurrent snapshot
consistency. Explicit prepared-payload tests prove adapters can hand off one
already-recorded inspection without a duplicate decision, while normal service calls still record
their own inspection and reject post-inspection payload mutation. REST, MCP, CLI, config, and TUI
tests compare the shared plan identity and safe semantic fields without credentials or live calls.

`tests/integration/test_workload_route_plan_persistence.py` is the real-PostgreSQL proof for
migration `0031` and JSONB/NUMERIC repository fidelity. It remains integration-marked and runs only
against the isolated test DSN. No ROUTE-01 test lifts the RunPod DNS guard or needs a provider
credential (`tests/routing/test_production_routing.py`,
`tests/integration/test_workload_route_plan_persistence.py`).


## Stream cancellation test diagnostics

The OpenAI fallback cancellation test keeps its one-second readiness deadline and
all upstream-close, terminal-state, cost, trace, and delivered-byte assertions.
Its readiness helper races the stream event against request completion, reporting
an early HTTP status or propagating the original request exception instead of
hiding it behind a timeout (`tests/api/test_openai_proxy_fallback.py`). A real
readiness timeout reports the pending request stack location; both the event waiter
and owned request are cleaned up on failure. Regression cases cover early status,
early exception, a pending request, and cleanup through the cancellation test itself.
These diagnostics do not retrospectively establish the cause of the original RA-050
failure. Preserve its evidence and keep that historical root cause unresolved until
an instrumented failure or equivalent causal evidence establishes it. For randomized
suite investigations retain an explicit `--randomly-seed` and verbose output.

## 10. Journeys and the surface matrix

The journeys in [the user-journey catalog](../operator/user-journey-catalog.md) are the end-to-end
lane: each drives a real entry point (CLI, REST server, MCP stdio server, console, shims, installed
artifacts) and asserts the oracle its catalogue row states. The matrix makes that lane
exhaustive over what the product exposes.

**The denominator is discovered, not listed.** `tools/release_acceptance/discovery.py` walks the
tree and reports every surface: REST operations, MCP tools, CLI commands and arguments,
configuration keys, console views, bindings, buttons, and events, Agent Routing providers,
adapters, shims, channel tools, and plugins, gateway and workbench surfaces (Python, plus the packaged Pi extensions), and operations
(Dockerfiles, Compose services, networks, volumes, migrations, release scripts).
`release_acceptance/denominator.json` pins the count per family;
`tests/release_acceptance/test_denominator.py` fails when a surface appears or disappears, and the
pin changes only with a stated reason. Constructs discovery cannot read statically are issues,
each reviewed in `release_acceptance/discovery-review.json` and kept current by
`tests/release_acceptance/test_discovery_review.py`.

**A new surface is bound to the test that proves it.** Families with a fixture file bind by rule
in `tools/release_acceptance/bind_surfaces.py` (MCP tools to J34, REST operations to J35, CLI
commands and arguments to J36, configuration keys to J37, migrations to J43, shims to J40); every
other surface is listed with its test node, framework, proof lane, and oracle in
`release_acceptance/surface-test-map.json`. After adding a surface's test and, where needed, its
map entry, regenerate the bindings; the command fails naming any unmapped surface or any test node
the static index cannot find:

```bash
uv run --frozen python -m tools.release_acceptance.bind_surfaces
```

`tests/release_acceptance/test_surface_bindings.py` runs in the hermetic lane and fails when a
surface is unmapped or the committed bindings differ from what the rules and map produce, so a
new route, tool, command, or key without a bound test fails CI.

A binding records its test file's hash, and the matrix refuses a binding whose file changed.
Regenerating re-hashes the rule and map bindings: rerun it after reading the edited test against
the rule or map entry that binds it. A hand-reviewed binding (one written directly in
`reviewed-bindings.json`) whose test changed is listed instead, and re-hashed only with
`--accept-reviewed` once the test has been re-read.
A binding whose test file or test function is gone (deleted or renamed) stops the run and names
the binding: point its `test_node_id` at the test that now covers the surface, in
`reviewed-bindings.json` and in the per-family record that lists it, re-read that test, and rerun
with `--accept-reviewed`.

**The command that proves coverage** is the full journey run. After the journeys it runs every
bound test once (`scripts/release/run_bound_tests.py`: pytest by lane, the Agent Routing unittest
runner, vitest for the Node packages) and then `tests/release/test_matrix_complete.py`, which
assembles the matrix with the run's collection receipt and fails unless every surface is bound
and every bound node passed:

```bash
make up
DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test \
REDIS_URL=redis://127.0.0.1:6380/0 \
  bash scripts/release/run-user-journeys.sh
```

The run ends with `matrix: <surfaces> surfaces, <cases> bound cases, 0 unbound, 0 failing`.
