# Agent profiles

For a complete local-endpoint recipe, cold-start expectations, harness timeout
settings, and liveness guidance, see [Self-hosted endpoints](self-hosted.md);
to find and attach one from scratch, see [Attach a locally hosted model](attach-local-endpoint.md).
For the OpenCode Go subscription model list and its `opencode-go/*` route
families, see [OpenCode Go subscription models](opencode-go.md).

An agent profile is a `name@harness` binding: it names a model/harness combination once so any host can dispatch it by name. Profiles live in the `[agents.profiles]` tables of `pitwall.toml`:

```bash
pitwall agents profiles add glimmer --model meta-models/Muse-Glimmer-30B --base-url http://localhost:9292/v1 --api-key-env MY_ENDPOINT_KEY --seat local
pitwall agents profiles list
route-shim.sh glimmer prompt.md            # default harness for an endpoint entry (qwen)
route-shim.sh glimmer@opencode prompt.md   # override the harness (after profiles sync)
route-shim.sh gpt-6.1-sol prompt.md        # any registry model id works without an entry
```

`route-shim.sh <name>[@harness] <prompt-source> [--routing-*] [harness flags]` is the profile entrypoint over the fourteen harnesses. It resolves the profile, prints one stderr line (`route-shim: glimmer -> qwen meta-models/Muse-Glimmer-30B (endpoint gpu-1:8000)`), and hands off to the same dispatch path the provider shims use, so the `SHIM-DONE` sentinel, `SHIM_RESULT` receipts, run records, timeouts, worktrees, and lifecycle hooks all apply unchanged.

## Harnesses

`pitwall agents harnesses [--json]` lists every harness with its installed path, version, kind, endpoint delivery, effort control, and route count.

| Harness | Kind | Prompt delivery | Endpoint sync mechanism | Notes |
|---|---|---|---|---|
| Codex | model-bound | stdin | none | Native to Codex. |
| Claude Code | model-bound | stdin | none | Native to Claude Code. |
| Antigravity CLI (`agy`) | model-bound | argv | none | Gemini provider. |
| Grok Build | model-bound | file | none | Grok provider; receives a private prompt-file path. |
| Kimi Code | model-bound | argv | none | Kimi provider. |
| OpenCode | model-agnostic | stdin | managed `opencode.json` | Endpoint provider blocks are written atomically. |
| Qwen Code | model-agnostic | argv | none | Endpoint values use process environment variables. |
| Pi | model-agnostic | file | managed `~/.pi/agent/models.json` | Stores `$API_KEY_ENV` references, not key values. |
| Hermes Agent | model-agnostic | argv | managed `~/.hermes/config.yaml` | Stores `key_env` references, not key values; holds several managed providers at once, unlike Cline/dsh. |
| Cline CLI | model-agnostic | argv | `cline auth --provider openai-compatible` | One custom endpoint at a time; sync persists the API key value in Cline's own store. |
| Muse Code | model-bound | file | none | Receives a prompt-file path: a private copy for stdin prompts and ask-support runs. |
| goose | model-agnostic | stdin | none | Endpoint values use process environment variables. |
| ZCode (`zcode`) | model-bound | argv | none | The saved ZCode model and account; see [ZCode](zcode.md). |
| DeepSeek Harness (`dsh`) | model-agnostic | argv | managed `settings.yaml` | Experimental; managed mapping carries `apiKeyEnv`, never a key value. |

## Where things live

| Layer | File | Owner |
|---|---|---|
| Registry | `src/pitwall/agents/resources/config/harness-registry.json` | maintainer — harnesses (`harnessKind`, `endpointDelivery`) and fully documented `modelFamilies` |
| Catalog | `src/pitwall/agents/resources/config/model-catalog.json` | maintainer — lightweight open-weight entries the picker offers |
| Profiles | the `[agents.profiles]` tables of `pitwall.toml` (`PITWALL_CONFIG_FILE`, `./pitwall.toml`, else `${XDG_CONFIG_HOME:-~/.config}/pitwall/pitwall.toml`; `PITWALL_AGENTS_PROFILES` names an alternative file) | you, `pitwall agents setup profiles`, or an automation such as a GPU broker |

