# CLI Subsystem

## 1. Purpose & Scope

The CLI subsystem is the top-level command-line interface for the Pitwall application. It is invoked as `pitwall` after installation or as `python -m pitwall` from the import package.

The command groups covered in this chapter are:

| Group | Responsibility |
|---|---|
| `db` | Database lifecycle (migrations, schema reset, status) |
| `retention` | Encrypted bounded archive and purge lifecycle |
| `init` | Guided local onboarding for a first capability and provider |
| `create-capability` | Create or update capabilities by flags or YAML/JSON spec |
| `seed` | Apply capability/provider seed files or directories |
| `config` | Validate boot-time configuration for a service |
| `register-template` | Register a RunPod template and cache its ID in the DB |
| `terminate-pod` | Terminate a single RunPod pod by ID with optional post-terminate verification |
| `register-endpoint` | Register a RunPod Serverless endpoint as a Pitwall provider |
| `set-provider-health` | Mark a provider `healthy`, `unhealthy`, `unknown`, or `hibernated` for routing |
| `setup` | First-run RunPod credential, endpoint-key, and Claude plugin setup for personal serving |
| `serve` | Launch or replay an OpenAI-compatible model-serving pod lease |
| `status` | Show what a personal pod is running and when it ends, or list registry leases |
| `stop` | Terminate a personal pod and remove its route, or stop a registry lease |
| `leases` | Read-only list of active pod leases |
| `models` | List, inspect, and price packaged model catalogue dossiers |
| `warm-volume` | Warm a RunPod network volume with the exact catalogue serve launch, then tear it down |
| `cost` | Read persisted daily cost summaries and per-workload cost detail |
| `burn-rate` | Read the monthly budget burn-rate forecast from persisted daily rollups |
| `guardrails` | Inspect pre-spend guardrail status or preview one JSON payload |
| `provider-ops` | Read safe provider descriptors, health, and bounded availability probes |
| `volume-files` | Bounded RunPod network-volume S3 transfers and pod-log reads |
| `dashboard` | Launch the Textual operator console with guarded model launch |
| `doctor` | Print an installation readiness report: install, config, services, and spend controls |
| `mcp serve broker` / `mcp serve channel` | Start the broker MCP server or the orchestrator channel MCP server over local stdio |
| `mcp relay` | Relay stdio to an MCP server command and restart it when it exits |
| `mcp install` | Register the Pitwall MCP server with Claude Code, Codex, and/or OpenCode |
| `mcp uninstall` | Remove the Pitwall MCP server registration from a coding-agent harness |
| `budget` | Show or change the runtime budget limits (audited; no restart) |
| `runpod`, `runpod-onboard` | RunPod account resource controls; plan and apply resumable onboarding |
| `routing` | Preview and operate routed jobs |
| `gateway`, `quotas` | Sync, inspect, serve, and doctor the free-tier gateway; show the free-pool quota burn-down (`pitwall.cli.gateway`) |
| `usage` | Subscription usage for every routed plan; `usage serve` publishes it (`pitwall.cli.usage`) |
| `agents` | Agent Routing: install, dispatch, profiles, workflows, runs, doctor (`pitwall.agents.cli`; see [23 — Agent Routing](23-agent-routing.md)) |
| `workbench` | Launch Pi under a workbench profile and the tools around it (`pitwall.cli.workbench`; see [25 — Pi Workbench](25-pi-workbench.md)) |

`serve --capability <name>` warms a registered self-hosted provider without launching a
pod or creating a lease; the response identifies `provider_kind: self_hosted`.

`serve`, `status`, `stop`, and `setup` are personal-first verbs implemented in
`pitwall.cli.personal`, except that `pitwall.cli.serve_model:cmd_serve` routes
`serve --plan-only` directly to the catalogue planner before backend selection. Other `serve`
invocations plus `status` and `stop` call `select_backend()`
(`src/pitwall/personal/backend.py`), which reads `[personal] backend` from `pitwall.toml`
(`PITWALL_CONFIG_FILE`, else `./pitwall.toml`) and returns `"personal"` unless the file sets
`backend = "registry"`. `DATABASE_URL` alone never selects the registry backend. Any other value,
or a `[personal]` table that is not a table, raises `ConfigFileError`; an unreadable file or invalid
TOML raises it too, naming the file and the TOML line and column. On the registry backend they run
the `serve` behaviour documented below, `leases list`, and `leases stop <id>`, respectively. On
the personal backend they launch, list, and stop a pod with a best-effort self-termination
safeguard directly against RunPod with no database — a completely different flag set from the
registry backend, documented separately below. `setup` does not branch on the backend, and ends
with the line `Backend: <name> (<where>; set [personal] backend in pitwall.toml)` so the active
backend is never implicit. The end-to-end personal-serving workflow is in
[Personal serving](../operator/personal-serving.md).

If setup input closes before a confirmation answer, the CLI cancels with exit 2
and a concise diagnostic. EOF never authorizes the pending action. This also
applies when the dashboard invokes first-run setup.

The `retention run` command reports invalid retention bounds or missing archive
encryption configuration on stderr with exit 1, without a traceback, and closes
its database pool. Dry-run does not require an archive encryption key.

The live-call gate (`is_live`, `require_live`) is test support, not part of the CLI: it lives in `tests/support/live.py`.

---

## 2. Components

### `src/pitwall/__main__.py`

**Responsibility:** Entry-point for `python -m pitwall`. Calls `cli.main()` and exits with its integer return code.

**Key content:**
```python
from pitwall.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

**Invariant:** Exits with `SystemExit(code)` where `code` is the integer return value of `cli.main`.

---

### `pitwall.cli.output`

**Responsibility:** Rich-based output layer shared by all CLI commands. Provides table, panel, and JSON renderers with a unified API so that commands do not emit bare `print()` calls.

#### `Output` class

```python
class Output:
    def __init__(self, json_mode: bool = False) -> None: ...
