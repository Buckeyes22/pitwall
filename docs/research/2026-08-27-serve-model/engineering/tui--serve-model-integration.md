# TUI integration for `serve-model`

Status: research complete 2026-08-27. Repository observations use `path:line`; framework claims are backed by the official Textual sources collected below.

## Part 1 — existing operator console

### Shell, navigation, and direction

`PitwallApp` is explicitly a read-only Textual shell (`src/pitwall/tui/app.py:21-24`). It gives the app title/subtitle to Textual's `Header`, declares global `q`/`Ctrl-C`, `o`, `p`, `l`, `c`, `e`, and `a` bindings, and therefore Footer displays the currently applicable bindings (`src/pitwall/tui/app.py:24-35`). Each screen composes its own `Header(show_clock=True)` and `Footer`, with a left navigation `ListView` and a main vertical panel; it is not yet a single shared navigation widget (`src/pitwall/tui/providers.py:95-110`; `src/pitwall/tui/leases.py:166-186`; `src/pitwall/tui/cost.py:170-189`; `src/pitwall/tui/resources.py:283-316`; `src/pitwall/tui/operations.py:522-565`).

The inline app CSS makes every screen vertical, fixes the shell navigation pane at width 24, applies `$primary` border and `$panel` background, uses `$accent` metric borders, and colours errors `$error`; no named custom theme is registered (`src/pitwall/tui/app.py:36-175`). On mount it installs Overview, Providers, Cost, Resources, and Operations, pushes Overview, and defers Leases installation until `l`; actions then switch registered named screens (`src/pitwall/tui/app.py:199-269`). The package merely re-exports `PitwallApp` (`src/pitwall/tui/__init__.py:1-7`). `dashboard` accepts no arguments and runs the app synchronously through `PitwallApp().run()` (`src/pitwall/cli.py:194-204`). There is no `_parse_dashboard_args` symbol in this repository revision; `cmd_dashboard` creates and immediately parses its local `ArgumentParser` instead (`src/pitwall/cli.py:194-201`).

The roadmap is more ambitious: k9s-style navigation including `:`, `/`, `?`, `q`, `r`, persistent active-key footer, read-only-by-default and type-to-confirm destructive operations, while sharing the CLI service layer (`docs/FEATURE-ROADMAP.md:186-214`). The implemented phase remains a small read-only subset: the SDLC document says no stop/renew/terminate/provider-mutation widget exists in Leases (`docs/sdlc/18-cli.md:551-577`).

### Sources, refresh, errors, and test seam

Every view follows a small view-model boundary: an async `*Source` Protocol with one `load_*` operation, immutable `*Snapshot`, a `Static*Source` with `load_count` for hermetic tests, and a production source. Examples include `ProvidersSource` / `StaticProvidersSource` / `RegistryProvidersSource` (`src/pitwall/tui/providers.py:23-81`), `LeasesSource` / `StaticLeasesSource` / `PostgresLeasesSource` (`src/pitwall/tui/leases.py:25-139`), `CostSource` / `StaticCostSource` / `PostgresCostSource` (`src/pitwall/tui/cost.py:38-151`), Resources (`src/pitwall/tui/resources.py:48-241`), Operations (`src/pitwall/tui/operations.py:50-435`), and Overview (`src/pitwall/tui/overview.py:31-203`). They load once in `on_mount` and again only on per-screen `r`; no periodic refresh timer/cadence is installed (`src/pitwall/tui/providers.py:112-129`; `src/pitwall/tui/leases.py:188-209`; `src/pitwall/tui/app.py:21-298`).

The app injects sources and a lazy async pool factory; `default_pool` calls `pitwall.db.get_pool`, and DB-backed sources acquire it only when their view resolves or refreshes (`src/pitwall/tui/app.py:177-197`; `src/pitwall/tui/app.py:271-297`). Each screen clears its inline error then catches `Exception` during loading rather than crashing the app (`src/pitwall/tui/cost.py:191-208`). `source_failure_message` normalizes whitespace, exposes a truncated 200-character reason, and returns `"<prefix>: <reason>"` (`src/pitwall/tui/errors.py:5-18`); this is more informative than the stale SDLC prose that calls messages generic.