```toml
[agents.profiles.defaults]
harness = "opencode"
endpointHarness = "qwen"

[agents.profiles.endpoints.local-cluster]
baseUrl = "http://localhost:9292/v1"
apiKeyEnv = "MY_ENDPOINT_KEY"

[agents.profiles.models.glimmer]
model = "meta-models/Muse-Glimmer-30B"
endpoint = "local-cluster"
seat = "local"

[agents.profiles.models.glm]
model = "zai-coding-plan/glm-5.3"
harness = "opencode"
seat = "default-author"

[agents.profiles.models.sol]
model = "gpt-6.1-sol"
args = ["-c", "model_reasoning_effort=high"]
seat = "critical"
```

Only the `[agents.profiles...]` tables are ever rewritten. Everything else in `pitwall.toml` is kept as written, and a save that would change any other value is refused. A `pitwall.toml` that is not valid TOML is reported by position only (`invalid TOML at line N, column M`); the message never quotes the file. Sections are `defaults`, `endpoints`, `harnesses`, `models`, and `pitwall`.

The optional `endpoints` table gives reusable endpoint objects names that
match `[A-Za-z][A-Za-z0-9._-]{0,63}`. Each object has `baseUrl` and an optional
`apiKeyEnv`, with the same validation and secret scanning as an inline endpoint. In either form a `baseUrl` must not carry userinfo (`user:password@`) or a credential-named query parameter (`key`, `token`, `secret`, `password`, `passwd`, `pwd`, or a name ending in one of them); keys go in `apiKeyEnv`.
An entry's `endpoint` may be one of those names or the existing inline object.
`pitwall agents profiles add <name> --endpoint <shared-name>` selects a shared endpoint;
`--endpoint` is mutually exclusive with `--base-url` and `--api-key-env`.

Entry fields: `model` (required), `harness`, `effort`, `endpoint` (shared name or inline object), `args` (inserted before caller flags), `env` (non-secret extra variables), `limits`, `account` (a label of 1 to 16 letters, digits, or hyphens for the subscription account the route reaches; see [Subscription usage](usage.md)), `seat` (`default-author | critical | review | burst | throughput | local | gateway`), `workspace`, `taskMode`, `expiresAt`, `origin`, and Pitwall-only `autoServe`. Route names match `[A-Za-z][A-Za-z0-9._-]{0,63}` and never contain `@`. Keys never live in the file: `apiKeyEnv` names the environment variable that holds the key, and the validator rejects any key that looks like a secret. The normalized file retains a shared endpoint's name, while dispatch and `pitwall agents profiles sync` use its resolved object. `pitwall agents profiles show` displays the named entry form and a `resolvedEndpoint` object.

`expiresAt` is an optional UTC lease-expiry timestamp in the form
`YYYY-MM-DDTHH:MM:SSZ`. An expired route is refused by `route-shim.sh` with exit
`78`; the resolver allows 60 seconds of clock skew. `origin` is optional,
non-secret provenance. This release recognizes only Pitwall origins:
`{"kind":"pitwall","capability":"<name>","leaseId":"<id or null>","url":"<PITWALL_API_URL>","state":"active|stopped|unknown"}`.
The API token is never part of this object or of `pitwall.toml`.
`autoServe` is the explicit spend opt-in and carries only caps:
`maxUsdPerHour`, `ttlMinutes`, `idleTimeoutMinutes`, and
`readyTimeoutMinutes`. Dispatch self-heals stale Pitwall routes and exits `78`
with `not revived: <code>` when they cannot be revived; see
[the Pitwall handoff guide](pitwall.md).

## Model Studio endpoints

An endpoint with `"kind": "model-studio"` replaces a literal `baseUrl` with Alibaba Cloud Model
Studio configuration; the base URL is derived from the committed catalog and persisted into the
normalized file, where it must re-validate (an edited `baseUrl` that differs from the
derivation is refused). Setup and the plan/terms background live in the broker-side operator
guide, [`docs/operator/model-studio.md`](../operator/model-studio.md); the reference:

| Field | Meaning |
|---|---|
| `kind` | `model-studio` |
| `plan` | `token-plan-personal`, `token-plan-team`, or `pay-as-you-go` (required) |
| `apiKeyEnv` | environment variable holding the key (required; `MODEL_STUDIO_API_KEY` by default) |
| `tier` | plan tier (`lite`/`essential`/`standard`/`pro` personal; `standard`/`advanced`/`premium` team); derives concurrency and the Credits budget |
| `region` | region id; Token Plan is `ap-southeast-1` only |
| `workspace` | workspace id for a dedicated pay-as-you-go host |
| `protocol` | `openai` (default) or `anthropic` — selects the derived base URL path |
| `concurrency` | explicit per-endpoint slot count; else derived from the tier |
| `renewsOn` | `YYYY-MM-DD` Token Plan cycle start; exhaustion lockouts lift here |
| `tokenPlanAutomation` | `accept` — records acceptance of the Token Plan automation risk |