```

- `json_mode=False` (default): renders human-friendly tables and panels via `rich.console.Console`.
- `json_mode=True`: suppresses all Rich rendering and accumulates structured data; `emit()` writes a single JSON object to stdout.

Key methods:

| Method | JSON mode | Rich mode |
|---|---|---|
| `print(msg, *, soft_wrap=False)` | no-op | plain stdout line |
| `print_table(title, columns, rows)` | records list of dicts | `rich.table.Table` |
| `print_panel(content, title, border_style)` | records panel metadata | `rich.panel.Panel` |
| `print_error(message)` | sets `"error"` key | red error panel to stderr |
| `print_warning(message)` | appends to `"warnings"` list | yellow warning panel to stderr |
| `print_success(message)` | sets `"success"` key | green success panel to stdout |
| `add_json(key, value)` | adds key to result dict | no-op |
| `set_json(data)` | replaces result dict | no-op |
| `emit()` | dumps JSON to stdout | no-op |

All commands instantiate `Output(json_mode=getattr(args, "json", False))` at the top of their handler so that `--json` is supported uniformly.

`print(msg, *, soft_wrap=False)` takes a keyword-only `soft_wrap` flag. `soft_wrap=True` never
breaks the line at the console width, so a reader can copy it verbatim; `pitwall doctor` and
`pitwall mcp install`/`uninstall` pass it for every line, because both print config paths, shell
commands, and config snippets a user or agent must copy exactly.

---

### `src/pitwall/tui/`

**Responsibility:** Textual-based operator console. It binds ten views in `src/pitwall/tui/app.py`: Overview, Providers, Leases, Models, Serve, Pods, Routes, Cost, Resources, and Operations. Overview, Leases, Routes, and Cost are read-only. Every mutation sits behind a preview and an exact typed confirmation: the Models catalogue launch (mandatory dry-run and type-to-confirm, described below), the Serve launch and Pods stop ([Personal Serving Views](#addendum-personal-serving-views-serve-pods-routes)), RunPod onboarding apply from Providers ([RunPod onboarding CLI and TUI](#addendum-runpod-onboarding-cli-and-tui)), Resources mutations ([TUI Resources View](#addendum-tui-resources-view)), and routing-job and volume-file actions from Operations ([TUI Operations View](#addendum-tui-operations-view)). Overview summarizes provider counts, provider health, lease states, active lease count, recent workload count, and persisted cost totals. The Overview metric cards size to their content (auto height, `min-height: 4`), so every label and value stays visible at both 80x24 and 140x45 terminals; at narrow widths the card text wraps onto extra rows instead of being clipped to an empty content region. When a source load fails, each data view replaces any still-`Loading` placeholder with `Unavailable` and retains values from an earlier successful refresh beside the inline error — this applies to Overview, Cost, Providers, Operations, and Resources, so a failed first load never leaves stale loading text on screen. Providers lists the registered provider plugins by id, status, and pricing-model contract. Models lists the packaged catalogue, opens a Markdown dossier detail view, and offers an advisory hardware-fit table. Model detail and fit views render companions, put the evidence kind beside confidence, and label non-chat dossiers `not servable via the OpenAI proxy`. The TUI delegates through the shared broker services and provider adapters; configured read sources can contact providers.

Resources action rows and the Operations guardrail preview row size to their controls; the guardrail input shares the available width with its button, keeping the actions visible at narrow terminal widths.

Global bindings are `?` help, `:` command palette, `/` list search, `q` / `Ctrl-C` quit, and the view-navigation keys shown in the footer. The `:` palette dispatches named views plus `help` and `refresh`; `/` filters the currently loaded Models, Providers, or Pods / Leases rows in memory. `Escape` closes an overlay, clears a search, and restores the prior table focus. Help is generated from the app and active-screen bindings. Models and hardware fit use compact priority-column tables below 140 columns, with `v` opening selected-row details (`Shift+V` cycles hardware-fit variants).

Key modules:

| Module | Responsibility |
|---|---|
| `tui.app` | `PitwallApp`, the Textual shell with header/footer, persistent navigation, `q` / `Ctrl-C` quit, `o` Overview, `p` Providers, `m` Models, and lazy DB-pool resolution |
| `tui.console` | Modal keyboard command and search `Input` overlays; the app owns dispatch while each supported screen owns transient snapshot filtering |
| `tui.overview` | `OverviewScreen`, `OverviewSnapshot`, `OverviewSource`, `PostgresOverviewSource`, `StaticOverviewSource`, status/count formatting helpers |
| `tui.providers` | `ProvidersScreen`, `ProvidersSnapshot`, `ProvidersSource`, `PostgresProvidersSource`, registry/static sources, and fixed-width provider table formatting |
| `tui.models` | `ModelsScreen`, `ModelDetailScreen`, `ModelsSnapshot`, `ModelCatalogueSource`, local and static sources, catalogue rows, and retained dossier Markdown rendering |
| `tui.hardware_fit` | `HardwareFitScreen`, fit source protocol/live/static adapters, GPU fit table, and snapshot price-state rendering |
| `tui.serve` | `ServeActionSource`, production/scripted adapters, mandatory dry-run preview, defense-in-depth secret redaction, and the exact type-to-confirm launch modal |

`PostgresOverviewSource` reads providers via `ProviderRepository.list`, lease state counts from `pitwall.leases`, cost totals via `pitwall.core.cost_reporting.cost_summary`, and recent workload counts via `pitwall.core.cost_reporting.recent_workloads`. Tests inject `StaticOverviewSource`, so Textual Pilot coverage is hermetic and never opens a DB connection.

`ServiceProvidersSource` is the production Providers source. Its normal refresh remains a
persisted descriptor read; `g` explicitly probes health and `a` explicitly requests at most 25
availability/pricing items through `ProviderOperationsService`. Provider/database failures render
stable generic messages and Decimal prices stay strings. The action renders loading, populated,
explicit empty, stale-after-failed-refresh, and unavailable presentation states; a failed service
snapshot never replaces the last successful bounded snapshot
(`pitwall.tui.providers`).
`PostgresProvidersSource` remains a persisted-only source. It
renders Provider ID, Status, Pricing model, Armed, and Active pod. Armed is `yes` only when both
the non-empty `config.active_pod_id` and `config.active_lease_id` facts exist; the active pod cell
otherwise shows `—`. `RegistryProvidersSource` remains available for demos and tests, reporting
registry entries as `registered`, `tagged`, unarmed, with no active pod. Tests inject
`StaticProvidersSource`, so Pilot coverage never imports live credentials or calls provider APIs.

Widgets only call their injected source plus pure display helpers. Service, pool, repository,
RunPod-client, fit, budget, launch, and teardown calls stay in source or adapter classes. The AST
guard in `tests/tui/test_no_business_logic_guard.py` enforces this boundary; `ServePreviewScreen`
is explicitly checked to delegate through its action source's `preview` and `launch` methods.

`LocalModelCatalogueSource` reads the local packaged catalogue only. Press `m` to open Models; the table shows model, vendor, family, variant count, default variant, and chat support. Press `r` to reload the catalogue, and Enter on a row opens its dossier with a generated variant table followed by the retained research body in `MarkdownViewer`. Press `g` in detail to inspect the default variant's hardware fit at a 120-minute secure-cloud TTL. The fit table always keeps every returned GPU row and shows GPU, count, VRAM, headroom, verdict, $/hr, TTL cost, and maximum count; missing headroom is `unknown` and unavailable prices/costs are `unpriced`. Its summary identifies model, variant, confidence, TTL, and cloud: confidence and a `no` verdict are advisory, never launch gates. Within the fit screen, `r` refreshes, `c` toggles secure/community cloud, and `v` cycles variants deterministically. Live snapshots show their age, become `stale (Nh Nm old)` after one hour, and fallback data is `unpriced`; the underlying price cache is five minutes. Tests inject static sources; source failures render inline and neither the list nor detail view opens a database pool.

Enter on a hardware row opens `ServePreviewScreen`; there is no launch binding on the catalogue,
dossier, or fit screens. The preview must complete a `serve_model(dry_run=True)` call before Enter
can open the modal. It shows the redacted launch result, fit verdict/confidence, and the same TTL
estimate used for confirmation. A `no` verdict remains advisory and is repeated in the modal. The
operator must type the capability name exactly (for example, `llm.model`) before Launch is enabled.
The live request changes `dry_run` to false only after dismissal with a true result. Success stays
inline with the proxy base URL and expiry; preview and launch failures stay inline without skipping
the gate. Priced choices derive `rate_per_second` as total row `$ / hr / 3600`; unpriced choices pass
no rate so serve-model can apply its existing-rate or required-rate contract. Preview mappings are
recursively redacted when key names contain `token`, `secret`, `password`, `api_key`, or
`authorization`, case-insensitively, in addition to serve-model's launch-secret redaction. Preview
output includes configured GPU-price age/source/stale status; a stale configured policy refuses the
later non-dry launch before pod creation.

---

### `pitwall.cli` (the `src/pitwall/cli/` package)

**Responsibility:** Top-level dispatcher (`pitwall.cli:main`) and one module per command group. Each module is imported, and its function called with the remaining arguments, only when its group is invoked, so `pitwall agents _shim` and the hook entry points never load the broker's runtime. Uses `argparse` for flag parsing. All functions return an integer exit code (0 = success, non-zero = failure). Parsers include `--json` via `add_json_argument(parser)`, except `setup`, `dashboard`, `models evidence`, `gateway sync`, `gateway serve`, `mcp relay`, `mcp serve channel`, `retention run`, and `usage serve`, plus the hand-parsed `workbench` subcommands.

`pitwall.cli.GROUPS` is the routing table:

| Group | Module and function |
|---|---|
| `db` | `pitwall.db:main` |
| `leases` | `pitwall.cli.leases:cmd_leases` |
| `register-template` | `pitwall.cli.templates:cmd_register_template` |
| `init` | `pitwall.cli.init:cmd_init` |
| `create-capability`, `seed` | `pitwall.cli.capabilities:cmd_create_capability`, `cmd_seed` |
| `config` | `pitwall.cli.config_check:cmd_config` |
| `terminate-pod` | `pitwall.cli.pods:cmd_terminate_pod` |
| `register-endpoint`, `set-provider-health` | `pitwall.cli.endpoints:cmd_register_endpoint`, `cmd_set_provider_health` |
| `serve` | `pitwall.cli.serve_model:cmd_serve` |
| `status`, `stop`, `setup` | `pitwall.cli.personal:cmd_status`, `cmd_stop`, `cmd_setup` |
| `doctor` | `pitwall.cli.doctor:cmd_doctor` |
| `models` | `pitwall.cli.models:cmd_models` |
| `warm-volume` | `pitwall.cli.warm_volume:cmd_warm_volume` |
| `dashboard` | `pitwall.cli.dashboard:cmd_dashboard` |
| `burn-rate` | `pitwall.cli.burn_rate:cmd_burn_rate` |
| `guardrails` | `pitwall.cli.guardrails:cmd_guardrails` |
| `routing` | `pitwall.cli.routing:cmd_routing` |
| `runpod-onboard` | `pitwall.cli.onboarding:cmd_runpod_onboard` |
| `cost` | `pitwall.cost.cli:cmd_cost` |
| `budget` | `pitwall.cli.budget:cmd_budget` |
| `runpod` | `pitwall.cli.runpod_resources:cmd_runpod_resources` |
| `provider-ops` | `pitwall.cli.provider_ops:cmd_provider_ops` |
| `volume-files` | `pitwall.cli.volume_files:cmd_volume_files` |
| `gateway`, `quotas` | `pitwall.cli.gateway:cmd_gateway`, `cmd_quotas` |
| `mcp` | `pitwall.cli.mcp:cmd_mcp` |
| `agents` | `pitwall.agents.cli:main` |
| `usage` | `pitwall.cli.usage:cmd_usage` |
| `workbench` | `pitwall.cli.workbench:cmd_workbench` |
| `retention` | `pitwall.retention.__main__:main` |

#### Module-level constants

| Constant | Value | Meaning |
|---|---|---|
| `_TERMINATE_VERIFY_TIMEOUT_S` | `60.0` | Default max seconds to wait for pod to reach `EXITED`/`TERMINATED` after terminate call |
| `_TERMINATE_VERIFY_INTERVAL_S` | `5.0` | Polling interval (seconds) during terminate verification loop |
| `_PROVIDER_HEALTH_STATUSES` | `("unknown", "healthy", "unhealthy", "hibernated")` | CLI-accepted provider health values |

#### Dispatcher: `main`

```python
def main(argv: list[str] | None = None) -> int
```

- `argv`: Command-line args (excluding the program name). Defaults to `sys.argv[1:]`.
- Reads `args[0]` as the command group and delegates to the function named in `GROUPS`.
- With no args, opens the console (`pitwall dashboard`). With `-h`/`--help`/`help`, prints usage
  to stdout and returns `0`; an unknown group prints `Unknown command group: <name>` plus usage to
  stderr and returns `1`.

`-V` and `--version` print the installed version. Every group in the table above is dispatched this way.

#### `models list`, `models show`, `models fit`, and `models evidence`

`models list [--json]` lists each dossier's model, vendor, variant count, best
single-GPU fit, and secure-cloud price per hour. The best row is selected from
the default variant's sorted 60-minute single-GPU fits; JSON uses
`model`, `vendor`, `variants`, `best_single_gpu`, and `price_per_hour` (null
when unavailable).

`models show MODEL [--json]` displays dossier metadata, a variants table, and
the retained Markdown research body. JSON is the dossier front matter plus a
`body` field. `MODEL` accepts either the catalogue spelling `org/model` or the
dossier/REST spelling `org--model`; when there is no slash, the CLI replaces one
`--` separator with `/`.

`models fit MODEL [--variant ID] [--ttl-minutes N] [--cloud secure|community] [--json]`
shows every GPU class with GPU count, VRAM, headroom, fit verdict, hourly
price, TTL cost, and variant confidence. TTL defaults to 120 minutes and must
be positive. Missing RunPod pricing uses the canonical VRAM fallback, prints a
one-line fallback notice, and renders prices and costs as `unpriced`; plain output reports price source,
age, and stale status, while JSON is `{model, variant, options, source, age_seconds, stale}` and
each option has `gpu_class`, `gpu_count`, `vram_gb`, `headroom_gb`, `fit`,
`warm_cache`, `cache_state`, `price_per_hour`, `cost_for_ttl`, `cloud`, `max_count`, and
`confidence`. When `DATABASE_URL` is configured, `models fit` reads the matching
`serve-<capability>` provider through a short-lived, closed-after-use connection and compares its verified variant and current volume: the plain
`Cache:` line and JSON `cache_state` are `warm` for a match and `cold` otherwise. Without a
configured or reachable registry, the plain line says `not checked` and JSON uses
`not_checked`; the backward-compatible `warm_cache` boolean remains false.
`stale` follows optional `PITWALL_PRICE_MAX_AGE_S`; unset remains false. Fit verdicts are `fits`, `tight` (positive single-GPU headroom below 10% of
aggregate VRAM), `tp`, or `no`.
Decimal values are strings and unavailable values remain null. Unknown models
or variants print an error and exit 1; successful commands exit 0.

`models fit MODEL --inventory FILE [--context N]` evaluates the variant against your own GPUs
instead of the RunPod catalogue: it reads no price snapshot and no registry, `--ttl-minutes` and
`--cloud` have no effect, and the JSON is `{model, variant, inventory, context_length, options,
source: "local"}` (`pitwall.cli.models`). `FILE` is YAML holding the inventory mapping directly or
under an `inventory:` key (`pitwall.models.inventory:LocalInventory`):

| Key | Type | Default | Meaning |
|---|---|---|---|
| `gpus[].name` | `str` | required | Label echoed as `gpu_class` |
| `gpus[].count` | `int` >= 1 | required | Cards of this kind on the host |
| `gpus[].vram_gb` | positive decimal | required | Memory per card |
| `gpus[].arch` | `sm_<2-3 digits>` | required | CUDA architecture, for example `sm_86` |
| `gpus[].nvlink` | `bool` | required | Whether the cards share NVLink |
| `gpu_memory_utilization` | decimal in (0, 1] | `0.90` | Fraction of each card usable for weights and KV cache |

`--context N` (a positive integer) sets the context length used for the KV-cache estimate; it
defaults to the variant's verified context, and the command exits 1 with `--context is required
when variant context is unverified` when neither exists. Each option's `gpu_count` is the card
count needed for weights plus KV cache within `gpu_memory_utilization`; `no` carries a `reason`
(`unverified` minimum VRAM, `arch` for an `fp8` or `nvfp4` variant below `sm_89`, or `kv_cache`
when the host has fewer cards than needed), and a multi-card option without NVLink carries the
warning `communication-bound`. Prices are `0` and `cost_for_ttl` is null. A malformed inventory
exits 1 naming the file and the offending keys or the YAML line and column, never the value.

#### `leases list`

`pitwall leases list [--json]` is a read-only view of active pod
leases. Plain and JSON output include last proxy traffic, idle timeout, renewal
policy, maximum hourly price, expiry, and the existing identity/state fields.
It uses the same persisted projection as the Textual Pods / Leases screen; it
does not query RunPod or mutate a lease.

`models evidence MODEL --variant ID --gpu-class CANONICAL_NAME --observed-vram-gb N
--observed-startup-s N` records a `kind: measured` observation dated in UTC. It accepts the same
two model-ID spellings, requires positive numeric observations and a canonical RunPod GPU name,
schema-validates the completed dossier before replacing YAML front matter, preserves the Markdown
body unchanged, reloads the catalogue cache, and exits 1 without writing on validation failure.

#### `serve` (registry backend, `[personal] backend = "registry"`)

`pitwall serve --plan-only` is dispatched by `pitwall.cli.serve_model:cmd_serve` directly to
`cmd_serve_model(argv)` before backend selection, so it remains available with the personal
backend and when `DATABASE_URL` is unset. Other `pitwall serve` invocations go through
`pitwall.cli.personal.cmd_serve`, which calls `select_backend()`
(`src/pitwall/personal/backend.py`) and, when `pitwall.toml` sets `[personal] backend =
"registry"`, delegates the raw argv to `RegistryBackend.serve()` → `cmd_serve_model(argv)`
unchanged; the registry path then also needs `DATABASE_URL`. This subsection
documents that registry-backed flag set plus the database-free planning exception; see
[`serve` (personal backend)](#serve-personal-backend)
below for the flags that apply instead when the registry backend is not selected.

`serve` launches or replays an OpenAI-compatible model-serving pod lease.
The operator sequence and the distinction between catalogue fit, plan-only,
registry-backed dry-run, paid launch, endpoint verification, and stop are in
[`docs/operator/serve-quickstart.md`](../operator/serve-quickstart.md).
`--plan-only` is the database-free catalogue preview: it does not require `--capability` or
`DATABASE_URL`, does not create a pool or any registry/provider state, and renders the resolved
model ID, engine/variant, GPU/count, image, exact argv, volume-cache environment, fit verdict,
startup timeout, TTL estimate, and `live` or `fallback` price source. It accepts the documented
`RTX_####` planning shorthand (for example `RTX_3090`) and reports the canonical GPU name.
Live launches and `--dry-run` require `--capability` and `DATABASE_URL`, because the dry-run
preview remains registry-backed. Without it the command exits 2 before creating a pool and reports
`serve needs DATABASE_URL (registry-backed dry run); run
\`pitwall init\` or export DATABASE_URL`; JSON reports
`{"error": "missing_database_url", "detail": "..."}`.

The `J23` release-harness selector is distinct from this command: it runs the same registry,
ready-hook, proxy, and disarm boundaries with in-process fakes, so
`DATABASE_URL='' uv run --frozen bash scripts/release/run-user-journeys.sh J23` needs no database.

Use canonical RunPod GPU names such as `NVIDIA GeForce RTX 4090`; supported legacy
aliases normalize automatically. `--model` accepts both `org/model` and
`org--model`. A catalogue model plus `--variant` resolves the variant's image and
engine, unless an explicit `--image` or `--engine` overrides those values. Without
a dossier, `--image` is required and `--variant` is invalid. Omitting `--engine`
preserves the catalogue engine; models without a dossier resolve to the service
default engine, `vllm`. Detail and fit views render companions, show evidence kind
beside confidence, and show `not servable via the OpenAI proxy` for non-chat rows.
Plain and JSON dry-run output shows the resolved engine.

`serve --route NAME` runs the configured `PITWALL_ROUTING_CLI` only after
a live serve succeeds. It adds `NAME` from the returned capability and Pitwall
base URL. It refreshes a Pitwall-origin route only when the add command exits 4
and stderr contains the case-sensitive text `already registered from Pitwall`.
Any other add failure is a registration failure. The routing token is inherited
from the environment and never placed in argv. Registration failure does not
roll back the lease: CLI exits 3, prints
`serve ok; route registration failed: <stderr>`, and prints the manual
`pitwall agents profiles add` remedy.

The command help ends with these examples:

```text
pitwall serve --plan-only --model Qwen/Qwen3.8-27B --gpu-class RTX_3090
pitwall serve --capability llm.qwen --model Qwen/Qwen3.8-27B --gpu-class 'NVIDIA GeForce RTX 4090' --variant gguf:UD-Q4_K_XL --dry-run
pitwall serve --capability llm.custom --model org/model --gpu-class 'NVIDIA GeForce RTX 4090' --image example/vllm-openai:latest --rate-per-second 0.004 --dry-run
```

#### `serve` (personal backend)

When the registry backend is not selected (the default) and `--plan-only` is absent, `cmd_serve`
(`pitwall.cli.personal`) skips the registry path entirely and parses argv
with its own parser, backed by
`PersonalServeService` (`src/pitwall/personal/service.py`). This flag set has no
`--capability`, `--image`, `--dry-run`, or `--plan-only` — those belong only to the
registry backend documented above.

| Flag | Required | Default | Type |
|---|---|---|---|
| `--model` | Yes | — | `str` |
| `--variant` | No | `None` | `str` |
| `--gpu-class` | Yes | — | `str` |
| `--gpu-count` | No | `1` | `int` |
| `--cloud` | No | `"community"` | choice: `secure`, `community` |
| `--ttl-minutes` / `--ttl` | No | `60` | `int`; `--ttl` is an alias; `ServeSpec` bounds it to 5-10,080 |
| `--max-usd-per-hour` | Yes (enforced after parsing) | — | `Decimal`; `ServeSpec` requires it be greater than 0 |
| `--rate-per-second` | No | `None` | `Decimal` |
| `--route` | Yes | — | `str`; 1-64 chars, must start with a letter (`ServeSpec` pattern `^[A-Za-z][A-Za-z0-9._-]*$`) |
| `--json` | No | `False` | `bool` (store_true) |

`--max-usd-per-hour` is not declared required to argparse; `cmd_serve` checks for it after
parsing and exits `2` with `--max-usd-per-hour is required: it caps what one hour of this
pod may cost` when it is missing. The parsed flags build a `ServeSpec`
(`src/pitwall/personal/service.py`), which the service checks against the GPU's live
price before launch, raising `ServeRefused` on rejection.

Before launching, `cmd_serve` resolves a RunPod credential via `resolve_runpod_api_key`
(`src/pitwall/personal/keys.py`); if none is configured and stdin is a TTY it runs the
same interactive flow as `pitwall setup`, otherwise it exits `2` with
`no RunPod credential: run \`pitwall setup\` or export RUNPOD_API_KEY`.

On success, plain output is `route <name> is ready: <served_model_id> at <endpoint_url>`
followed by the deadline and a `route-shim.sh <route> prompt.md` try-it line; `--json`
emits the lease plus that same `"try"` field.