Pilot tests run `async with app.run_test(...) as pilot`, press global/view keys, pause, then inspect widgets and `load_count`; the files use injected static sources and `pytest.mark.anyio` (`tests/tui/test_leases_screen.py:100-206`; `tests/tui/test_providers_screen.py:60-110`; `tests/tui/test_cost_screen.py:151-235`; `tests/tui/test_cli_dashboard.py:8-17`). They also pin empty states, exact row/summary formatting, refresh, and error rendering; overview/leases contain property checks for normalized display state and overview pins confirmation mappings (`tests/tui/test_overview.py:46-101`; `tests/tui/test_leases_screen.py:76-97`).

### Current screen inventory

| Screen | Rendering and present data |
| --- | --- |
| Overview | `Static` metric cards summarise registered/enabled provider health, lease counts/state, cost daily entries, and recent workloads from repositories/reporting (`src/pitwall/tui/overview.py:205-385`). |
| Providers | A fixed-width Rich-text table held in `Static`, populated from the process provider-plugin registry IDs; each row currently has only ID, `registered`, and `tagged` pricing (`src/pitwall/tui/providers.py:68-81`; `src/pitwall/tui/providers.py:95-159`). |
| Pods / Leases | `DataTable` with lease, pod, provider, state, readiness, expiry, accrued cost; Postgres query excludes terminal `stopped`, `failed`, `expired` leases and derives readiness from persisted JSON signals (`src/pitwall/tui/leases.py:25-164`; `src/pitwall/tui/leases.py:166-249`). |
| Cost | `Static` summaries/tables for burn-rate forecast, chargeback sub-budgets, and what-if projection from DB cost layers (`src/pitwall/tui/cost.py:101-151`; `src/pitwall/tui/cost.py:170-294`). |
| Resources | Four fixed-width `Static` tables: RunPod endpoints, Hub templates, network volumes, and registry auths, supplied by existing read-only client list APIs (`src/pitwall/tui/resources.py:184-241`; `src/pitwall/tui/resources.py:283-389`). |
| Operations | `Static` tables/summaries for catalog, jobs, resilience, routing, policy, and shadow-mode autopilot (`src/pitwall/tui/operations.py:341-514`; `src/pitwall/tui/operations.py:522-682`). |

### Mutability and confirmation

`confirmation.py` is a policy stub, not an interaction implementation: its enum has NONE, CONFIRM, TYPE_TO_CONFIRM, DOUBLE_CONFIRM; it maps refresh to NONE, renewal to CONFIRM, termination to TYPE_TO_CONFIRM, provider disable to DOUBLE_CONFIRM, and defaults unrecognised actions to CONFIRM (`src/pitwall/tui/confirmation.py:8-28`). No screen imports this module or creates a button/modal, so no write action exists today (`src/pitwall/tui/confirmation.py:1-34`; `src/pitwall/tui/app.py:1-298`). Tests pin the four relevant mappings, notably termination/provider-disable (`tests/tui/test_overview.py:67-70`).

### Textual baseline

The project pins `textual>=8.2,<9` and already relies on `App`, `Screen`, `Binding`, `Header`, `Footer`, containers, `Static`, `Label`, `ListView`/`ListItem`, `DataTable`, and `run_test`/Pilot (`pyproject.toml:44-72`; imports above). Version 8.2 therefore must be the compatibility floor for any new widget.

## Part 2 — capability integration

### Inputs and the catalogue boundary

The implemented service defines `serve_model(pool, request) -> ServeResult`, returning capability, lease ID, expiry, model ID, proxy base URL, workload/template/provider IDs; it creates or reuses a `pod_lease` provider whose config includes image, allowed GPU class, TTL, environment, start command, port 8000, and active rate (`src/pitwall/serve.py:181-194,1080-1579`). The console uses the same service through its thin `ServeActionSource` adapter rather than a second launch implementation (`src/pitwall/tui/serve.py:27-70`).