Create one with `pitwall agents profiles add-model-studio-endpoint <name>` (flags mirror
the fields: `--plan`, `--tier`, `--region`, `--model-studio-workspace`, `--protocol`,
`--concurrency`, `--renews-on`, `--api-key-env`, `--accept-token-plan-automation`;
`MODEL_STUDIO_*` environment variables supply defaults). Routes on the endpoint get their
`limits` (context/output) and `effort` vocabulary from the catalog model: `pitwall agents profiles add flash
--model qwen3.8-flash --endpoint ms` derives `context=991808,output=131072` automatically, and
a pinned `--effort` must be one of the model's own values. Without `--harness` the route is
pinned to `defaults.endpointHarness` (`qwen` out of the box); pass `--harness opencode` to
choose. The `anthropic` protocol pairs with the opencode harness only and takes no `effort`.

Dispatch holds one `flock` slot per in-flight session; when all slots are busy it exits `75`
(`endpoint_busy`), and the kernel frees slots of crashed dispatches. A Token Plan quota
exhaustion writes a local lockout until the renewal date; `pitwall agents profiles probe` reports the endpoint
`quota-exhausted` (with the renewal date) and `misconfigured` for key/plan/URL refusals, on top
of the usual `ok`/`warming`/unreachable states.

## Harness defaults

The `harnesses` table supplies defaults shared by profiles that resolve to a harness:

```toml
[agents.profiles.defaults]
harness = "opencode"
endpointHarness = "qwen"

[agents.profiles.harnesses.codex]
effort = "high"

[agents.profiles.harnesses.agy]
effort = "low"
args = ["--sandbox"]

[agents.profiles.models.sol]
model = "gpt-6.1-sol"
seat = "critical"

[agents.profiles.models.fast]
model = "gemini-3.8-flash"
effort = "medium"
```

Each `harnesses.<id>` must name a registry harness. Its `effort`, and a route's `effort`, must be allowed by that harness's registry effort declaration; a harness with no enumerated values accepts any non-empty effort, while a harness whose effort kind is `none` rejects it. For a registered model, route effort must also be one of the model's `effortValues`.

Effective effort follows route `effort` → `harnesses.<harness>.effort` → none. If caller flags already contain the harness's effort option, the resolver leaves that override alone. The final argv order is prompt, model selector, endpoint hook argv, harness `args`, effective effort, route `args`, then caller flags.

## How a spec resolves

1. `name` → an `[agents.profiles.models]` entry; else a registry model id or alias; else a model id claimed by a harness's route family (`gemini-3.8-flash-high` → agy, `qwen3-coder-next` → qwen); else, when the name contains `/` and the default harness is model-agnostic, a pass-through; else exit `64`.
2. Harness = `@override` → the entry's `harness` → (`endpoint` present ? `defaults.endpointHarness` : the model's vendor harness, else `defaults.harness`).
3. Validation before any child starts: a model-bound harness (codex, claude, grok, kimi, agy, muse, zcode) must own the model and cannot take an endpoint; an endpoint needs an agnostic harness. Qwen and goose receive endpoint values through per-process environment variables; opencode, Pi, Cline, dsh, and Hermes must be synced first; a named `apiKeyEnv` must be set. Failures here exit `78` with the fix on stderr and write no ledger row.
4. The harness argv is built from the registry's model selectors, then endpoint hook argv, harness `args`, effective effort, route `args`, and caller flags. For opencode endpoint entries the model argument is `<name>/<model>` because the synced provider key is the route name, while dsh's profile supplies its model and emits no model argv.

Exit codes: `64` malformed spec / unknown name / invalid combo; `78` incomplete configuration; everything else exactly as the provider shim would return.

## Commands