| Exit | Meaning |
|---|---|
| `0` | Lease launched |
| `1` | `ServeRefused` — for example the GPU's live price exceeds `--max-usd-per-hour` |
| `2` | Missing `--max-usd-per-hour`, or no RunPod credential could be resolved |
| `3` | `ServeFailed` — the pod launched but failed afterward; termination was attempted |
| `130` | Interrupted (`KeyboardInterrupt`); cleanup was attempted for any pod already created |

Those termination messages describe a best-effort cleanup attempt. Provider/API
errors are suppressed, so they do not independently confirm that the pod stopped.

The full personal-serving workflow, including `pitwall setup`, is in
[Personal serving](../operator/personal-serving.md).

#### `status` and `stop`

`status` and `stop` are also implemented in `pitwall.cli.personal` and branch on
`select_backend()` the same way `serve` does: the registry backend only when `pitwall.toml`
sets `[personal] backend = "registry"`.

`pitwall status [--json]`:

- Registry backend: an alias for `pitwall leases list`. `cmd_status` parses its flags first,
  then `RegistryBackend.status()` calls `cmd_leases(["list"])`, adding `--json` when given.
  Plain output starts with the `Backend: registry (Postgres registry; ...)` line, and JSON gains
  a `"backend": "registry"` key.
- Personal backend: prints the `Backend: personal (local state file; ...)` line, then one line per
  lease (route, state, served model ID, GPU class, and deadline), or `nothing running` when there
  are none; `--json` emits `{"backend": "personal", "leases": [...]}`. If `PITWALL_ENDPOINT_KEY` is not set in the shell, plain output adds
  a trailing note that routes will not authenticate until it is. For a live lease past
  its deadline, `status` requests termination; an error leaves the lease active so a
  later `status` or `stop --all` retries, while a successful request removes the route
  and records `stopped` / `terminated_late`.

`pitwall stop [route] [--all] [--json]`:

- Registry backend: `route` is treated as a lease ID and is required —
  `RegistryBackend.stop()` calls `cmd_leases(["stop", lease_id])`, adding `--json` when
  given. Omitting it exits `2`
  with `pitwall stop <lease-id> on the registry backend`.
- Personal backend: pass a route name, or `--all` to stop every lease currently in state
  `launching` or `ready`; omitting both exits `2` with
  `pitwall stop <route> or pitwall stop --all`. Plain output is one `stopped <route>` line
  per stopped lease, or `nothing to stop`; `--json` emits `{"stopped": [...]}`. `--all` also
  stops the gateway sidecar: plain output adds `stopped gateway`, and JSON adds
  `"gateway_stopped": true|false`; a gateway that will not stop is reported as `false`, never
  raised. An unknown route exits `1` with `refused: unknown_route <route>`.

#### `setup`

`pitwall setup [--yes]` runs `pitwall.personal.setup.run_setup`, the first-run flow that
configures a RunPod credential, the `PITWALL_ENDPOINT_KEY` endpoint key, and the Pitwall
Claude plugin. It does not branch on `DATABASE_URL`. Without `--yes` it prompts
interactively over stdin; `--yes` answers every prompt affirmatively. It always returns
`0`. The full setup sequence is in [Personal serving](../operator/personal-serving.md).

#### `cmd_register_template` / `_register_template_async` / `_dry_run_validate`

```python
def cmd_register_template(argv: list[str]) -> int
async def _register_template_async(args: argparse.Namespace) -> int
def _dry_run_validate(args: argparse.Namespace) -> int
```

- **`cmd_register_template`**: Parses args; dispatches to `_dry_run_validate` (if `--dry-run`) or wraps `_register_template_async` in `asyncio.run`.
- **`_dry_run_validate`**: Performs no I/O. Prints parsed image SHA, normalized template name, display name, container disk size, registry auth ID, and the known `_TEMPLATE_ENV_KEYS` tuple.
- **`_register_template_async`**: Requires `RUNPOD_API_KEY` env var. Calls `ensure_template(pool, image_ref, ...)`, whose default generated-template identity is a full-config SHA rather than an image-only key, then prints `Template registered: <id>`.

Argument parser: `_parse_register_template_args`

| Flag | Required | Default | Type |
|---|---|---|---|
| `--image` | Yes | — | `str` |
| `--template-name` | No | `"pitwall-cloud-worker"` | `str` |
| `--container-disk-gb` | No | `50` | `int` |
| `--dry-run` | No | `False` | `bool` (store_true) |

#### `cmd_init` / `_init_async`

```python
def cmd_init(argv: list[str]) -> int
async def _init_async(args: argparse.Namespace) -> int
```

- **`cmd_init`**: Parses args and wraps `_init_async` in `asyncio.run`.
- **`_init_async`**: Uses `--from-seed`, the default `./seed` directory when present, or a manual in-memory seed payload. Applies capability/provider seed data, requires at least one capability and one provider, marks the first provider `healthy`, then prints `Pitwall init complete` plus a dry-run `/v1/inference` smoke `curl`.

Argument parser: `_parse_init_args`. Manual-path defaults come from `_DEFAULT_INIT_*` constants and `_manual_init_seed_payload`. Enum choices come from `CapabilityClass`, `CostMode`, and `ProviderType` (`pitwall.core.enums:CapabilityClass`, `pitwall.core.enums:CostMode`, `pitwall.core.enums`).

| Flag | Required | Default | Type |
|---|---|---|---|
| `--from-seed` | No | `None` | seed file or directory |
| `--manual` | No | `False` | `bool` (store_true) |
| `--non-interactive` | No | `False` | `bool` (store_true) |
| `--yes` | No | `False` | `bool` (store_true; alias for `--non-interactive`) |
| `--capability-name` | No | `"embedding.demo"` on manual path | `str` |
| `--capability-class` | No | `"embedding"` on manual path | choice: `embedding`, `rerank`, `llm`, `vision`, `transcribe`, `gpu_lease`, `custom` |
| `--cost-mode` | No | `"per_second"` on manual path | choice: `per_second`, `per_request`, `per_token` |
| `--provider-name` | No | `"demo-runpod-lb"` on manual path | `str` |
| `--endpoint-id` | No | `"eptest00000000"` on manual path | `str` |
| `--provider-type` | No | `"serverless_lb"` on manual path | choice: `serverless_queue`, `serverless_lb`, `public_endpoint`, `pod_lease` |
| `--region` | No | `"US-EXAMPLE-1"` on manual path | `str` |
| `--gpu-class` | No | `"NVIDIA L4"` on manual path | `str` |
| `--per-second-active` | No | `"0.001"` on manual path | `str` |
| `--priority` | No | `1` | `int` |
| `--smoke-base-url` | No | `PITWALL_API_URL`, else `PITWALL_BASE_URL`, else `http://127.0.0.1:$PITWALL_API_PORT` (port 8080 when unset) | `str` |
| `--smoke-text` | No | `"hello"` | `str` |

Seed inputs: `--from-seed` accepts a YAML/JSON file or directory; without `--manual`, `./seed` is used when it exists (`pitwall.cli.capabilities`, `pitwall.seed`). Seed outputs are capability/provider registry writes plus a `SeedApplyResult` countable as `capabilities` and `providers` (`pitwall.seed`).

#### `cmd_create_capability` / `_create_capability_async`

```python
def cmd_create_capability(argv: list[str]) -> int
async def _create_capability_async(args: argparse.Namespace) -> int
```

- **`cmd_create_capability`**: Parses args and wraps `_create_capability_async` in `asyncio.run`.
- **`_create_capability_async`**: Uses either `--spec` or the manual flag set. `--spec` applies capability entries only from a YAML/JSON spec file; manual mode requires `--name`, `--class`, and `--cost-mode`, then upserts through `CapabilityRepository`. Success prints `Capability created: <id>`, name, class, and cost mode.

Argument parser: `_parse_create_capability_args`. Enum choices come from `CapabilityClass`, `CostMode`, and `CapabilityHint` (`pitwall.core.enums:CapabilityClass`, `pitwall.core.enums:CostMode`, `pitwall.core.enums`).

| Flag | Required | Default | Type |
|---|---|---|---|
| `--spec` | Conditional | `None` | YAML/JSON file path |
| `--name` | Conditional | `None` | `str` |
| `--version` | No | `"1.0.0"` | `str` |
| `--class` | Conditional | `None` | choice: `embedding`, `rerank`, `llm`, `vision`, `transcribe`, `gpu_lease`, `custom` |
| `--cost-mode` | Conditional | `None` | choice: `per_second`, `per_request`, `per_token` |
| `--description` | No | `None` | `str` |
| `--hint` | No | `[]` | repeatable choice: `latency_sensitive`, `cost_sensitive`, `region_preference` |
| `--openai-compatible` | No | `False` | `bool` (store_true) |

`--spec` cannot be combined with `--name`, `--class`, or `--cost-mode`; without `--spec`, those three flags are required.

#### `cmd_seed` / `_seed_async`

```python
def cmd_seed(argv: list[str]) -> int
async def _seed_async(args: argparse.Namespace) -> int
```

- **`cmd_seed`**: Parses args and wraps `_seed_async` in `asyncio.run`.
- **`_seed_async`**: Applies one or more seed files/directories through `apply_seed_files`, optionally marks every applied provider `healthy`, then prints `Seed applied:` with capability/provider counts.

Argument parser: `_parse_seed_args`.

| Argument / Flag | Required | Default | Type |
|---|---|---|---|
| `paths` | Yes | — | one or more seed files or directories |
| `--mark-healthy` | No | `False` | `bool` (store_true) |

Seed inputs are YAML/YML/JSON files or directories containing those suffixes; directory files are sorted before loading. Seed outputs are capability/provider registry writes, and duplicate provider names with different IDs fail before writing the conflicting provider (`pitwall.seed`).

#### `cmd_config`

```python
def cmd_config(argv: list[str]) -> int
```

- **`cmd_config`**: Parses args and runs the `check` subcommand synchronously (no `asyncio.run`); a non-`check` subcommand returns `1`.
- Calls `check_domain_config(service)` (the same boot-time domain-config validator `require_runtime_env` uses; see `16-core-config.md`). On `ValueError` (including `ConfigFileError` for settings that fail validation) it prints `format_settings_load_error(exc)` to stderr and returns `os.EX_CONFIG`. Otherwise it prints `format_config_check_result(result)`; if the result carries errors it goes to stderr and returns `os.EX_CONFIG`, else it prints to stdout and returns `0`.

The load error is `ERROR [invalid-settings]` followed by `Pitwall settings could not be parsed:` and one
`  - <setting> (<ENV_NAME>): <message>` line per rejected setting (for example
`pitwall_pre_spend_mode (PITWALL_PRE_SPEND_MODE): Input should be 'balanced', 'block' or 'redact'`), or
`could not read Pitwall config file <path>: invalid TOML at line <n>, column <m>` for a syntax error.
Neither names a configured value or quotes the file; exit is `78` either way.

Argument parser: `_parse_config_args`.

| Argument | Required | Default | Type |
|---|---|---|---|
| `check` (subcommand) | Yes | — | literal subcommand (`required=True`) |
| `service` | No | `"api"` | positional service name |

#### `cmd_terminate_pod`

```python
def cmd_terminate_pod(argv: list[str]) -> int
```

- Calls `terminate_pod_sync(pod_id)` then polls `get_pod_sync` until the pod reaches `EXITED`/`TERMINATED`, returns `None` from the RunPod API, or the deadline is exceeded.
- Skips the verification loop if `--no-verify` is set.
- Polling interval: `_TERMINATE_VERIFY_INTERVAL_S` (5 s). Deadline: `--verify-timeout-s` (default 60 s).

Argument parser: `_parse_terminate_pod_args`

| Flag | Required | Default | Type |
|---|---|---|---|
| `--pod-id` | Yes | — | `str` |
| `--no-verify` | No | `False` | `bool` (store_true) |
| `--verify-timeout-s` | No | `60.0` | `float` |

Private helper: `_is_terminated(pod: dict[str, Any]) -> bool` — returns `True` when `pod["desiredStatus"]` is `"EXITED"` or `"TERMINATED"`.

#### `cmd_register_endpoint` / `_register_endpoint_async`

```python
def cmd_register_endpoint(argv: list[str]) -> int
async def _register_endpoint_async(args: argparse.Namespace) -> int
```

- **`cmd_register_endpoint`**: Parses args and wraps `_register_endpoint_async` in `asyncio.run`.
- **`_register_endpoint_async`**: Looks up (or upserts) a `Capability` by name, checks for duplicate provider name, builds a `Provider` record, and calls `ProviderRepository.create`. Sets `openai_base_url` for `SERVERLESS_QUEUE`/`PUBLIC_ENDPOINT` types and `lb_base_url` for `SERVERLESS_LB`. Without `--capability-name`, the supplied `--capability-id` must already exist; otherwise the command exits with a friendly "create it first" error before the DB FK can fail.

Argument parser: `_parse_register_endpoint_args`

| Flag | Required | Default | Type |
|---|---|---|---|
| `--endpoint-id` | Yes | — | `str` |
| `--provider-type` | Yes | — | choice: `serverless_queue`, `serverless_lb`, `public_endpoint`, `pod_lease` |
| `--capability-id` | Yes | — | `str` (must already exist unless `--capability-name` is given) |
| `--capability-name` | No | `None` | `str` (look up or upsert the capability by name) |
| `--name` | Yes | — | `str` (provider name) |
| `--region` | No | `None` | `str` |
| `--gpu-class` | Yes | — | `str` |
| `--cost-mode` | No | `None` | choice: `per_second`, `per_request`, `per_token` |
| `--per-second-active` | No | `None` | `float` (USD) |
| `--per-request` | No | `None` | `float` (USD) |
| `--per-million-input-tokens` | No | `None` | `float` (USD) |
| `--per-million-output-tokens` | No | `None` | `float` (USD) |
| `--workers-min` | No | `0` | `int` |
| `--workers-max` | No | `None` | `int` |
| `--idle-timeout-minutes` | No | `0` | `int` |
| `--flash-boot-verified` | No | `False` | `bool` (store_true) |
| `--max-payload-mb` | No | `30` | `int` |
| `--request-timeout-s` | No | `330` | `int` |
| `--priority` | No | `0` | `int` |
| `--health` | No | `unknown` | choice: `unknown`, `healthy`, `unhealthy`, `hibernated`; set `healthy` to make the provider immediately routable |

#### `cmd_set_provider_health` / `_set_provider_health_async`

```python
def cmd_set_provider_health(argv: list[str]) -> int
async def _set_provider_health_async(args: argparse.Namespace) -> int
```

- **`cmd_set_provider_health`**: Parses args and wraps `_set_provider_health_async` in `asyncio.run`.
- **`_set_provider_health_async`**: Loads the provider by id, patches `health_status`, and when setting `healthy` also clears failure/cooldown fields so the resolver can route to the provider immediately.