The examined dossiers establish a consistent YAML-front-matter contract: model/vendor/family/license and `gated`; architecture; weights; `serving.recommended_engine`, image and `docker_start_cmd`; hardware native/quantized VRAM floors and `recommended_gpu_classes`; Pitwall capability/served-name example; and `confidence.overall`/notes. For example, Qwen’s 27B dossier selects vLLM and 80-GB classes, while the GGUF variant selects llama.cpp and a 24-GB quantized floor (`../Qwen--Qwen3.8-27B.md:1-78`; `../unsloth--Qwen3.8-27B-GGUF.md:1-99`). Kimi and MiniMax dossiers use the same top-level shape (`../moonshotai--Kimi-K3.md:1-75`; `../MiniMaxAI--MiniMax-Music3.md:1-78`).

Recommendation: add a read-only **Model catalog** screen backed by `ModelCatalogSource.load_catalog() -> ModelCatalogSnapshot`. It should list `served_model_name/model_id`, engine, format/quant, minimum VRAM, recommended canonical GPU classes, confidence badge, licence, and gated/public status in a `DataTable`; pressing Enter can push a `ModelDetailScreen` whose `MarkdownViewer` renders the dossier body. `MarkdownViewer` is available well before this project’s minimum (added in Textual 0.11) and accepts Markdown text with optional table of contents, so it is compatible with `textual>=8.2,<9` ([Textual MarkdownViewer](https://textual.textualize.io/widgets/markdown_viewer/)). Prefer `Markdown` where no history/TOC is wanted; it accepts a Markdown string and uses a GFM-like parser by default ([Textual Markdown reference](https://textual.textualize.io/widgets/markdown_viewer/)). Do not make external dossier files an unversioned runtime dependency: integrate the selected documents into `docs/models/*.md` (or a packaged catalogue resource) first, and resolve a configured repository-relative directory only in the source adapter.

Pitwall’s seed loader imports PyYAML opportunistically, falls back to its own simple YAML parser when unavailable, and only promises mapping-root seed documents (`src/pitwall/seed.py:220-239`; `src/pitwall/seed.py:549-671`). PyYAML is absent from normal `[project.dependencies]` (`pyproject.toml:25-40`). A model reader should therefore be deliberately smaller than generic YAML: require opening `---`, locate the closing `---`, parse only the known scalar/map/list front-matter subset via an extracted/tested safe reader (or add a pinned PyYAML runtime dependency if full YAML support is genuinely required), retain the body verbatim, reject malformed/unexpected shape with a per-dossier inline error, and never execute YAML tags. Reusing `_load_seed_payload` directly would be an inappropriate private coupling because it consumes a whole document and requires mapping root semantics (`src/pitwall/seed.py:220-239`).

### Hardware fit screen

Add a **Model × GPU fit** view, entered from the catalogue. Rows should be a model variant crossed with canonical GPU class and contain `floor` (native or selected-quant VRAM), recommended/not-recommended, GPU memory where catalogued, live availability/stock, price type/value, datacenter, and freshness. This must distinguish deterministic *dossier fit* from volatile *market feasibility*: recommended GPU classes are suitability claims, not capacity or an admission guarantee.

The existing live substrate is adequate but should be behind a `HardwareFitSource`: `RunpodGraphQLClient.gpu_types()` returns current type data and `datacenters()` returns GPU lanes (`src/pitwall/runpod_client/graphql.py:391-400`); `get_bid_price` exposes minimum bid, uninterruptible price, stock status, and available GPU counts for a GPU/DC/count (`src/pitwall/runpod_client/graphql.py:402-450`). `GpuDiscoveryService` normalizes GPU memory, pricing, datacenter IDs, availability, status/count, TTL-caches for 60 seconds, and serializes refreshes behind an async lock (`src/pitwall/runpod_client/discovery.py:25-47`; `src/pitwall/runpod_client/discovery.py:121-145`). The older availability cache is a five-minute boolean cache keyed by DC, canonical GPU name, cloud type, and count—not a pricing source (`src/pitwall/runpod_client/availability.py:1-8`; `src/pitwall/runpod_client/availability.py:50-94`). Reuse the discovery service/facade, not raw GraphQL from widgets; normalize display names to the canonical GPU names validated for serves.

The concurrently produced research is present: `engineering/runpod--gpu-catalog.md` and `engineering/wiring--pod-to-model.md`. The latter observes that cache/volume/engine settings are not yet model-record plumbing and recommends a model record with immutable revision/quant/bytes/engine, while generic launch primitives already exist (`engineering/wiring--pod-to-model.md:88-147`). The fit screen should expose this as *cache status unavailable* until that model registry and warm-cache inventory exist, not fabricate cache-hit information.

### Safe serve interaction

Recommended exact flow:

1. Select model/variant in the catalogue, then select only dossier-recommended GPU classes (allow an explicit override marked unsupported), TTL, DC, and rate. Pre-fill image, served model ID, disk, environment and start arguments from the chosen dossier; present gated/token and unverified fields as warnings.
2. `Preview` invokes `pitwall.serve.serve_model(..., dry_run=True)` through `ServeActionSource.preview()`, and renders the returned launch plan, provider/capability names, chosen engine/image/args (with secret values redacted), TTL reservation/cost estimate, availability timestamp, and the fact that no pod will be created. The implementation stops the dry-run before paid launch (`src/pitwall/serve.py:1446-1481`; `src/pitwall/tui/serve.py:53-59,243-252`).
3. Only a successful, still-current preview enables `Launch`. A modal requires the operator to type the complete capability name (or a supplied fixed phrase), repeats the amount/TTL and model/GPU, then calls the same service with `dry_run=False`. Action ID `serve.launch` maps to `TYPE_TO_CONFIRM`: it creates a billable pod lease and reserves the whole TTL, making it at least as consequential as the existing `lease.terminate` policy (`src/pitwall/tui/confirmation.py:17-23`; `src/pitwall/tui/serve.py:124-205`). `CONFIRM` is too weak for a paid resource; `DOUBLE_CONFIRM` should remain reserved for provider-wide disable.
4. Put the call in a Textual async worker (`run_worker(..., exclusive=True, exit_on_error=False)`), render an indeterminate progress/status panel, and refresh the lease source until terminal/active. Show `creating → waiting_runtime → waiting_probe → active`, states validated by the existing lifecycle suite (`tests/sql/test_lease_state_transitions.sql:224-247`; `src/pitwall/core/enums.py:99-106`). Workers exist specifically so network or long work does not block event handling; `exclusive=True` cancels obsolete workers in a group ([Textual Workers](https://textual.textualize.io/guide/workers/)). Handle exceptions as the same safe inline source/action failure and retain the preview for retry.
5. On ACTIVE, surface the returned `proxy_base_url`, `expires_at`, capability, lease ID and served model ID, with copyable text; do not show the direct RunPod URL. The service result and TUI launch flow expose those values (`src/pitwall/serve.py:181-194`; `src/pitwall/tui/serve.py:270-276`).

For the waiting/progress panel, `set_interval` can refresh at a bounded operator-facing interval and returns a stoppable timer ([Textual message pump](https://textual.textualize.io/api/message_pump/); [Timer](https://textual.textualize.io/api/timer/)). It must stop when screen unmounts/leaves, launch finishes, or user cancels. Do not call blocking sync RunPod/HTTP or synchronous dossier file parsing in handlers: use an async source/worker, or a thread worker for truly blocking reads; Textual warns that thread workers must marshal UI calls with `call_from_thread` ([Textual Workers](https://textual.textualize.io/guide/workers/)).

### Existing views after serve

**Leases.** Extend `LeaseDisplayRow` and the query/snapshot with served model ID, `proxy_base_url` (derived/display-safe), `active_pod_id`, `openai_proxy_port`, expiry countdown, and an explicit dead-lease disposition. Today the query selects only lease/pod/provider/state/expiry/readiness/cost and deliberately filters all terminal states (`src/pitwall/tui/leases.py:91-124`); therefore a dead/expired serving provider cannot currently be seen. Add either a “show terminal/dead” toggle or a separate recently-closed section, include `expired`/failed relevant rows, and label **dead—proxy unavailable** where provider has been disarmed. This aligns with the implementation: readiness arms validated `active_pod_id`/`active_lease_id`, teardown removes them and marks the route unavailable until a new serve re-arms it (`src/pitwall/api/leases/launch.py:565-576`; `src/pitwall/api/leases/teardown.py:216-230`; `src/pitwall/routing/openai.py:320-345`). Countdown requires a local timer render only; the source should retain absolute UTC `expires_at`.

**Providers.** Do not replace `RegistryProvidersSource`: it reports installed provider plugins, not persisted providers (`src/pitwall/tui/providers.py:68-81`). Extend with a separate provider-state source/repository query or a new Serve Providers subsection: provider ID/capability, `pod_lease`, served model, health, armed boolean, `active_pod_id`, port, expiry and dead/unhealthy state. That gives the operator a correct answer to “is this serve provider armed?” rather than conflating registration with operational health.

**Operations.** The implemented Operations screen is read-only and its catalog is repositories plus summaries; it has no stop or renew control (`src/pitwall/tui/operations.py:341-479`). The API implements `POST /v1/leases/{id}/stop` and renew, and re-serving can re-arm a provider (`src/pitwall/api/routes/leases.py:243-310`; `src/pitwall/serve.py:1502-1579`). If enabled later, add `serve.renew` at `CONFIRM` and `serve.stop` at `TYPE_TO_CONFIRM`, both previewing effect/cost and calling a service-layer function—not direct SQL or RunPod client. These are phase-3 actions; catalog and fit remain read-only.

### Data-plumbing and testability

Screens must call service-level adapters: `ModelCatalogService`/front-matter reader, `GpuDiscoveryService`, `pitwall.serve.serve_model`, and future `renew_lease`/`stop_lease` service functions. They must not reach `pitwall.routing`, `pitwall.runpod_client`, budget gates, repositories, or SQL directly from widget event handlers. There is no TUI-specific AST guard today: the analogous MCP guard explicitly forbids handler imports of RunPod, routing, and cost-estimator internals (`tests/mcp/test_no_business_logic_guard.py:1-33`; `:89-137`). Add `tests/tui/test_no_business_logic_guard.py` with an allow-list appropriate for sources (or, stronger, make widgets import only TUI source interfaces) to preserve that boundary.

The app already has the necessary injection seam: pass `catalog_source`, `hardware_fit_source`, `serve_action_source`, and enhanced leases/providers sources into `PitwallApp`; lazy sources receive the app’s cached `pool_factory`, whose default uses `pitwall.db.get_pool` (`src/pitwall/tui/app.py:177-197`; `src/pitwall/tui/app.py:271-297`). `StaticModelCatalogSource`, `StaticHardwareFitSource`, and `StaticServeActionSource` should provide snapshots/results and call counts just like current sources (`src/pitwall/tui/overview.py:68-77`; `src/pitwall/tui/leases.py:79-88`). A fake action source should record preview/launch requests and return scripted state/results; it must never construct real client/pool connections in a Pilot test.

## Part 3 — implementation sketch

### Files and incremental order

1. Create `src/pitwall/models/catalog.py` — typed dossier/front-matter reader, validation, body retention, canonical GPU class normalization; create `src/pitwall/tui/models.py` — read-only `ModelCatalogSource`, snapshot/rows, `ModelCatalogScreen`, detail screen with `MarkdownViewer`, static fake.
2. Modify `src/pitwall/tui/app.py` — dependency injection, named screen installation, `m` key/navigation only after catalogue exists; add CSS for tables/detail/progress. Modify `src/pitwall/tui/__init__.py` only if a public screen/source export is desired.
3. Create `tests/tui/test_models_screen.py` — malformed/front-matter error, row ordering/badges, Markdown detail, `m` navigation, refresh/error, and static-source count with `run_test`/Pilot; add miniature fixture dossiers under `tests/fixtures/models/`.
4. Create `src/pitwall/tui/hardware_fit.py` — derived dossier floor × live discovery snapshot, staleness and unavailable states, static fake; modify `app.py` for `g`/from-model navigation. Create `tests/tui/test_hardware_fit_screen.py` — recommended/floor classification, stale/empty market states, table rows, refresh/error, Pilot navigation; do not use live GraphQL.
5. After `pitwall.serve` lands, create `src/pitwall/tui/serve.py` — request/preview/result presentation and `ServeActionSource` thin adapter; create a confirmation modal component and add `serve.launch` mapping in `confirmation.py`. Modify `app.py` for a guarded Serve screen; create `tests/tui/test_serve_screen.py` for dry-run-only first, then type-to-confirm gating, no launch without preview, request assembly/redaction, worker/progress scripted states, success URL/expiry and failure rendering.
6. Modify `src/pitwall/tui/leases.py` and `tests/tui/test_leases_screen.py` — serving columns, dead state/recently terminal view and countdown rendering. Modify `src/pitwall/tui/providers.py` and its tests — persisted serve-provider health/armed facts alongside plugin registry (or make a dedicated subsection/screen). Add stop/renew UI/tests only after service interfaces are stable.
7. Create `tests/tui/test_no_business_logic_guard.py` — enforce widget/service boundary. Update `docs/sdlc/18-cli.md` TUI inventory, bindings and tests; update the `Operator dashboard` row in `docs/capability-matrix.md` to distinguish read-only catalogue/fit from guarded serve preview/launch; update `docs/FEATURE-ROADMAP.md` when launch policy is decided.

The slice order is intentionally catalogue read-only → fit read-only → preview → confirmed launch → leases/provider lifecycle actions. It protects the current roadmap’s read-only default while making the data model and dossier quality observable before money can be spent (`docs/FEATURE-ROADMAP.md:186-214`).

### Design questions for the maintainer

1. Should the dashboard ever launch paid leases, or should it remain preview-only and hand the final command to CLI/REST? This changes whether the confirmation modal and launch action are in scope.
2. Will dossiers live in versioned `docs/models/`, a packaged resource, or a database/catalog service? The current `../` research location is unsuitable as a deployed dashboard dependency.
3. Is a minimal restricted front-matter grammar acceptable, or should PyYAML become a pinned runtime dependency? What schema/version and failure policy are required?
4. Are confidence levels merely badges, or hard eligibility gates (for example, forbid low/unverified dossiers from launch)? Who owns overriding a GPU outside `recommended_gpu_classes`?
5. What exact rate source and cost estimate should preview show when a dossier example has a rate but live market pricing differs? Is a configured/operator rate mandatory before enablement?
6. Which cache/volume readiness facts will the eventual model registry expose, and should the fit/preview screen require a warm cache for large or gated models?
7. May a TUI expose/copy a proxy endpoint only, as recommended, or should it display the derived RunPod pod/port facts for diagnosis? What redaction applies to model start arguments and environment?
8. Does “dead lease” mean a terminal lease only, an unarmed/unhealthy provider, or both? How long should historical/dead rows remain visible?
9. Should the repeated per-screen navigation `ListView` be consolidated before adding three views, and are the roadmap’s `:`, `/`, and `?` now required acceptance criteria?

## Sources

- [Textual MarkdownViewer](https://textual.textualize.io/widgets/markdown_viewer/) — `MarkdownViewer` availability, Markdown rendering and TOC — accessed 2026-08-27.
- [Textual Workers](https://textual.textualize.io/guide/workers/) — `run_worker`, exclusive work, non-blocking network work, thread/UI constraints — accessed 2026-08-27.
- [Textual message pump](https://textual.textualize.io/api/message_pump/) — `set_interval`/`set_timer` contract — accessed 2026-08-27.
- [Textual Timer](https://textual.textualize.io/api/timer/) — timer pause/resume/stop semantics — accessed 2026-08-27.
- Repository paths cited inline — existing shell, sources, serving design, and test contracts — accessed 2026-08-27.

## Open questions

The nine maintainer decisions in Part 3 are open; no implementation should silently choose a persistent dossier location or authorize dashboard spending.