| Command | Purpose |
|---|---|
| `pitwall agents harnesses [--json]` | installed path, version, kind, endpoint delivery, effort control, and route count for every harness |
| `pitwall usage [--json]` | one row per subscription plan and account: percent used per window, reset time, status, and the routes that reach it; see [Subscription usage](usage.md) |
| `pitwall usage serve [--host H] [--port 8848] [--interval 45]` | sample usage on a schedule and answer `GET /usage` (the desk meter payload), `GET /plans`, and `GET /health`; any address other than loopback needs `PITWALL_AGENTS_USAGE_TOKEN` |
| `pitwall agents profiles list [--json]` | name, model, harness, effective effort, seat, endpoint host, sync status, readiness |
| `pitwall agents profiles show <name>` | the entry (plus `resolvedEndpoint` for a shared endpoint), resolved argv, effective effort and source, and environment variable names (values redacted) |
| `pitwall agents profiles resolve <spec> [--json]` | resolution, including effective effort and source — the shape skills, doctor, and automation consume |
| `pitwall agents profiles add … [--endpoint NAME \| --base-url URL [--api-key-env VAR]] [--effort VALUE] [--env KEY=VALUE]… [--limits context=N,output=N] [--account LABEL] [--arg ARG]… [--workspace shared\|isolated\|auto] [--task-mode read\|write]` / `pitwall agents profiles remove <name>` | edit the `[agents.profiles]` tables of `pitwall.toml` atomically (mode 0600), optionally selecting a shared endpoint, pinning typed effort, adding repeatable non-secret env or harness arguments, capping context/output, or setting the route's default workspace mode and task mode |
| `pitwall agents profiles add <name> --from-pitwall <capability> [--auto-serve SPEC]` | read Pitwall capability metadata and add its leased OpenAI endpoint; `--auto-serve` opts into capped revival spending |
| `pitwall agents profiles add-model-studio-endpoint <name> [--plan P] [--tier T] [--region R] [--model-studio-workspace W] [--protocol openai\|anthropic] [--concurrency N] [--renews-on DATE] [--api-key-env VAR] [--accept-token-plan-automation]` | create a `model-studio` endpoint whose base URL, limits, and effort vocabulary derive from the catalog |
| `pitwall agents profiles refresh <name> [--pitwall-url URL] [--json]` | re-read a Pitwall-origin route's capability metadata and update its model/expiry/lease in place, preserving seat, effort, args, and env |
| `pitwall agents profiles refresh --all-pitwall [--pitwall-url URL] [--json]` | refresh every Pitwall-origin route once, continuing after per-route errors |
| `pitwall agents profiles probe <name> [--json] [--timeout SECONDS]` | explicitly check endpoint liveness and the configured model, distinguishing a cold `warming` state; self-heal may also probe once after reviving a route |
| `pitwall agents profiles discover (--base-url URL [--api-key-env VAR] \| <route-name>) [--json] [--timeout SECONDS]` | list an endpoint's served model ids and, when its swapper exposes `/running`, each model's ready, starting, or not-loaded state |
| `pitwall agents profiles sync [--harness H] [--dry-run] [--yes]` | materialize endpoint entries into harnesses that only read their own config |
| `pitwall agents broker receiver [--port 8765] [--install] [--enable]` | run the signed loopback webhook receiver, or install its user service |
| `pitwall agents broker subscribe --receiver-url URL` | create the four-event webhook subscription (token needs `webhook:admin`) |
| `pitwall agents broker watch [--interval 5m]` | periodically refresh all Pitwall routes as the pull fallback |
| `pitwall agents setup profiles` | the interactive picker (needs `/dev/tty`) |

`profiles add` also takes three options that shape the child's argv and workspace:

- `--arg ARG` (repeatable) appends one argument to the entry's `args`. Route `args` go into the argv after the effective effort and before caller flags (see [How a spec resolves](#how-a-spec-resolves)). Repeat the option once per argument; there is no default.
- `--workspace shared|isolated|auto` stores the entry's `workspace`. At dispatch it becomes `--routing-workspace <value>` unless the caller already passed that flag. Without it the dispatch runs `shared`; see [Isolated worktree dispatch](worktree-dispatch.md).
- `--task-mode read|write` stores the entry's `taskMode`. At dispatch it becomes `--routing-task-mode <value>` unless the caller already passed that flag. `--workspace auto` needs a task mode, from here or from the caller, because it fails without one.

## `profiles sync`