Argument parser: `_parse_set_provider_health_args`

| Argument | Required | Default | Type |
|---|---|---|---|
| `provider_id` | Yes | — | `str` |
| `health` | Yes | — | choice: `unknown`, `healthy`, `unhealthy`, `hibernated` |

#### `cmd_warm_volume` / `_warm_volume_async`

```python
def cmd_warm_volume(argv: list[str]) -> int
async def _warm_volume_async(args: argparse.Namespace) -> int
```

- **`cmd_warm_volume`**: Parses catalogue-backed warm-volume args and wraps `_warm_volume_async` in `asyncio.run`.
- **`_warm_volume_async`**: Resolves the catalogue model and variant, selects the cheapest single-GPU fit before dry-run or launch (fallback pricing preserves the fallback table order), then calls the shared `serve_model` launch with `warm_only=True` to warm the supplied volume and tear down the lease. Its plain and JSON result includes the resolved `gpu_class`, `gpu_count`, and `price_source` (`live` or `fallback` when default selection consulted pricing; `null` for an explicit `--gpu-class`).

Argument parser: `_parse_warm_volume_args`

| Flag | Required | Default | Type |
|---|---|---|---|
| `--model` | Yes | — | `str` |
| `--variant` | No | `None` (catalogue default) | `str` |
| `--volume-id` | Yes | — | `str` |
| `--datacenter` | No | `None` | `str` |
| `--gpu-class` | No | cheapest fitting GPU | `str` |
| `--gated` | No | `False` | `bool` |
| `--dry-run` | No | `False` | `bool` |
| `--json` | No | `False` | `bool` |

#### `cmd_serve_model` / `_serve_model_async`

```python
def cmd_serve_model(argv: list[str]) -> int
async def _serve_model_async(args: argparse.Namespace, out: Output) -> int
```

- Loads the same catalogue and calls the same `pitwall.serve.serve_model` service as REST and MCP.
- Both plain and JSON modes render the returned `ServeResult`; JSON is exactly `ServeResult.to_dict()`.
- For a catalogue model, returned `model_id` is the dossier's `served_model_name` (falling back to
  the Hugging Face ID); an explicit `--served-model-name` wins. Consumers use returned `model_id`.
- Plain output includes capability, model id, engine, variant, GPU count, lease/workload/template ids,
  proxy URL, expiry, dry-run state, and estimated cost.

Argument parser: `_parse_serve_model_args`

| Flag | Required | Default | Type |
|---|---|---|---|
| `--capability` | Conditional | — | `str`; required unless `--plan-only` |
| `--model` | Conditional | `None` | `str`; required with `--plan-only`, otherwise omitting it restores capability serve history |
| `--gpu-class` | Conditional | `None` | `str`; required with `--plan-only`, otherwise omitting it restores capability serve history |
| `--gpu-count` | No | `1` (`ServeRequest` default) | `int` |
| `--engine` | No | `None` | `vllm` \| `llama.cpp` \| `sglang` |
| `--variant` | No | `None` | `str` |
| `--template-id` | No | `None` | `str` |
| `--ttl-minutes` / `--ttl` | No | `120` (`ServeRequest` default) | `int`; `--ttl` is an alias of `--ttl-minutes` |
| `--idle-timeout-min` | No | `None` | `int`; minimum 5; omitted renewal policy defaults to `activity` when set |
| `--max-usd-per-hour` | No | `None` | `Decimal`; refuses the launch when the selected GPU's live price exceeds it |
| `--renewal` | No | `None` | choice: `manual`, `activity`; defaults from `--idle-timeout-min` |
| `--route` | No | `None` | `str`; registers or refreshes this route after a live serve succeeds |
| `--image` | No | `None` | `str` |
| `--served-model-name` | No | `None` | `str` |
| `--datacenter` | No | `None` | `str` |
| `--container-disk-gb` | No | `None` | `int` |
| `--rate-per-second` | No | `None` | `Decimal` |
| `--gated` | No | `False` | `bool` |
| `--env KEY=VAL` | No | `[]` | repeatable; later duplicate keys win |
| `--start-arg ARG` | No | `[]` | repeatable |
| `--dry-run` | No | `False` | `bool`; mutually exclusive with `--plan-only` |
| `--plan-only` | No | `False` | `bool`; mutually exclusive with `--dry-run`; requires `--model` and `--gpu-class`, and needs no `DATABASE_URL` or registry access |
| `--idempotency-key` | No | `None` | `str` |
| `--json` | No | `False` | `bool` |

For the subagent-model-routing flow, configure the same bearer token in
`PITWALL_API_SCOPED_TOKENS` with both `read` and `spend`: it reads capability
metadata and then calls the OpenAI proxy with that one token.

#### `cmd_runpod_resources`

`pitwall.cli.runpod_resources` owns one cohesive feature parser and handler for raw RunPod
account resources. The serialized top-level dispatcher integrates it as
`pitwall runpod`. Its 29 leaves mirror the shared service:

- `pods list|get|create|update|action|terminate`;
- `endpoints list|get|create|update|delete`;
- `templates list|get|create|update|delete` for account-owned templates;
- `volumes list|get|create|grow|delete`;
- `registry-auths list|get|create|replace|delete`;
- `hub list|get|search`, which is read-only (`hub list` takes `--limit` default `50` and `--offset` default `0`; `hub search QUERY` takes `--limit` default `50`).

Every leaf supports `--json`. Mutations require `--idempotency-key`; `--dry-run` selects the
zero-write preview, while a live mutation requires `--confirm` equal to the exact resource ID or
create name. Resource-specific fields are a strict, size-bounded `--request-json` object. Intent
and idempotency may only use their dedicated flags, and validation diagnostics omit input values.
Nested endpoint worker/scaling/GPU-pool objects remain intact. Full examples and provider limits
are in [RunPod account resource controls](../operator/runpod-resource-controls.md).

`pitwall runpod catalogue [--refresh] [--json]` (`pitwall.cli.runpod_market`) is the one read that
sits outside those 29 leaves: it renders the cached RunPod market read (GPUs with secure and
community price, minimum bid and stock; datacenters; balance and billing support) and prints
`RunPod catalogue: <state> | age <n>s | cache hit|refreshed` first. `--refresh` requests one
live read instead of the cache (`PITWALL_RUNPOD_MARKET_CACHE_TTL_S`, default `300` seconds);
`--json` is the `RunpodMarketRead` contract shared with `GET /v1/runpod/catalogue` and
`pitwall_runpod_catalogue` ([RunPod market](../operator/runpod-market.md)). It returns `0`.

#### `cmd_dashboard`

```python
def cmd_dashboard(argv: list[str]) -> int
```

- Parses `pitwall dashboard` arguments.
- With the personal backend (the default) and no RunPod credential resolving, it runs
  `pitwall setup` in an interactive terminal; otherwise it prints
  ``no RunPod credential: run `pitwall setup` or export RUNPOD_API_KEY`` and returns `2`
  (`pitwall.cli.dashboard`).
- Lazily imports `PitwallApp` from `pitwall.tui`.
- Calls `PitwallApp().run()` and returns `0` after the Textual app exits.

The command is read-only except for the guarded mutations listed in the TUI responsibility
paragraph. Overview, Leases, Routes, and Cost are read-only views; Models retains authored dossier
Markdown and routes a selected hardware row through mandatory dry-run preview and `serve.launch`
type-to-confirm before calling serve-model. Serve launches a personal route only after the
operator types the route name, and Pods stops a pod only after a typed confirmation
(`pitwall.tui.personal`). Resources similarly
requires its shared-service preview and exact resource/name confirmation before apply.

#### `pitwall.cli.mcp:cmd_mcp`

```python
def cmd_mcp(argv: list[str]) -> int
```

- `pitwall mcp serve broker` calls `ensure_runtime_env()` from `pitwall.mcp` (validates the required env vars for the MCP runtime) and then `mcp.run(transport="stdio")`. With `--json` it writes `{"transport": "stdio"}` to stderr, because stdout carries only MCP messages.
- `pitwall mcp serve channel` starts the orchestrator channel MCP server; it needs no broker configuration.
- `--transport` and `PITWALL_MCP_TRANSPORT` accept only `stdio`; network modes fail closed.
- `_parse_mcp_args` is the `pitwall mcp` group parser with `serve` (`broker` or `channel`), `relay`, `install`, and `uninstall` subparsers. Bare `pitwall mcp` is an argparse usage error (exit `2`); `mcp --help` and `mcp serve broker --help` exit `0`, while a non-stdio transport exits `2`.

| Flag | Required | Default | Type |
|---|---|---|---|
| `--transport` | No | `"stdio"` (or `PITWALL_MCP_TRANSPORT` env var) | `stdio` only |

`pitwall mcp relay -- COMMAND [ARGS…]` keeps a harness connected across broker restarts: it
relays the harness's stdio to the server command and restarts the command whenever it exits,
replaying the MCP `initialize` handshake to the new server. Register the relay instead of the
server so the harness survives `docker exec` targets restarting:

```sh
claude mcp add pitwall_broker -s user -- pitwall mcp relay -- docker exec -i <api-container> pitwall mcp serve broker --transport stdio
```

- A request that was in flight when the server died is answered exactly once with `-31010`
  and `data.error` `mcp_server_restarted`; `data.retryable` is `true`. A replayed `initialize`
  response is never forwarded to the harness.
- A server that is not ready within the wait window answers requests with `-31010`
  `mcp_server_unavailable`; `data.retryable` is `true`.
- Only lines that are JSON objects with `"jsonrpc": "2.0"` reach the harness. Any other server
  output (stray prints, `42`, `[]`, `{"x": 1}`) is dropped. The relay logs the first drop to
  stderr and then every 100th with the count, and never echoes the dropped text.
- A server line over the 64 MiB read limit cannot be repaired. Neither can an unterminated
  fragment that the line framing joined to a real reply: the relay recognises it when an
  unparsable line ends, from its last `{"jsonrpc"`, in a response (no `"method"`) to a pending
  request. In both cases the relay logs a fixed message, restarts the server, and answers every
  in-flight request with `-31010` `mcp_server_restarted` (`data.retryable` `true`). A log line
  that merely mentions `jsonrpc` is dropped without a restart.
- `PITWALL_MCP_RELAY_WAIT_SECONDS` caps how long the relay waits for the server to become
  ready (default `30`). The relay exits `0` when the harness closes stdin.

---

### `src/pitwall/doctor.py`

**Responsibility:** Builds the installation readiness report shared by `pitwall doctor` and the
`pitwall_doctor` MCP tool (`src/pitwall/mcp/tools/doctor.py`). It never makes a paid call and never
prints a secret value: every check names a variable, not its contents. Real services are reached
through the injectable `Probes` dataclass (`database`, `redis`, `api_health`, `canary`), so unit
tests never open a real connection.

`run_doctor(*, environ, settings=None, probes=None, api_url=None, api_token=None, canary=None,
timeout_s=DEFAULT_TIMEOUT_S)` returns a `DoctorReport`. Mode follows
`pitwall.personal.backend.select_backend`: `"registry"` when `pitwall.toml` sets
`[personal] backend = "registry"`, else `"personal"`. Each mode runs only its own checks. `DoctorReport.status` is `"fail"` if any
check is `"fail"`, else `"warn"` if any is `"warn"`, else `"ok"`. `DoctorReport.exit_code(strict=)`
returns `1` for `"fail"`, or for `"warn"` when `strict=True`, else `0`.