opencode learns endpoints from `~/.config/opencode/opencode.json`. `pitwall agents profiles sync` writes one managed provider block per endpoint profile — `provider.<name>` with `npm: "@ai-sdk/openai-compatible"`, the base URL, `apiKey: "{env:VAR}"` (a reference, never the value), and the model — after showing a unified diff and asking for confirmation (`--yes` for scripts; non-TTY runs without it exit `2`). Pi writes its managed `models.json` entries with `$API_KEY_ENV` references; it updates only the provider endpoint/API, an explicitly supplied key reference, and the selected model's ID/context/output fields. Existing provider fields, selected-model metadata, and other models are preserved, and endpoint/API collisions fail for manual resolution. Omitting `apiKeyEnv` is an explicit keyless route policy: sync emits no dummy key and preserves any existing user-owned auth field. dsh writes a managed `settings.yaml` mapping with `apiKeyEnv`; Hermes merges a managed `providers:` block into `~/.hermes/config.yaml` with `key_env`, and — unlike Cline and dsh — can hold several managed providers at once, with dispatch auto-selecting each by an injected `--provider <route-name>`; Cline runs its documented `cline auth --provider openai-compatible` command. Cline accepts only one custom endpoint, and that command persists the API key value in Cline's own store; read and accept that warning before confirming sync. Every endpoint route is materialized where its harness allows it so overrides work. The merge is idempotent, preserves unrelated providers, backs up the previous file, and refuses a file that is not strict JSON (JSONC comments) or an unmanaged provider block — resolve those by hand. `pitwall agents profiles remove` never touches harness config; delete managed configuration yourself if you no longer want it.

For an intentionally keyless Pi server, the Pi runtime may still require a configured authentication source. Explicitly set `apiKey: "dummy"` in that provider entry in the isolated Pi `models.json` when the server accepts a dummy key; subsequent profile sync preserves it. A `synced` result checks configuration ownership and drift, not credential validity or successful inference.

## Seats

`seat` is optional metadata for the orchestrator: the Claude skill reads `pitwall agents profiles list` when `[agents.profiles]` exists and lets seats there override its seed rankings. Nothing enforces a seat. Recognized values are `default-author`, `critical`, `review`, `burst`, `throughput`, `local`, and `gateway`; the `gateway` seat marks routes attached to the loopback free-tier gateway fork so the orchestrator can group and budget them separately from other endpoint entries.

## Doctor

`pitwall agents doctor` adds a read-only `harness.summary` check before the route and provider checks, reporting installed and missing harnesses and warning when a route pins a missing harness. It also checks `runtime.routes_config`, `routes.<name>.harness_installed`, `routes.<name>.sync_status`, `routes.<name>.api_key_env`, `routes.<name>.expiry`, `routes.<name>.pitwall_sync`, `pitwall.receiver`, and `security.routes_file_mode`. The expiry check warns when a recorded expiry is past or within 15 minutes; the Pitwall sync check warns when that lease window also has no sidecar update in 30 minutes. Route checks stay offline unless `--probe-routes`, which performs bounded endpoint probes and can report a cold route as `warming`; see [Self-hosted endpoints](self-hosted.md#discovery-and-liveness). Receiver health uses only `http://127.0.0.1:<port>/health`, and only after the sidecar shows that a receiver has run.

## Host boundaries

Claude Code keeps Claude work native: its DAG tripwire flags `route-shim.sh` with a Claude model spec (`ROUTE BOUNDARY`). Codex keeps GPT work inline. `pitwall agents profiles resolve <spec> --json` reports `nativeTo` so any host can apply the same rule. One exception: a saved route whose `env` sets `CLAUDE_CONFIG_DIR` (or `CODEX_HOME` on a Codex host) reaches a second account of that plan, so it is not native work. The Claude Code tripwire reads the `[agents.profiles]` tables of `pitwall.toml` and lets such a route through; the workflow runner still rejects Claude tasks from a Claude host.

## Limitations

- Qwen Code's per-process delivery assumes environment variables override `~/.qwen/.env`; verify with a pong dispatch on first use.
- Cline holds one custom endpoint at a time under its fixed `openai-compatible` provider. `pitwall agents profiles sync --harness cline` runs `cline auth` and warns before confirmation that Cline stores the API key value in `~/.cline/data/settings/providers.json`.
- dsh is experimental developer-preview software; its managed `settings.yaml` schema is pinned by the adapter and doctor warns when the CLI contract drifts.
- Workflow JSON accepts profile names with `route.name`; keep `route.provider` and `route.model` alongside the name as the auditable expected identity.