Every check is a `DoctorCheck(id, phase, status, detail, next_step=None)`. The full catalogue of
`check.id` values, what each means, and its fix, is documented for agents in
[`docs/agents/install.md`](../agents/install.md#7-doctor-check-catalogue).

### `pitwall.cli.doctor`

**Responsibility:** `pitwall doctor` thin CLI wrapper around `doctor.run_doctor`.

```python
def cmd_doctor(argv: list[str]) -> int
```

Flags: `--strict` (treat warnings as failures), `--api-url` (default `PITWALL_API_URL` or
`http://127.0.0.1:8080`), `--canary CAPABILITY` (also send a dry-run inference to this embedding
capability), `--timeout` (per-probe timeout in seconds, default `5`), `--json`.

Text mode prints one unwrapped line per check with `Output.print(..., soft_wrap=True)`, in the
form `[status] check.id: detail`, followed by `       next: <step>` when the check has a next step.
The final line is a summary: `doctor: <status> (N ok, N warn, N fail, N skip), mode <mode>,
pitwall <version>`. `--json` prints the `DoctorReport.to_dict()` object instead: `schema_version`,
`mode`, `version`, `status`, a per-status `summary`, and `checks` (each with `id`, `phase`,
`status`, `detail`, `next_step`). The command exits `report.exit_code(strict=args.strict)`.

### `src/pitwall/mcp_install.py`

**Responsibility:** Renders and applies MCP server registrations for Claude Code, Codex, and
OpenCode (`HARNESSES`). Every registration is named `pitwall`, launches `server_command()` over
stdio (`pitwall mcp serve broker`, through the `pitwall` console script beside the running
interpreter when present, else `<python> -m pitwall mcp serve broker`), and forwards `FORWARDED_ENV` (`RUNPOD_API_KEY`, `DATABASE_URL`,
`REDIS_URL`, `PITWALL_CONFIG_FILE`) by reference, never by value, using each harness's own syntax
(`${VAR:-}` for Claude Code, `env_vars` for Codex, `{env:VAR}` for OpenCode).

`SCOPES` records which scopes each harness actually loads servers from: `claude-code` and
`opencode` support `"user"` and `"project"`; `codex` supports `"user"` only, because codex-cli
ignores `[mcp_servers]` in a project's `.codex/config.toml`. `config_path` and `render_snippet`
both raise `McpInstallError` for an unsupported harness/scope pair, naming the scope to use
instead.

Key functions:

| Function | Responsibility |
|---|---|
| `config_path(harness, scope, *, environ, home, project_root)` | The config file path for one harness and scope; raises `McpInstallError` for an unsupported scope. |
| `render_entry(harness, command)` | The registration entry: a `dict` for Claude Code/OpenCode, a managed TOML block string for Codex. |
| `render_snippet(harness, scope, command)` | The exact text `docs/agents` shows: the `claude mcp add-json` command for Claude Code user scope, else the rendered entry as pretty JSON or TOML. |
| `plan_registration(...)` | Reads the current file, decides what would change, and returns an `InstallPlan`; never writes. |
| `apply_plan(plan, *, run=subprocess.run)` | Writes the new file atomically (after backing up an existing one to `<name>.bak.<UTC timestamp>`), then runs any harness CLI commands (Claude Code user scope). |
| `detect_harnesses(*, environ, home, project_root)` | Harnesses with a binary on `PATH` or an existing user config; filesystem only. |

Claude Code user scope is applied through `claude mcp add-json --scope user`/`claude mcp remove
--scope user`, because Claude Code rewrites `~/.claude.json` while it runs. Every other scope
writes the file directly. JSON files are re-serialized with 2-space indentation, preserving
foreign entries by value; the Codex TOML block preserves every foreign byte outside its
`# >>> pitwall mcp` / `# <<< pitwall mcp` markers exactly. An existing `pitwall` entry that this
tool did not write raises `McpInstallError` unless `force=True`, except for Codex, which has no
force override because it edits a managed block rather than a single value.

### `pitwall.cli.mcp_install`

**Responsibility:** `pitwall mcp install`/`pitwall mcp uninstall` thin CLI wrapper around
`mcp_install`. Kept import-light on purpose: it must never require the MCP server's own runtime
environment just to register or unregister it with a harness.

```python
def cmd_mcp_install(args: argparse.Namespace) -> int
```

With no harness named, it registers every harness `detect_harnesses` finds; naming one or more
`HARNESSES` values registers only those. For each harness it prints `== <harness> (<scope>):
<path>`, then either `adds this 'pitwall' server to <path>:` followed by the exact snippet from
`render_snippet` (or `removes the 'pitwall' server from <path>` for uninstall), or `already
registered` / `not registered` when nothing would change. It never prints a diff of the rest of
the harness config, because that file can hold other servers' literal credentials and this output
often lands in an agent's transcript. Unless `--dry-run`, a successful write prints `wrote <path>`
(with the backup path when one was made) and a `verify: <hint>` line from `VERIFY_HINT`. Outside
registry mode (no `DATABASE_URL`), a non-remove run also prints a note that `pitwall mcp serve broker` still
needs `DATABASE_URL`, `REDIS_URL`, and `RUNPOD_API_KEY` where the harness runs.

An `McpInstallError` for one harness (an unsupported scope, a foreign entry without `--force`, or
an unparseable config) prints as a single unwrapped `error: <message>` stderr line and marks only
that harness failed; the command still processes every other requested harness, and exits `1` if
any failed. With no supported harness detected and none named, it exits `1` with `no supported
harness found (claude-code, codex, opencode); name one explicitly`.

---

## 3. Command Inventory

### Global option

Every CLI command except `setup`, `dashboard`, `models evidence`, `gateway sync`, `gateway serve`, `mcp relay`, `mcp serve channel`, `retention run`, `usage serve`, and the hand-parsed `workbench` subcommands accepts `--json` to emit machine-readable JSON instead of human-friendly Rich tables and panels. When `--json` is passed, the command accumulates structured data into a single JSON object written to stdout on success or error.

### Exit codes

| Command | Exit `0` | Exit `1` | Exit `2` | Exit `3` |
|---|---|---|---|---|
| `pitwall db migrate [--json]` | Success | DB error | Unrecognized args | — |
| `pitwall db reset [--force] [--json]` | Success | Safety check / DB error | Unrecognized args | — |
| `pitwall db status [--json]` | Success | Schema not migrated yet / DB error | Unrecognized args | — |
| `pitwall db [migrate\|reset\|status] -h\|--help\|help` | Usage on stdout; the command does not run | — | — | — |
| `pitwall init [--from-seed PATH \| --manual] [--non-interactive \| --yes]` | Init complete + provider healthy | Seed validation / no capability or provider / provider health error / exception | — | — |
| `pitwall create-capability [--spec FILE \| --name NAME --class CLASS --cost-mode MODE]` | Capability created | Flag conflict / missing flags / seed validation / no capabilities / exception | — | — |
| `pitwall seed PATH... [--mark-healthy]` | Seed applied | Seed validation / provider health error / exception | — | — |
| `pitwall config check [service]` | Config report | — | — | — |
| `pitwall register-template [--dry-run]` | Success | Missing API key / error | — | — |
| `pitwall terminate-pod [--no-verify]` | Pod confirmed gone | Missing key / error / timeout | — | — |
| `pitwall register-endpoint` | Success | Missing capability / dup name / DB error | — | — |
| `pitwall set-provider-health` | Success | Provider not found / DB error | — | — |
| `pitwall serve` | Success | Launch / verification / unexpected error | Budget / 409 / 422 / input validation | — |
| `pitwall warm-volume --model <id> --volume-id <vol> [--variant <v>] [--datacenter <dc>] [--gpu-class <class>] [--gated] [--dry-run] [--json]` | Verified model cache warmed and lease torn down | Launch / verification / unexpected error | Budget / invalid catalogue input | — |
| `pitwall runpod RESOURCE OPERATION ... [--json]` | Read/preview/apply succeeded | Stable provider/control-plane failure | Parse, strict request, or confirmation failure | — |
| `pitwall dashboard` | Textual app launched and exited | Unhandled app error | Argument parsing (argparse); personal backend with no credential in a non-interactive shell | — |
| `pitwall mcp serve` | Success (blocks) | `ensure_runtime_env` error | Missing `serve` subcommand / non-stdio transport (argparse) | — |
| `pitwall burn-rate [--window-days N] [--json]` | Success | Persistence / configuration failure (folded into `burn_rate_unavailable`) | Argument parsing (argparse); bad `--window-days` | — |
| `pitwall cost summary [--capability-class NAME] [--since DATE] [--until DATE] [--json]` | Daily cost report | Any error (its message is printed) | Argument parsing (argparse); bad `--since`/`--until` | — |
| `pitwall cost workloads [--capability-id ID] [--provider-id ID] [--provider-type TYPE] [--state STATE] [--since DATETIME] [--until DATETIME] [--limit 1-100] [--json]` | Per-workload cost report | Any error (its message is printed) | Argument parsing (argparse); bad `--since`/`--until` | — |
| `pitwall doctor [--strict] [--api-url URL] [--canary CAPABILITY] [--timeout S] [--json]` | No `fail` (and, with `--strict`, no `warn`) | Any check `fail` (or, with `--strict`, any `warn`) | — | — |
| `pitwall guardrails status [--json]` | Rules, mode, counters, and last-decision metadata | — | Argument parsing (argparse) | — |
| `pitwall guardrails preview --payload JSON [--json]` | Decision rendered (allow / redact / block are all success) | — | Argument parsing (argparse); payload parse / size failure (`parse_pre_spend_json`) | — |
| `pitwall leases list [--json]` | Active leases rendered | Any error (`lease_operation_failed`) | Argument parsing (argparse) | — |
| `pitwall leases stop <lease-id> [--reason REASON] [--json]` | Lease stopped | `lease_not_found`, `lease_state_conflict`, `teardown_failed` (provider call failed; the lease stays stopping and is retried), or `lease_operation_failed` for a pre-spend rejection or any other error | Argument parsing (argparse) | — |
| `pitwall leases renew <lease-id> [--extends-minutes N] [--json]` | Lease renewed | `lease_not_found`, `lease_state_conflict`, `lease_expiry_limit_exceeded`, `idempotency_conflict`, or `lease_operation_failed` for a pre-spend rejection or any other error | Argument parsing (argparse) | — |
| `pitwall mcp install [HARNESS...] [--scope user\|project] [--project-root PATH] [--dry-run] [--force] [--json]` | Every requested harness registered (or already registered) | An unsupported scope, a foreign entry without `--force`, an unparseable config, or no harness detected | — | — |
| `pitwall mcp uninstall [HARNESS...] [--scope user\|project] [--project-root PATH] [--dry-run] [--force] [--json]` | Every requested harness unregistered (or already unregistered) | An unsupported scope, an unparseable config, or no harness detected | — | — |
| `pitwall models list [--json]` | Catalogue rows rendered | Catalogue / `ValueError` failure | Argument parsing (argparse) | — |
| `pitwall models show MODEL [--json]` | Dossier metadata, variants table, and retained body | Unknown model / Catalogue / `ValueError` failure | Argument parsing (argparse) | — |
| `pitwall models fit MODEL [--variant ID] [--ttl-minutes N] [--cloud secure\|community] [--inventory FILE] [--context N] [--json]` | Hardware fit rows rendered | Unknown model / variant or missing `--context` (Catalogue / `ValueError`) | Argument parsing (argparse) | — |
| `pitwall models evidence MODEL --variant ID --gpu-class CANONICAL_NAME --observed-vram-gb N --observed-startup-s N` | Evidence recorded, cache reloaded | Unknown model / variant or dossier validation failure | Argument parsing (argparse) | — |
| `pitwall provider-ops list [--capability-id ID] [--enabled-only] [--limit 1-100] [--json]` | Safe descriptors rendered | Service unavailable (`provider_operations_unavailable`) | Argument parsing (argparse); `provider_operations_invalid_request` | — |
| `pitwall provider-ops describe PROVIDER_ID [--json]` | Provider descriptor rendered | Service unavailable (`provider_operations_unavailable`) | Argument parsing (argparse); `provider_not_found`; `provider_operations_invalid_request` | — |
| `pitwall provider-ops availability PROVIDER_ID [--limit 1-100] [--json]` | Availability probe rendered | Service unavailable (`provider_operations_unavailable`) | Argument parsing (argparse); `provider_not_found`; `provider_operations_invalid_request` | — |
| `pitwall provider-ops health PROVIDER_ID [--probe] [--json]` | Persisted (or live with `--probe`) health rendered | Service unavailable (`provider_operations_unavailable`) | Argument parsing (argparse); `provider_not_found`; `provider_operations_invalid_request` | — |
| `pitwall retention run --archive-dir DIR [--days N] [--batch-size N] [--purge] [--dry-run]` | Bounded archive / purge manifest | Database unreachable; an archive error propagates as a traceback | Argument parsing (argparse); missing `DATABASE_URL` (`parser.error`) | — |
| `pitwall routing plan CAPABILITY_ID [--provider-id ID] [--input-json JSON] [--operation OP] [--json]` | Plan rendered | Unknown capability / planning / budget / unexpected failure | Argument parsing (argparse); invalid `--input-json`; pre-spend guardrail block | — |
| `pitwall routing submit CAPABILITY_ID [--provider-id ID] [--input-json JSON] [--idempotency-key KEY] [--webhook-url URL] [--dry-run] [--confirm CAPABILITY] [--json]` | Admitted or dry-run preview rendered | Unknown capability / planning / budget / unexpected failure | Argument parsing (argparse); invalid `--input-json`; `--confirm` mismatch; pre-spend guardrail block | — |
| `pitwall routing status WORKLOAD_ID [--json]` | Workload state rendered | Unknown workload / unexpected failure | Argument parsing (argparse) | — |
| `pitwall routing result WORKLOAD_ID [--json]` | Bounded result rendered | Unknown workload / unexpected failure | Argument parsing (argparse) | — |
| `pitwall routing cancel WORKLOAD_ID [--confirm WORKLOAD_ID] [--json]` | Cancellation submitted | Unknown workload / unexpected failure | Argument parsing (argparse); `--confirm` mismatch | — |
| `pitwall routing follow WORKLOAD_ID [--max-polls 1-100] [--interval 0-60] [--json]` | Polled workload rendered | Unknown workload / unexpected failure | Argument parsing (argparse); `--interval` out of `0-60` range | — |
| `pitwall runpod-onboard REQUEST_FILE [--action plan\|apply\|status\|resume\|rollback] [--confirmed-plan-id ID] [--json]` | Plan / apply / status / resume / rollback rendered | Service failure (folded into `runpod_onboarding_unavailable`); other `OnboardingError` | Argument parsing (argparse); invalid request file; `plan_confirmation_mismatch` | — |
| `pitwall setup [--yes]` | First-run setup completed | — | Argument parsing (argparse) | — |
| `pitwall status [--json]` (registry: `[personal] backend = "registry"` → `leases list`; personal: default → `service.status()`) | Registry: leases list; personal: leases per route or `nothing running` | Registry: lease operation failure; personal: unhandled service exception | Argument parsing (argparse); personal backend with no credential in a non-interactive shell | — |
| `pitwall stop <route> [--all] [--json]` (registry: `[personal] backend = "registry"` → `leases stop`; personal: default → `service.stop()`) | Registry: lease stopped; personal: pods stopped or `nothing to stop` | Registry: lease operation failure; personal: unhandled service exception | Argument parsing (argparse); registry: missing route; personal: neither a route nor `--all`; personal: no credential in non-interactive shell | — |
| `pitwall volume-files list VOLUME_ID DATA_CENTER_ID [--prefix PREFIX] [--max-items N] [--json]` | Volume object page rendered | Other `VolumeFileError`; folded `volume_file_provider_error` | Argument parsing (argparse); `VolumeFileError` with status `409` / `413` / `422` | — |
| `pitwall volume-files upload VOLUME_ID DATA_CENTER_ID OBJECT_KEY LOCAL_PATH [--root DIR] [--overwrite] [--confirm-overwrite] [--expected-sha256 SHA] [--idempotency-key KEY] [--dry-run] [--json]` | Upload completed | Other `VolumeFileError`; folded `volume_file_provider_error` | Argument parsing (argparse); `VolumeFileError` with status `409` / `413` / `422` | — |
| `pitwall volume-files download VOLUME_ID DATA_CENTER_ID OBJECT_KEY LOCAL_PATH [--root DIR] [--overwrite] [--confirm-overwrite] [--expected-sha256 SHA] [--dry-run] [--json]` | Download completed | Other `VolumeFileError`; folded `volume_file_provider_error` | Argument parsing (argparse); `VolumeFileError` with status `409` / `413` / `422` | — |
| `pitwall volume-files delete VOLUME_ID DATA_CENTER_ID OBJECT_KEY [--confirm-delete] [--idempotency-key KEY] [--dry-run] [--json]` | Object deleted | Other `VolumeFileError`; folded `volume_file_provider_error` | Argument parsing (argparse); `VolumeFileError` with status `409` / `413` / `422` | — |
| `pitwall volume-files logs POD_ID [--max-lines N] [--max-bytes N] [--json]` | Redacted bounded pod logs rendered | Other `VolumeFileError`; folded `volume_file_provider_error` | Argument parsing (argparse); `VolumeFileError` with status `409` / `413` / `422` | — |
| `pitwall gateway sync --version <v> [--from-json FILE] [--repo-root DIR]` | Catalog lock and seed files written | Delegated `tools.gateway.sync_catalog` failure (its exit code passes through) | — | — |
| `pitwall gateway sync --apply-verdicts DOSSIER [--seed FILE] [--from-json FILE] [--repo-root DIR]` | Every provider the benchmark dossier marks `kill` is `enabled: false` in the providers seed | Unknown provider in the dossier, or an unreadable dossier (the sync tool's exit code passes through) | — | — |
| `pitwall gateway status [--json]` | Lock summary; live quotas from `PITWALL_API_URL` when reachable | Missing catalog lock | — | — |
| `pitwall gateway doctor [--json]` | All checks passed (`catalog_lock`, `seeds_match_lock`, `gateway_health`, `front_door_loopback`) | Any check failed | — | — |
| `pitwall gateway serve [--bind ADDR] [--port N]` | Gateway served in the foreground until interrupted | Gateway start failure (`pitwall-gateway failed to start: <reason>`) | — | — |
| `pitwall quotas [--json]` | Free-pool burn-down table; catalog-only local rows when no API is reachable | — | — | — |
| `pitwall usage [--json]` | Usage table or JSON | — | Argument parsing; unreadable routes or registry | — |
| `pitwall usage serve [--host H] [--port N] [--interval SECONDS]` | Stopped by interrupt | Cannot bind the address | Argument parsing | — |
| `pitwall serve --gateway ...` (personal) | Pod served with the loopback gateway sidecar supervised | Refusal before launch | Gateway sidecar start failure (missing `PITWALL_GATEWAY_TOKEN`) | Failure after launch (termination attempted) |

`serve` exits `2` for `serve_conflict`, `invalid_gpu_class`, `rate_required`,
`unknown_variant`, `invalid_template`, `ttl_below_startup`, budget rejection, and Pydantic input validation. It exits
`1` for `launch_failed`, including RunPod authentication or GraphQL query failures,
`served_model_mismatch`, and unexpected failures.

`pitwall config check` exits `78` (`os.EX_CONFIG`) on a settings load error or domain-config
errors; the table's columns stop at `3`. `pitwall usage serve` also exits `78` when `--host` is not
loopback (`127.0.0.1`, `::1`, `localhost`) and `PITWALL_AGENTS_USAGE_TOKEN` is unset, or when
`--interval` is under 30 seconds; its defaults are `--host 127.0.0.1`, `--port 8848`, and
`--interval 45`.

---

## 4. Public Interfaces (for other subsystems)

### From `pitwall.cli` and its command modules

| Function | Signature | Who calls it |
|---|---|---|
| `main` | `(argv: list[str] \| None = None) -> int` | `__main__.py`, console script |
| `cmd_init` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_create_capability` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_seed` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_config` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_register_template` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_terminate_pod` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_register_endpoint` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_set_provider_health` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_serve` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_warm_volume` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_dashboard` | `(argv: list[str]) -> int` | `main()` dispatch |
| `cmd_mcp` | `(argv: list[str]) -> int` | `main()` dispatch |
| `_init_async` | `(args: argparse.Namespace) -> int` | `cmd_init` |
| `_create_capability_async` | `(args: argparse.Namespace) -> int` | `cmd_create_capability` |
| `_seed_async` | `(args: argparse.Namespace) -> int` | `cmd_seed` |
| `_register_template_async` | `(args: argparse.Namespace) -> int` | `cmd_register_template` |
| `_register_endpoint_async` | `(args: argparse.Namespace) -> int` | `cmd_register_endpoint` |
| `_set_provider_health_async` | `(args: argparse.Namespace) -> int` | `cmd_set_provider_health` |
| `_serve_model_async` | `(args: argparse.Namespace, out: Output) -> int` | `cmd_serve` |
| `_warm_volume_async` | `(args: argparse.Namespace) -> int` | `cmd_warm_volume` |

The isolated RunPod feature module exports
`build_runpod_resources_parser() -> argparse.ArgumentParser` and
`cmd_runpod_resources(argv, *, service=None) -> int`; top-level parser/dispatch registration is a
serialized integration hotspot.

---

## 5. Configuration

### Env vars read by the command modules

| Env var | Command(s) | Default | Purpose |
|---|---|---|---|
| `RUNPOD_API_KEY` | `register-template`, `terminate-pod` | `~/.runpod/config.toml` fallback | Preferred process-level RunPod API authentication |
| `DATABASE_URL` | `dashboard` | *(required unless a test source is injected)* | Postgres pool DSN for Overview state reads |
| `PITWALL_MCP_TRANSPORT` | `mcp serve broker` | `"stdio"` | MCP transport override |
| `PITWALL_API_URL` | `doctor`, `gateway status` | `doctor`: `http://127.0.0.1:8080`; `gateway status`: unset (local lock only) | Broker API base URL for the API probe and live quota rows |
| `PITWALL_API_TOKEN` | `doctor`, `gateway status` | unset | Bearer token sent with the API probe and the live `/v1/quotas` read |
| `PITWALL_GATEWAY_TOKEN` | `serve --gateway`, `gateway serve`, `gateway doctor` | unset | Bearer token the loopback gateway requires; the sidecar does not start without it. `gateway doctor` always runs its health check and fails without the token, while the gateway section of `pitwall doctor` skips it |
| `PITWALL_GATEWAY_URL` | `gateway doctor` | `http://127.0.0.1:20130` when unset (the `PitwallSettings.pitwall_gateway_url` default adds `/v1`) | Front-door URL; `front_door_loopback` fails unless it is loopback |
| `PITWALL_AGENTS_USAGE_TOKEN` | `usage serve` | unset | Bearer token for `GET /usage`; required for a non-loopback `--host` |

---

## 6. Failure Modes & Error Types

### CLI argument parsing errors
Commands built on `argparse` print a usage line to stderr and exit `2` when a required flag is
missing, a value fails type conversion, or an unknown flag or subcommand is given
(`uv run pitwall dashboard --bogus; echo $?` prints `2`). `pitwall db` parses its own arguments
and matches that: anything extra after `migrate`, `reset`, or `status` exits `2`. Two hand-written
dispatchers exit `1` instead: an unknown command group (`pitwall bogus`) and an unknown or missing
`pitwall db` command (`pitwall db bogus`, bare `pitwall db`). Once parsing succeeds, each command
returns the codes in the [exit-code table](#exit-codes).

### Missing environment variables
The direct CLI credential paths for `register-template`, `terminate-pod`, and raw RunPod
resources resolve a non-empty `RUNPOD_API_KEY` first, then fall back to the key saved by
`runpodctl` in `~/.runpod/config.toml`. This fallback does not apply to every long-running
service process; surfaces that explicitly require the environment variable still need it
there. `warm-volume` uses the normal registry-backed serve launch, including its configured
RunPod credentials and optional `--datacenter`; it does not use a separate worker image or
marker-script environment. Its output has `cache_state`: `warm` only after verification
records the selected variant and volume, otherwise `cold`.

### `cmd_init` failure modes
- `SeedValidationError` from seed file loading or manual seed application: prints `"ERROR: ..."`, returns `1`.
- No capability, no provider, or a failed provider health lookup after seed application: prints descriptive error, returns `1`.
- Unhandled async exception: printed as `"Error: ..."`, returns `1`.

### `cmd_create_capability` failure modes
- `--spec` combined with manual capability flags; missing `--name`, `--class`, or `--cost-mode`; empty capability name: prints `"ERROR: ..."`, returns `1`.
- Seed validation or an empty spec result in `--spec` mode: prints `"ERROR: ..."`, returns `1`.
- Unhandled async exception: printed as `"Error: ..."`, returns `1`.

### `cmd_seed` failure modes
- `SeedValidationError` from missing paths, empty directories, invalid seed shapes, enum validation, duplicate provider names, or provider config validation: prints `"ERROR: ..."`, returns `1` (`pitwall.cli.capabilities`, `pitwall.seed`).
- `--mark-healthy` provider health lookup failure: prints `"ERROR: Provider not found: ..."`, returns `1`.
- Unhandled async exception: printed as `"Error: ..."`, returns `1`.

### `cmd_terminate_pod` failure modes
- `RuntimeError` from `terminate_pod_sync`: prints error + `"Manual teardown via RunPod console required."` and returns `1`.
- Verification timeout: prints `"did not reach EXITED/TERMINATED within Ns"` + `"Manual verification via RunPod console required."` and returns `1`.
- `get_pod_sync` raising during polling: warns and continues (does not fail the command).

### `cmd_register_endpoint` failure modes
- Duplicate provider name: `"ERROR: Provider with name 'X' already exists (id=prov_...)"` to stderr, returns `1`.
- `RuntimeError` / exception from `asyncio.run`: caught, printed to stderr as `"Error: ..."`, returns `1`.

### `cmd_warm_volume` failure modes
- `create_pod_with_fallback_sync` fails for all GPU types: returns `2`.
- `terminate_pod_sync` fails after pod exit: returns `3`.
- Capability or provider not found: prints descriptive error, returns `1`.

### `cmd_serve_model` failure modes
- `BudgetRejected`, Pydantic request validation, and service errors with HTTP status `409` or `422`
  return `2`.
- Service launch failures (`503`), served-model verification failures (`502`), and unexpected
  command-boundary failures return `1`.
- JSON failures use the mapped exception's stable response body; plain mode prints the error.

### `cmd_dashboard` failure modes
- Argument parsing errors are handled by `argparse`.
- If the Overview source raises during refresh, including when the database is unreachable, the screen shows `Overview unavailable: <reason>` and keeps the shell running. The reason is the exception text on one line, with URL credentials, auth headers, and token-shaped values redacted (`src/pitwall/tui/errors.py`). The Pods / Leases screen does the same with `Pods / leases unavailable: <reason>`.
- If the Providers source raises during refresh, the screen shows `Providers unavailable (<ExceptionClass>).` and keeps the shell running. Only the class name is shown, so provider configuration and credential details are not reflected into the UI.
- If the Models source raises during list refresh, the screen shows `Model catalogue unavailable: <reason>` (redacted, as above) and clears stale rows while keeping the shell running.
- If the Models source raises while opening a row's dossier, the screen shows `Model detail unavailable: <reason>` (redacted, as above), stays on Models, keeps the loaded list rows usable, and never pushes the detail screen; a later Enter or `r` retries normally.
- An unhandled exception exits the console with a traceback that omits frame local variables, so settings such as `DATABASE_URL` are never printed (`PitwallApp._fatal_error`).
- A failed model dry-run never opens confirmation; launch failures remain on the preview screen.
  Secret-looking mapping values are redacted again before preview content reaches a widget.
- Without an injected test source, the default app path resolves the normal Pitwall DB pool, so `DATABASE_URL` must be configured before the Overview can read broker state.

---

## 7. Testing

| File | What it covers |
|---|---|
| `tests/cli/test_models_commands.py` | Catalogue parser defaults; list/default-variant selection and JSON shape; retained dossier body; all-row fit rendering; unpriced and zero-price handling; unknown-variant exit contract. |
| `tests/cli/test_cli_dispatch.py` | Characterization suite (1338+ lines). Dispatch routes for DB, `register-template`, `terminate-pod`, `register-endpoint`, `set-provider-health`, `serve`, `warm-volume`, MCP, and `config check`; argument parser defaults, `serve` flag/default mappings, JSON delegation, and exit-code mappings; dry-run output, terminate verification loop (timeout, pod gone, already exited, poll error, waiting message), async exception handlers, warm-volume dry-run, capability/provider not-found, duplicate provider name, cost mode, serverless LB/public endpoint provider types, `mcp serve` subcommand parsing, help/usage exit codes, the end-to-end `mcp serve` → `mcp.run(transport="stdio")` regression, and nothing reaching stdout before `mcp.run`; `_is_terminated` truth table. Includes `--json` flag validation for `register-template`, `serve`, `warm-volume`, `terminate-pod`, and `config check`. |
| `tests/cli/test_cli_output.py` | Output layer unit tests: plain text / JSON mode switching, table/panel/error/warning/success rendering, JSON emission, `add_json_argument` / `json_mode` helpers, `_safe_json` serialization for Pydantic models and plain objects. |
| `tests/cli/test_init_seed.py` | Onboarding command helpers: `create-capability` happy path and blank-name rejection, seed file application, and `init --from-seed --non-interactive` marking the created provider healthy. |
| `tests/tui/test_overview.py` | Textual Overview coverage: snapshot formatting, property-based status normalization, confirmation-tier stubs, Pilot mount/refresh tests with `StaticOverviewSource`. |
| `tests/tui/test_providers_screen.py` | Textual Providers coverage: default registry enumeration, snapshot summary pluralization, Pilot navigation from Overview with `p`, and row rendering with `StaticProvidersSource`. |
| `tests/tui/test_models_screen.py` | Textual Models coverage: Pilot navigation with `m`, catalogue row and empty-state rendering, source refresh, inline source failure, Enter to Markdown dossier detail with variant table and retained research body, and detail-load failure staying on Models with a redacted inline error, preserved rows, and retry/navigation. |
| `tests/tui/test_hardware_fit_screen.py` | Textual hardware-fit coverage: snapshot live/stale/unpriced states; Pilot `g` navigation, all-row rendering, `r` refresh, `c` cloud toggle, stale/unpriced labels, and inline source failure with stale-row clearing. |
| `tests/tui/test_serve_screen.py` | Recursive preview redaction plus Pilot coverage for dry-run-before-confirm, advisory `no` display, wrong confirmation refusal, exact confirmation launch, and inline launch failure. |
| `tests/tui/test_cli_dashboard.py` | Hermetic `pitwall dashboard` dispatch coverage with `PitwallApp.run` monkeypatched. |
| `tests/test_live.py` (exercises `tests/support/live.py`) | `is_live()` truth table (no env, only live flag, only base URL, both, alias var, truthy/falsy values, empty base URL); `require_live()` exit behavior. |
| `tests/runpod_client/test_cli.py` | Subprocess-level tests for `register-template`: missing API key exit, dry-run output for GHCR/GitLab/Docker Hub/unknown registries (auth selection), missing `--image` error, image/template detail output. Async tests: cache hit, template creation, missing API key. |
| `tests/runpod_client/test_warm_volume.py` | `_warm_volume_async` — tests auto-provider selection, explicit provider, missing volume ID handling. |
| `tests/db/test_reset_safety.py` | `cmd_reset` from `pitwall.db` is invoked via `pitwall db reset`; tests the single-drop invariant. |
| `tests/db/test_db_cli_help.py` | `test_db_help_flag_prints_usage_to_stdout`, `test_db_help_with_json_flag_prints_usage`, `test_db_no_args_prints_usage_to_stderr_and_returns_1`, `test_db_unknown_command_returns_1`, and `test_db_help_after_subcommand_prints_usage_without_running` characterize DB help (including help after a subcommand, which must not execute it), no-args, and unknown-command behavior. |
| `tests/db/test_asyncpg_migrate.py` | `cmd_migrate` async path with mocked pool: pending migration execution, skip already-tracked, error handling. |

---

## 8. Dependencies

### From `pitwall` (internal)

| Module | Used by | Purpose |
|---|---|---|
| `pitwall.runpod_client.templates` | `pitwall.cli` | `ensure_template`, `get_registry_auth_id_from_env`, `image_sha`, `normalize_template_name`, `template_display_name`, `_TEMPLATE_ENV_KEYS` |
| `pitwall.runpod_client.pods` | `pitwall.cli` | `terminate_pod_sync`, `get_pod_sync`, `create_pod_with_fallback_sync` |
| `pitwall.runpod_client.gpu` | `pitwall.cli` | `validate_canonical_gpu_name` |
| `pitwall.runpod_client.workloads` | `pitwall.cli` | `WorkloadConfig` |
| `pitwall.seed` | `pitwall.cli` | `SeedValidationError`, `apply_seed_files`, `apply_seed_data`, `apply_capability_seed_files` |
| `pitwall.db` | `pitwall.cli` | `get_pool`, `main` (for `pitwall db` subcommands) |
| `pitwall.db.repository` | `pitwall.cli` | `CapabilityRepository`, `ProviderRepository` |
| `pitwall.core.enums` | `pitwall.cli` | `CapabilityClass`, `CapabilityHint`, `CostMode`, `ProviderType`, `CapabilitySource` |
| `pitwall.core.ids` | `pitwall.cli` | `ulid_new` |
| `pitwall.core.models` | `pitwall.cli` | `Provider` |
| `pitwall.tui` | `pitwall.cli` | Textual dashboard app (`PitwallApp`) |
| `pitwall.core.cost_reporting` | `tui.overview` | Read-only cost and workload summary queries |
| `pitwall.db.repository` | `tui.overview` | Provider reads for Overview state |
| `pitwall.providers.registry` | `tui.providers` | Read-only provider plugin enumeration via `get_default_registry` / injected registry factory |
| `pitwall.mcp` | `pitwall.cli` | `ensure_runtime_env`, `mcp` (the MCP SDK 2 `MCPServer` instance) |

### External libraries

| Library | Version constraint | Purpose |
|---|---|---|
| `argparse` | stdlib | CLI flag parsing |
| `asyncio` | stdlib | Async command wrappers |
| `base64` | stdlib | Pre-warm script encoding |
| `json` | stdlib | Init smoke-command payload formatting, JSON mode emission |
| `time` | stdlib | Polling loops |
| `rich` | `>=15.0` (transitive via `textual`) | Tables, panels, and styled console output (`pitwall.cli.output`) |
| `textual` | `>=8.2,<9` | TUI app shell, widgets, key bindings, Footer, and Pilot tests |
| `mcp` | `>=1.0.0` (from `pitwall.mcp`) | MCP server (`cmd_mcp`) |

---

## 9. TUI Pods / Leases View

`src/pitwall/tui/leases.py` adds the read-only Pods / Leases screen to the existing
Textual `pitwall dashboard` shell. It follows the same source-injection pattern as the
Overview screen: production code uses `PostgresLeasesSource`, while tests inject
`StaticLeasesSource` and never open Postgres or RunPod connections.

The screen is reached with `l` from `PitwallApp`; `o` returns to Overview, `r` refreshes
the active screen, `?` opens binding-derived help, and `q` / `Ctrl-C` still quit through the app shell. Navigation is
read-only. There are no terminate, stop, renew, or provider mutation widgets on this
screen.

`PostgresLeasesSource` reads persisted active pod lease rows by joining `pitwall.leases` to the
provider and capability rows, excluding terminal states (`stopped`, `failed`, `expired`). It
displays the lease ID, RunPod pod ID, provider ID, served model, engine, variant, lease state,
persisted readiness label, UTC expiry, and accrued cost. Missing model-serving facts render as
`—`. Readiness is derived from the persisted readiness JSON: all runtime, port-mapping, and probe
timestamps produce `ready`; a subset produces `partial`; no signals produce `pending`.

Hermetic coverage lives in `tests/tui/test_leases_screen.py`:

| Test area | Coverage |
|---|---|
| formatting | Cost rounding, missing-cost label, readiness labels, UTC timestamp rendering |
| property | Status normalization always produces a non-empty display-safe key |
| source | `StaticLeasesSource` load counting for refresh assertions |
| Pilot | `l` navigation, served-model table rows, empty state, refresh, source-failure rendering, `o` back to Overview |
## Addendum: TUI Cost View

`pitwall dashboard` now includes a read-only Cost screen alongside Overview and Providers. The global Textual bindings are `o` Overview, `p` Providers, `c` Cost, `r` refresh on the active screen, and `q` / `Ctrl-C` quit.

`src/pitwall/tui/cost.py` owns the Cost screen. It follows the existing source/snapshot/static-source pattern:

| Object | Responsibility |
|---|---|
| `CostScreen` | Textual screen with burn-rate forecast, sub-budget chargeback, what-if summary, refresh, and generic source-failure messaging |
| `CostSnapshot` | Immutable view model built from `BurnRateRead`, `ChargebackReport`, and `WhatIfBatchProjection` |
| `CostSource` | Async protocol for loading one Cost refresh |
| `StaticCostSource` | Hermetic test/demo source |
| `PostgresCostSource` | Lazy Postgres-backed source for dashboard runtime |

Runtime data flow:

- Burn-rate uses `pitwall.finops.burn_rate.read_configured_burn_rate` over aggregated
  `pitwall.cost_daily` Decimal rows. The rendered summary shows daily rate, remaining budget,
  month-end forecast, projected UTC breach and ETA, trend/confidence, and explicit no-data/sparse/
  stale state from the shared `BurnRateRead` model.
- Sub-budget rows use `pitwall.cost.sub_budgets.generate_chargeback_report` over current-month workload cost rows. Tags are resolved from workload `budget_tag`, `tag`, or `team` values when present.
- What-if renders a `pitwall.cost.simulator.WhatIfBatchProjection` summary. If no projection inputs are configured, the screen renders an empty read-only projection with current spend, budget headroom, and zero reserved spend.
- `PostgresCostSource` is lazy: `PitwallApp` installs the screen at mount, but the Cost source does not resolve the DB pool until the Cost screen is opened or refreshed.

Cost failure modes:

- If the Cost source raises during refresh, the screen shows `Cost unavailable: <reason>` and keeps the shell running. The reason is the exception text on one line with URL credentials, auth headers, and token-shaped values redacted.
- Without an injected source, the Cost screen needs `DATABASE_URL` for broker state and `PITWALL_MONTHLY_BUDGET_USD` for runway/headroom calculations.

Cost TUI coverage:

| File | What it covers |
|---|---|
| `tests/tui/test_cost_screen.py` | Cost snapshot summaries, no-data/stale rendering, property-based percent bounds, source delegation to `BurnRateRead`, sub-budget table rendering, `StaticCostSource`, Pilot navigation via `c`, screen rendering, refresh, and generic source-failure messaging |

Additional internal dependencies:

| Module | Used by | Purpose |
|---|---|---|
| `pitwall.finops.burn_rate` | `tui.cost` | Shared persisted `BurnRateRead` forecast adapter |
| `pitwall.cost.sub_budgets` | `tui.cost` | Chargeback line items, reports, and sub-budget report generation |
| `pitwall.cost.simulator` | `tui.cost` | What-if batch projection summary |

## Addendum: TUI Resources View

`pitwall dashboard` includes a RunPod Resources screen that is read-only by default and
uses guarded dialogs for explicit mutations. The global Textual bindings are `o` Overview, `p`
Providers, `l` Leases, `c` Cost, `e` Resources, `r` refresh on the active screen, and `q` /
`Ctrl-C` quit.

`src/pitwall/tui/resources.py` owns the Resources screen. It follows the sibling
source/snapshot/static-source pattern:

| Object | Responsibility |
|---|---|
| `ResourcesScreen` | One Textual screen for pods, endpoints, account templates, read-only Hub templates, volumes, and registry auth, with lookup, filtering, refresh, and guarded mutations |
| `ResourcesSnapshot` | Immutable view model with sanitized entries, UTC refresh time, and stale/unavailable section state |
| `ResourcesSource` | Async read/query/preview/apply boundary; widgets call no provider clients |
| `StaticResourcesSource` | Hermetic scripted test/demo source with call counts |
| `RunPodResourcesSource` | Adapter over `RunPodControlPlaneService`, including concurrent partial reads and mutation dispatch |
| `ResourceMutationEditorModal` | Explicit operation plus strict JSON request editor; assigns an idempotency key and cannot set intent directly |
| `ResourceMutationConfirmModal` | Mandatory preview summary and exact type-to-confirm gate before apply |

Runtime data flow:

- `RunPodResourcesSource` concurrently calls the exact shared service list methods. A failed
  section becomes unavailable; previously loaded entries remain visible and are marked stale.
- Pods render ID, name, normalized status, and GPU. Endpoints retain worker bounds, scaler, GPU
  pools, and template identity. Account and Hub templates are separate tables. Volumes render
  size/datacenter. Registry auth renders only ID and name.
- Lookup exposes resource gets and Hub search. Hub has no mutation option.
- Every mutation calls the matching shared-service preview first. The confirmation modal shows
  provider, operation, resource, effect, estimated ceiling, and consequences. Apply is disabled
  until the resource ID or create name is typed exactly, then a successful/already-absent result
  triggers a refresh.

Resources failure modes:

- Initial loading and each empty table are explicit. Partial provider failures identify unavailable
  sections and stale retained data. A total source failure, lookup failure, preview failure, or
  mutation failure stays inline and leaves the shell operational.
- Without an injected source, reads need the normal RunPod credential reference. Live mutations
  additionally need the configured Pitwall audit pool.
- The screen renders sanitized service projections and stable errors, never provider credentials,
  raw authorization headers, or provider client objects.

Resources TUI coverage:

| File | What it covers |
|---|---|
| `tests/tui/test_resources_screen.py` | Shared-service-only mapping, all tables, loading/empty/stale/unavailable/malformed/provider-error states, filtering, refresh, mandatory preview, wrong-confirmation zero writes, exact-confirm success refresh, and already-absent results |

The complete REST/MCP/CLI/TUI operation matrix and examples are documented in
[RunPod account resource controls](../operator/runpod-resource-controls.md).

## Addendum: TUI Operations View

`pitwall dashboard` now includes a read-only Operations screen for the final
Initiative-2 operator summary. The global Textual bindings are `o` Overview,
`p` Providers, `l` Leases, `c` Cost, `e` Resources, `a` Operations, `r`
refresh on the active screen, and `q` / `Ctrl-C` quit.

`src/pitwall/tui/operations.py` owns the Operations screen. It follows the
sibling source/snapshot/static-source pattern:

The embedded `VolumeFilesOperationsPanel` delegates list, upload, safe local
download, delete, and bounded logs to `VolumeFileService`. It renders ordered
progress and derives empty/unavailable/stale-refresh presentation without
inventing service statuses. Upload/download overwrite is an explicit
default-off checkbox preserved from preview to apply. The type-to-confirm modal
shows provider, exact target, effect, estimated ceiling, and irreversible
consequence. Cancelling an in-flight mutation uses an ambiguity warning—the
write may have completed—and directs the operator to inspect current state and
durable audit before an exact-key retry.

| Object | Responsibility |
|---|---|
| `OperationsScreen` | Textual screen with Catalog, Jobs, Resilience, Routing, Policies, and Autopilot summaries plus refresh and generic source-failure messaging |
| `OperationsSnapshot` | Immutable view model with catalog rows, job state counts, recent jobs, provider resilience rows, routing/policy/autopilot summaries, and UTC refresh state |
| `OperationsSource` | Async protocol for loading one Operations refresh |
| `StaticOperationsSource` | Hermetic test/demo source |
| `PostgresOperationsSource` | Read-only Postgres-backed source composed from existing repositories, pure routing, policy, and autopilot layers |

Runtime data flow:

- Catalog uses `CapabilityRepository.list` and `ProviderRepository.list` to
  render capability ID, name, class, cost mode, enabled state, and provider
  count.
- Jobs use narrow read-only SQL over `pitwall.workloads` to render state counts
  and recent workload rows. The screen displays IDs, state, UTC submission time,
  and rounded cost only.
- Resilience uses provider health, consecutive failures, cooldown trips,
  cooldown timestamp, and recent error rate from persisted provider rows.
- Routing uses the existing pure `plan_route` / `RoutingRequest` stack for the
  first capability with providers. It renders selected provider, fallback chain,
  candidate count, eliminated count, and capacity decision count.
- Policies use `load_default_policy_set` and `evaluate_policies` against a
  sanitized in-memory snapshot of capabilities, providers, and recent jobs.
- Autopilot uses `AutopilotController` in shadow mode with the existing
  `WhatIfSimulator` and deterministic `PlanningContext`. The default dashboard
  path supplies no action signals; injected sources can render non-empty
  decision summaries hermetically.

Operations failure modes:

- If the Operations source raises during refresh, the screen shows
  `Operations unavailable: <reason>` and keeps the shell running. The reason is the
  exception text on one line with URL credentials, auth headers, and token-shaped values
  redacted.
- Without an injected source, the Operations screen needs the normal Pitwall
  database runtime environment used by the underlying read-only state queries.
- The screen does not render raw provider config, raw workload input, raw policy
  violation payloads, raw API URLs, or secrets.

Operations TUI coverage:

| File | What it covers |
|---|---|
| `tests/tui/test_operations_screen.py` | Operations snapshot summaries, display-label property, fixed-width catalog/jobs/resilience tables, injected read-only source mapping, Pilot navigation via `a`, screen rendering, refresh, and generic source-failure messaging |

## Addendum: RunPod onboarding CLI and TUI

The feature-local `pitwall.cli.onboarding.cmd_runpod_onboard` command reads one
bounded, secret-free JSON request file. `--action` defaults to `plan`; `apply`
and `resume` require `--confirmed-plan-id`. It renders either the shared result
JSON or a Rich summary/ordered step table and returns `0` on success, `2` for
invalid input or confirmation mismatch, and `1` for bounded service failure.
The legacy `init`, `seed`, and manual registration commands are unchanged
(`pitwall.cli.onboarding`).

`pitwall.tui.onboarding.RunPodOnboardingPanel` is an attachable
Providers/Resources panel over the same command/result model. Apply is enabled
only by a completed plan; resume is enabled only by resumable state; both open
an exact-plan-ID confirmation modal. Active work can be cancelled, after which
the panel directs the operator to status because a confirmed remote write may
have completed. Errors are generic and credential-safe
(`pitwall.tui.onboarding`).

The surface sequence and request examples are in
[RunPod onboarding](../operator/runpod-onboarding.md). CLI adapter coverage is
in `tests/onboarding/test_surfaces.py`; Textual Pilot coverage is in
`tests/tui/test_onboarding_panel.py`. Top-level CLI dispatch and dashboard-panel
attachment are serialized integration points.

---

## Addendum: Personal Serving Views (Serve, Pods, Routes)

`pitwall dashboard` adds three screens for the personal backend alongside the seven
described above: a Serve wizard, a Pods list, and a Routes list. The global Textual
bindings are `o` Overview, `p` Providers, `l` Leases, `c` Cost, `e` Resources,
`a` Operations, `s` Serve, `d` Pods, `t` Routes, `r` refresh on the active screen, and
`q` / `Ctrl-C` quit.

`PitwallApp` installs all three screens unconditionally at mount (`_install_personal()`),
but only the personal backend (the default; the registry backend is selected by `[personal] backend = "registry"` — see `select_backend()`,
`src/pitwall/personal/backend.py`) opens the console directly on `serve` instead of
`overview`. On that backend Overview, Providers, Leases, Cost, Resources, and Operations
are never installed, so their key bindings and the `:` command-palette entries for them
are no-ops; only Models (`m`), Serve (`s`), Pods (`d`), and Routes (`t`) are reachable.
On the registry backend the console still opens on Overview as
before, and `s`/`d`/`t` reach the same three screens alongside the rest.

`src/pitwall/tui/personal.py` owns the three screens:

| Object | Responsibility |
|---|---|
| `PersonalServeSource` | Async boundary: `preview`, `serve`, `status`, `stop`, `logs`, `key_env_present`, `gpu_choices` |
| `ServicePersonalSource` | Production adapter over `PersonalServeService` (`pitwall.personal.service`) |
| `StaticPersonalSource` | Hermetic scripted test source with exact call recording |
| `ServeWizardScreen` | Serve wizard: form entry, mandatory preview, exact route-name type-to-confirm launch |
| `PodsScreen` | Lists personal pods/leases, shows the selected pod's logs, stops one with type-to-confirm |
| `RoutesScreen` | Lists personal routes and their `route-shim.sh` invocation, with an endpoint-key warning |

Serve (`s`): `ServeWizardScreen` presents a form (Model, Variant, GPU class, Cloud, TTL
minutes, Maximum USD per hour, Route name). `Preview` calls `source.preview(spec)` and
renders the request preview JSON (its `docker_start_cmd` and any `PITWALL_ENDPOINT_KEY`
env value are redacted before display) plus price/hour, max spend, and deadline; a
`ServeRefused` preview — including an already-active route (`route_exists`), a
missing routing CLI (`routing_cli_missing`), a non-fitting GPU (`does_not_fit`),
unpriced GPU classes, or a price over the cap
(`price_over_cap`) — shows `refused: <code> <detail>` inline and never launches. `Launch`
is gated: the confirm-text input must exactly match the route name, and the previewed
spec must be the exact spec being launched (edit any field and preview again). A
successful launch appends progress lines ending in `<state>: <route> · <endpoint_url>`; a
`ServeFailed` shows `failed after launch: <code>; pod termination attempted`. `Escape` returns to
Models. The Cloud and TTL controls share the row at fractional widths so both remain
visible at narrow terminal sizes; the remaining wizard fields continue below the
scrollable panel when the viewport is short. A source failure during preview or launch
stays inline through the shared `source_failure_message` helper
(`src/pitwall/tui/errors.py`), so URL credentials, auth headers, and token-shaped values
in the exception text are redacted rather than rendered.

Pods (`d`): `PodsScreen` lists personal leases (Route, Model, GPU, State, Ends in,
Price/h) from `source.status()`. Selecting a row loads its logs via `source.logs(route)`.
`Stop` opens a type-to-confirm prompt requiring the exact route name before
`source.stop(route)` runs; a source failure during refresh, logs, or stop stays inline and
redacted through `source_failure_message` (`pods unavailable: …`, `logs unavailable: …`).
`r` refreshes, `Escape` returns to Serve.

Routes (`t`): `RoutesScreen` lists personal routes (Route, Endpoint, Served model, State)
from the same `source.status()` leases. Selecting a row shows its last-probed state, the
`route-shim.sh <route> prompt.md` invocation line, and a
`Warning: PITWALL_ENDPOINT_KEY is not set` line when `source.key_env_present()` is false.
`r` refreshes, `Escape` returns to Serve.

Hermetic coverage lives in `tests/tui/test_personal_screens.py`: wizard preview-before-
confirm and wrong-route-name refusal, refusal-without-launch, pods and routes empty
states with `r` refresh, pods wrong-route-name stop refusal with no `stop` call, pods
listing/logs/stop-with-confirmation, inline redacted failures for pods/routes status,
logs, and wizard launch, routes probe/shim-line/key-warning, and the dashboard's
entry-screen selection following `select_backend()`.

---

## Cost, burn-rate, and guardrail commands

`pitwall cost`, `pitwall burn-rate`, and `pitwall guardrails` are small, feature-local,
read-only command groups. Each owns its own module and keeps `--json` via
`add_json_argument`.

### `cost summary` / `cost workloads`

`src/pitwall/cost/cli.py` supplies `pitwall cost`:

| Command | Effect |
|---|---|
| `cost summary [--capability-class NAME] [--since DATE] [--until DATE] [--json]` | Daily aggregate cost via `cost_summary_read`; plain output is a `Cost Summary` table (Day, Capability, Provider type, Workloads, Cost USD) captioned with the total USD |
| `cost workloads [--capability-id ID] [--provider-id ID] [--provider-type TYPE] [--state STATE] [--since DATETIME] [--until DATETIME] [--limit 1-100, default 20] [--json]` | Per-workload estimate/ceiling/actual/reconciliation via `recent_workloads_read`; plain output is a `Recent Workload Costs` table |

`summary --since`/`--until` take an ISO date (`YYYY-MM-DD`); `workloads --since`/`--until`
take an ISO datetime that must carry a UTC offset and is normalized to UTC. Both render
Decimal costs as strings; JSON is each read model's `to_legacy_serializable_dict()`.
`cmd_cost` returns `0` on success; unlike the other groups below, a persistence exception
is printed verbatim to stderr in plain mode rather than folded into a stable code, and
returns `1`.

### `burn-rate`

`pitwall.cli.burn_rate` supplies `pitwall burn-rate [--window-days N] [--json]`. It
reads the persisted monthly burn-rate forecast via `read_configured_burn_rate` over
`--window-days` (1-366, default 30) of UTC daily cost rollups. Plain output is a panel
with as-of time, observation window, spend to date vs. budget, daily rate, forecast
month-end, projected breach date/ETA, confidence/trend, and data sufficiency/freshness;
JSON is `BurnRateRead.to_dict()`. Returns `0` on success. Any persistence or configuration
failure returns `1` with the stable error `burn_rate_unavailable`
(JSON `{"error": "burn_rate_unavailable"}`) so secret-bearing exception text is never
reflected.

### `guardrails status` / `guardrails preview`

`pitwall.cli.guardrails` supplies `pitwall guardrails`, backed by
`PreSpendInspectionService` (`pitwall.security.pre_spend`):

| Command | Effect |
|---|---|
| `guardrails status [--json]` | Configured rules, mode, and cumulative inspected/allow/redact/block counters; plain output adds a `Guardrail rules` table (Rule, Kind, Balanced action, Description) |
| `guardrails preview --payload JSON [--json]` | Inspects one JSON payload with zero provider, database, audit, or counter writes; plain output shows the decision, finding count, and inspected byte count, plus a `Guardrail findings` table (Rule, Kind, Path, Action, Fingerprint) |

`status` always returns `0`. `preview` returns `2` when `--payload` fails to parse as JSON
or exceeds the service's configured input-size limit (`parse_pre_spend_json`); otherwise
it returns `0` regardless of the inspected decision — `allow`, `redact`, and `block` are
all a successful preview, not a CLI failure.

---

## Provider operations command group

`pitwall.cli.provider_ops` supplies `pitwall provider-ops`, a read-only adapter over
`ProviderOperationsService`:

| Command | Effect |
|---|---|
| `provider-ops list [--capability-id ID] [--enabled-only] [--limit 1-100, default 100] [--json]` | Lists safe persisted provider descriptors |
| `provider-ops describe PROVIDER_ID [--json]` | Describes one provider safely |
| `provider-ops availability PROVIDER_ID [--limit 1-100, default 100] [--json]` | One explicit bounded, read-only availability probe |
| `provider-ops health PROVIDER_ID [--probe] [--json]` | Reads persisted health; `--probe` makes one explicit live read |

Plain output for `list` is a `Providers` table (Provider ID, Adapter, Persisted health,
Credential set, Capabilities); `describe`, `availability`, and `health` render a summary
panel, and `availability` adds an `Availability` table (Resource, Kind, Available, Region,
Accelerator, Pricing). `provider_id` and `capability_id` reject empty strings and NUL
bytes; `--limit` is bounded to 1-100.

`cmd_provider_ops` returns `0` on success. An unknown `provider_id` returns `2` with the
stable JSON `{"error": "provider_not_found", "id": "<id>"}`. Any other `ValueError`
returns `2` with `{"error": "provider_operations_invalid_request"}`. Any other exception —
which can include provider or database detail — is folded into exit `1` with
`{"error": "provider_operations_unavailable"}`, never reflecting the underlying message.

---

## Volume-files command group

`pitwall.cli.volume_files` supplies `pitwall volume-files`, a bounded adapter over
`VolumeFileService` (`pitwall.runpod_files`) — the same service the Textual Operations
screen's `VolumeFilesOperationsPanel` uses (see the Operations addendum above):

| Command | Effect |
|---|---|
| `volume-files list VOLUME_ID DATA_CENTER_ID [--prefix PREFIX] [--max-items N, default 200] [--json]` | Lists one bounded page of volume objects |
| `volume-files upload VOLUME_ID DATA_CENTER_ID OBJECT_KEY LOCAL_PATH [--root DIR] [--overwrite] [--confirm-overwrite] [--expected-sha256 SHA] [--idempotency-key KEY] [--dry-run] [--json]` | Uploads one bounded local regular file |
| `volume-files download VOLUME_ID DATA_CENTER_ID OBJECT_KEY LOCAL_PATH [--root DIR] [--overwrite] [--confirm-overwrite] [--expected-sha256 SHA] [--dry-run] [--json]` | Downloads one bounded object to a safe local path |
| `volume-files delete VOLUME_ID DATA_CENTER_ID OBJECT_KEY [--confirm-delete] [--idempotency-key KEY] [--dry-run] [--json]` | Deletes one object after explicit confirmation |
| `volume-files logs POD_ID [--max-lines N, default 100] [--max-bytes N, default 65536] [--json]` | Reads bounded, redacted pod diagnostics |

`object_key` rejects path traversal; `local_path` must resolve under `--root` (default the
current directory), which must already exist and must not be a symlink. `upload` and
`delete` require `--idempotency-key` for a live (non-`--dry-run`) call. Replacing an
existing target on `upload`/`download` needs both `--overwrite` and `--confirm-overwrite`
together — `cmd_volume_files` only treats the object or local target as replaceable when
both are set. Plain output is a panel (Operation, Status, Bytes, Checksum, Truncated) plus
a `Volume objects` table for `list` or a `Pod logs` table for `logs`.

Exit codes come from `VolumeFileError.status_code`: `409`, `413`, and `422` map to CLI
exit `2`; every other `VolumeFileError`, and any other exception (folded into the stable
`{"error": "volume_file_provider_error"}`), map to exit `1`. A `KeyboardInterrupt` during
a transfer returns `130` with `{"error": "volume_file_cancelled"}`; a successful call whose
result status is `cancelled` also returns `130`.

---

## Production routing command group

`pitwall.cli.routing` supplies the feature-local `routing` command group:

| Command | Effect |
| --- | --- |
| `routing plan` | Payload-free deterministic preview; supports operation and explicit provider |
| `routing submit` | Async dry-run or admitted submission; a live submit requires exact capability confirmation |
| `routing status` | Read the shared persisted workload and plan identity |
| `routing result` | Read the shared bounded, input-free result page and plan identity |
| `routing cancel` | Provider-neutral cancellation with exact workload confirmation |
| `routing follow` | Bounded polling: 1–100 attempts and 0–60 second interval |

Every command supports stable human output and `--json`. Request bodies are accepted only as JSON
objects and are never rendered in plan output. The command group delegates lifecycle operations to
`ProductionRoutingService`; it does not perform transport-specific selection
(`pitwall.cli.routing`).
Guardrail denial returns exit 2 and the stable JSON envelope
`{"error":"pre_spend_payload_rejected"}` without rule or payload detail
(`pitwall.cli.routing`).

---

## Addendum: Free-tier gateway TUI surfaces

The Providers screen grows three free-pool columns after the pod column: `HEADROOM`, `RESET`,
and `TOS`. Headroom renders as a ten-cell bar with a percentage (`[####------] 40%`), missing
quota state renders as `—`, `RESET` renders a compact days+hours countdown to the pool's reset
window (`2d 3h`, `now`), and `TOS` renders the catalog `tos` verdict with a visible placeholder
when absent. Rows are keyed by `(provider_id, pool_key)` from the injectable
`QuotaRepositoryFactory`; production sources read `pitwall.provider_quotas`, and tests inject
static factories, so the screen never opens Postgres
(`pitwall.tui.providers`).

The Cost screen gains a read-only `free-tier burn-down` panel under the summary blocks. It
renders one fixed-width row per pool with the pool key, used/budget units in compact form
(`1.0M`), and the reset timestamp; an empty configuration renders
`free-tier burn-down: no free pools configured`. The panel reads the same quota repository seam
as the Providers columns (`pitwall.tui.cost`).

Hermetic coverage lives in `tests/tui/test_providers_quota_columns.py`,
`tests/tui/test_providers_screen.py`, `tests/tui/test_cost_free_burn_down.py`, and
`tests/tui/test_cost_screen.py`.

Serve preview and launch errors use the shared sanitized failure formatter, including bearer credential redaction. A failed Models or Hardware Fit refresh clears cached rows, selection and details; subsequent terminal resize cannot redisplay the invalidated snapshot.

Personal Pods, Routes, and Serve wizard exceptions use the same sanitized failure formatter. Onboarding errors replace the loading message with an explicit no-result state; failures never leave a completed action displayed as loading. Recovery and cancellation are covered by hermetic Pilot tests without cloud resource actions.

In personal mode, MCP `pitwall_serve_model(dry_run=True)` calls the personal preview service and returns the plan/cost/deadline preview with `dry_run: true`, `state: dry_run`, and the requested route. It never calls the personal launch service or opens a registry connection; normal serves retain their lease response (`src/pitwall/mcp/tools/serve.py`).

MCP pod leases without an explicit provider select an enabled `pod_lease` provider before applying the result limit; a higher-priority serverless provider cannot satisfy a pod lease request. Personal MCP `dry_run` uses the shared service planning method and returns a preview without launching a pod or registering a route.

Personal `serve` validates model-launch arguments and resolves credentials before starting an optional Gateway. Invalid arguments return exit2 with field-level guidance and no Gateway startup; a missing credential also exits2 without starting the sidecar.

With `--gateway`, the shared personal service completes serve planning before starting the sidecar once. A plan refusal leaves the Gateway untouched. Startup errors return `gateway_start_failed` with exit 2 before pod creation. When the runpodctl credential is selected because the shell value is empty, the selected key is put in the child process environment used by the serving adapter.
