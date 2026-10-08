# SDLC §16 — Core Models & Configuration

## 1. Purpose & Scope

The **Core Models & Configuration** subsystem is the foundation layer of the Pitwall GPU broker. It comprises:

- `src/pitwall/config.py` — strict Pydantic settings populated from environment variables and optional TOML config, boot-time domain-config validation, a fail-closed service bootstrapping gate, and a cached global `PitwallSettings` singleton.
- `src/pitwall/core/models.py` — Pydantic v2 domain objects (`Capability`, `Provider`, `Lease`, `Workload`, etc.) that form the persisted registry and runtime record vocabulary.
- `src/pitwall/core/enums.py` — `StrEnum` values for capability classes, lease/workload lifecycle states, provider types, cost modes, and registry prefixes.
- `src/pitwall/core/` leaf modules — `ids.py`, `idempotency.py`, `jobs.py`, `inference.py`, `cost_reporting.py`, `errors.py`, `types.py`, `constants.py`.

All other Pitwall subsystems (`api`, `routing`, `cost`, `runpod_client`, `db`, etc.) import domain models and settings from this subsystem. It has **no runtime dependencies on any other Pitwall package** — only standard-library, Pydantic, and `asyncpg`.

---

## 2. Components

### `src/pitwall/core/__init__.py`

Re-exports the public surface of `pitwall.core`. The module docstring states the design constraint: heavier modules (`inference`, `cost`, routing) are **not** re-exported here to avoid import cycles — they are imported by full path where needed.

**Public exports:**

| Symbol | Origin |
|---|---|
| `Capability`, `CapabilityDefaults`, `Provider`, `Lease`, `LeaseEndpoints`, `LeaseReadiness`, `LeaseTcpEndpoint`, `Workload`, `ConfigAuditEntry`, `WebhookSubscription*` | `core.models` |
| `CapabilityClass`, `CapabilityHint`, `CapabilitySource`, `CostMode`, `LeaseRenewalPolicy`, `LeaseState`, `ProviderType`, `RegistryPrefix`, `ResultDelivery`, `WorkloadState` | `core.enums` |
| `ULID`, `ulid_new()` | `core.ids` |
| `reserve_idempotency_key()`, `IdempotencyReservation`, `IdempotencyMismatch` | `core.idempotency` |

### `src/pitwall/core/enums.py`

`StrEnum` definitions for all domain vocabulary. All enums use `str` base so serialized forms are plain strings.

| Enum | Values |
|---|---|
| `RegistryPrefix` | `GHCR_IO`, `GITLAB_REGISTRY`, `DOCKER_HUB`, `DOCKER_HUB_ALT`; `from_image_ref()` classmethod |
| `CapabilityClass` | `EMBEDDING`, `RERANK`, `LLM`, `VISION`, `TRANSCRIBE`, `GPU_LEASE`, `CUSTOM` |
| `CostMode` | `PER_SECOND`, `PER_REQUEST`, `PER_TOKEN` |
| `CapabilitySource` | `API`, `MCP`, `YAML` |
| `ResultDelivery` | `SYNC`, `ASYNC` |
| `CapabilityHint` | `LATENCY_SENSITIVE`, `COST_SENSITIVE`, `REGION_PREFERENCE` |
| `WorkloadState` | `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, `TIMED_OUT` |
| `ProviderType` | `SERVERLESS_QUEUE`, `SERVERLESS_LB`, `PUBLIC_ENDPOINT`, `POD_LEASE` |
| `ProviderAdapterId` | `RUNPOD`, `VAST`, `TOGETHER`, `LAMBDA_CLOUD` | stable persisted adapter ids |
| `LeaseState` | `CREATING`, `WAITING_RUNTIME`, `WAITING_PROBE`, `ACTIVE`, `STOPPING`, `STOPPED`, `FAILED`, `EXPIRED` |
| `LeaseRenewalPolicy` | `MANUAL` (currently; other values defined in spec) |

### `src/pitwall/core/models.py`

All models inherit from `PitwallModel`, which enforces `extra="forbid"`, `populate_by_name=True`, `str_strip_whitespace=True`, and `use_enum_values=False`. Typed fields use `Strict*` wrappers that validate enum values arrive as strings before coercion, raising `ValueError` otherwise.

**Key types (non-model):**

- `UTCDateTime` — `datetime` with tzinfo required; rejects naive datetimes.
- `UsdAmount` — `Decimal` ge=0, max 12 digits, 6 decimal places.
- `NonEmptyString`, `NonNegativeInt`.
- `JsonObject` — plain `dict[str, Any]`.

**Domain models:**

| Model | Responsibility | Key invariant | Line |
|---|---|---|---|
| `CapabilityDefaults` | Default execution settings attached to a capability | `execution_timeout_ms` default 60 000; `ttl_ms` default 300 000; `result_delivery` default `SYNC` |
| `Capability` | What a consumer asks Pitwall to fulfill | `id`, `name`, `version`, `class_` required; `has_active_signals`-equivalent via `capability_class` property |
| `Workload` | Persisted unit of consumer-requested work | Generic `external_job_id`; legacy RunPod-only input is normalized and dual values must match |
| `LeaseTcpEndpoint` | TCP proxy endpoint exposed by RunPod for a lease | `port` range 1–65535 |
| `LeaseEndpoints` | HTTP and TCP endpoints for a pod lease | Both fields default to empty dict |
| `LeaseReadiness` | Readiness signals before a lease becomes active | `has_active_signals` property: all three timestamps (`runtime_seen_at`, `port_mappings_seen_at`, `probe_passed_at`) must be non-None |
| `Lease` | Stateful provider resource allocation | Requires generic or legacy resource id; dual values must match; existing lifecycle/readiness invariants remain |
| `Provider` | Concrete binding to a static provider adapter | Typed `adapter_id`, environment-name-only `credential_ref`, and redacted config serialization |
| `ConfigAuditEntry` | A single row in the config mutation audit trail | `id` is `int` (not ULID); all value fields nullable |
| `WebhookSubscription` | Consumer-registered webhook URL for async result callbacks | `hmac_secret` has `repr=False` to prevent accidental exposure |
| `WebhookDeliveryFailure` | Failed webhook delivery attempt record | `attempt` range 1–4 (max 4 retries) |

Pod-lease `Provider.config` may retain a validated `warm_cache` object with
non-empty `variant`, timezone-aware ISO 8601 `verified_at`, and `volume_id`.

### `src/pitwall/core/ids.py`

ULID identifier helpers. ULIDs follow the pattern `{prefix}_{ulid}` in the application layer (e.g., `wkl_01ARYZ...`).

- `ULID_PATTERN` — `r"^[0-9A-HJKMNP-TV-Z]{26}$"`, excludes I, L, O, U per Crockford base32.
- `ULID` type alias — pydantic `Annotated[str, Field(min_length=26, max_length=26, pattern=ULID_PATTERN)]`.
- `ulid_new()` — lazily imports `python-ulid` to avoid import-time cost when only types are needed.
- `is_valid_ulid()` — pure Python validation without the library.

### `src/pitwall/core/idempotency.py`

Atomic idempotency-key reservation inside an `asyncpg` transaction. Uses `ON CONFLICT DO NOTHING` for insert, then a separate lookup + body-hash comparison to detect safe replays vs. mutation attempts.

- `reserve_idempotency_key(conn, *, key, body_hash, workload_id)` — inserts or looks up; raises `IdempotencyMismatch` if same key with different body hash.
- `_hash_input()` — SHA-256 of JSON-serialized input (null → `sha256(b"null")`).
- `IdempotencyMismatch` — exception with `original_workload_id` attribute.
- `IdempotencyReservation` — `dataclass` result with `is_new: bool` and `workload_id: str`.

### `src/pitwall/core/cost_reporting.py`

Read-only aggregation queries over `pitwall.workloads` and `pitwall.cost_daily`.

- `cost_summary(pool, *, capability_class, since, until)` — returns `{total_usd: float, entries: list[dict]}`.
- `recent_workloads(pool, *, capability_id, provider_id, provider_type, state, since, until, limit)` — returns `{workloads: list[dict]}` with float conversion on cost fields.

### `src/pitwall/core/errors.py`

Currently empty (`__all__ = []`). Shared error types are defined in-place in each module (`IdempotencyMismatch`, `R2TempCredentialError`, etc.).

### `src/pitwall/core/types.py` / `constants.py`

Both currently empty (`__all__ = []`). Reserved for future cross-package type aliases and shared constants.

---

## 3. Domain Model

### Enums (`core.enums`)

All enums inherit `StrEnum`. Serialization always produces the string value. `CapabilityHint` and `CapabilitySource` drive provider ranking and capability registration respectively. `RegistryPrefix.from_image_ref()` is the single dispatch point for selecting registry auth credentials based on Docker image ref prefix.

### PitwallModel Base (`pitwall.core.models`)

`PitwallModel` enforces **strictness** across all child models:

```python
model_config = ConfigDict(
    extra="forbid",  # reject unknown fields
    populate_by_name=True,  # allow alias and field name
    str_strip_whitespace=True,
    use_enum_values=False,  # serialize enum members as objects by default
)
```

### Capability / Provider

`Capability` describes what a consumer requests. `Provider` binds it to one of the four static adapters through `adapter_id`; `credential_ref` stores only the environment-variable name resolved at an adapter call boundary. Both support `source` (`API`, `MCP`, `YAML`) and a `last_applied_yaml_hash` for YAML-registered capabilities. `Provider.config` remains adapter configuration, but repository/API/GitOps writes reject raw credential-shaped values recursively across snake, camel, and separator variants; model and audit reads redact legacy values, and repr omits the field. Migration 0028 removes legacy values from provider config after deriving safe credential references. For generated pod templates, the complete `image_ref` (repository plus tag or digest), `docker_start_cmd`, `ports`, the container disk size, the registry auth id, and the sorted non-secret key names from `env_vars` contribute to `config_sha`; environment values never do. The RunPod account enters only as a keyed digest (HMAC-SHA256 keyed by SHA-256 of a fixed context string plus the API key), so two accounts never share a cached template and the key is never stored or recoverable. Cache rows written before these inputs existed simply miss once and are recreated.

Generated-template image references must be credential-free Docker references, and because `docker_start_cmd` is template identity, start arguments must never contain secrets.

### Lease / Workload

`Lease` models the stateful resource lifecycle from `CREATING` through `ACTIVE` to `STOPPED`/`EXPIRED`. `external_resource_id` is authoritative; `runpod_pod_id` remains a nullable compatibility field. Its model validator also enforces `expires_at > created_at` and that `ACTIVE` leases have observed endpoints and three readiness signals.

`Workload` models individual inference jobs with generic `external_job_id`, retained `runpod_job_id` compatibility, full timing, and cost tracking. `WorkloadState` transitions are driven atomically by `pitwall.workload_lifecycle:transition_to_running`, `transition_to_completed`, and `transition_to_failed`.

### Settings Loading (`pitwall.config`)

`load_settings_from_env()` returns `PitwallSettings()`, converting a pydantic `ValidationError` into `ConfigFileError` whose text lists each rejected setting as `loc (ENV_NAME): msg` without the configured value; the model customizes settings sources as init values, explicit environment variables, optional TOML config, then model defaults (`pitwall.config:PitwallSettings`, `pitwall.config`). Environment values win over TOML file values because `_PitwallEnvSettingsSource` precedes `_PitwallTomlSettingsSource` in that tuple. The env source returns only explicitly configured env values, so field defaults do not mask file values (`pitwall.config`).

The env source still uses private helpers:

- `_get_env(key, default)` — raw `os.environ.get`.
- `_get_bool_env(key, default)` — parses `1/true/yes/on` and `0/false/no/off`; raises `ValueError` on invalid string.
- `_get_first_env(*keys, default)` — returns first non-empty value across aliases.

`PITWALL_MODELS_DIR` and the TOML `pitwall_models_dir` key are converted through `Path`. An
explicit `load_catalogue(root)` argument wins over this setting, and `reload()` clears the
per-process directory cache. `models evidence` writes its schema-validated measured observation
to this resolved directory and then clears that cache; operators should set this directory to a
writable dossier checkout when the packaged catalogue is installed read-only.

### TOML Config File (`pitwall.config`)

`PITWALL_CONFIG_FILE` points at an explicit config path; if it is unset, `resolve_config_file()` uses `./pitwall.toml` only when that file exists (`pitwall.config`). An explicit missing file or non-`.toml` suffix raises `ConfigFileError`; an absent default `pitwall.toml` contributes no file settings (`pitwall.config`). The file is loaded through `pydantic-settings` `TomlConfigSettingsSource(..., toml_file=path)` and normalized before merge (`pitwall.config`); Pitwall declares `pydantic-settings` as a runtime dependency and requires Python >=3.14 (`pyproject.toml`).

Accepted TOML keys mirror `PitwallSettings` field names plus env-style aliases: `_config_file_key_to_field()` accepts each field name, its uppercase form, and every alias from `_ENV_FIELD_ALIASES` (`pitwall.config:PitwallSettings`, `pitwall.config`). Entrypoint-only bind host env vars are not `PitwallSettings` fields; see Configuration.

Two top-level tables are not settings. `[personal]` is read by `pitwall.personal.backend`, whose only key is `backend` (`personal`, the default, or `registry`; see [CLI](18-cli.md)). `[agents]` holds the Agent Routing profile tables (`[agents.profiles]` with `defaults`, `endpoints`, `harnesses`, `models`, and `pitwall`) and is validated here by `agents_settings_from_toml()`; a malformed `[agents]` table raises `ConfigFileError` as `invalid [agents] table: agents.<key path>: <message>` (`pitwall.config`). Any other key that is not a `PitwallSettings` field or alias fails settings validation (`extra="forbid"`).

An error in the config file or in a setting never echoes the value or the file content, because either can be a secret. A TOML syntax error reports `could not read Pitwall config file <path>: invalid TOML at line <n>, column <m>`; any other read failure reports the OS reason, or the exception class when there is none. A setting that fails validation names the setting and its environment variable (`pitwall_pre_spend_mode (PITWALL_PRE_SPEND_MODE): ...`); an environment value that cannot be converted reports only `<ENV_NAME> could not be parsed`.

### Fail-Closed Boot (`pitwall.config`)

`require_runtime_env(service)` is called by every Pitwall service entrypoint before accepting traffic. It:

1. Calls `check_domain_config(service)`, which normalizes the service name, loads settings if needed, and runs the boot-time domain-config checks (`pitwall.config`).
2. Converts `ConfigFileError` (including settings that fail validation) and parsing `ValueError` into sanitized stderr text and raises `SystemExit(os.EX_CONFIG)`. A value that fails its environment conversion reports `<ENV_NAME> could not be parsed`, and a rejected setting lists the field with its environment name (`pitwall_pre_spend_mode (PITWALL_PRE_SPEND_MODE): ...`); neither ever echoes the configured value (`pitwall.config`).
3. Prints warnings and continues; prints errors and exits `os.EX_CONFIG` (`pitwall.config`).

Some keys are read by a service straight from the environment instead of through
`PitwallSettings`. `check_domain_config` validates them by name too, so `pitwall config check`
and every service's startup check reject a malformed value with `invalid-env` (the key and its
rule, never the value): `PITWALL_API_PORT` (1–65535); `PITWALL_API_MAX_CONCURRENCY`,
`PITWALL_API_MAX_BODY_BYTES`, `PITWALL_COST_EXPORTER_MAX_CONCURRENCY`,
`PITWALL_WEBHOOK_MAX_BODY_BYTES`, `PITWALL_WEBHOOK_MAX_CONCURRENCY`, and
`PITWALL_RETENTION_DAYS` (integers of at least 1); `PITWALL_RETENTION_BATCH_SIZE` (1–10,000);
`PITWALL_WEBHOOK_RATE_LIMIT` (`<requests>/<window>`); `PITWALL_WEBHOOK_PREVIOUS_SECRETS` (a JSON
list of strings); `PITWALL_RETENTION_MODE` (`off`, `archive`, `archive-purge`);
`PITWALL_RUN_LIVE` and `RUNPOD_LIVE` (1/0, true/false, yes/no, on/off); and
`PITWALL_UNSAFE_ALLOW_INSECURE_BIND` (1 or 0); `PITWALL_ARCHIVE_ENCRYPTION_KEY` (URL-safe base64 of exactly 32 bytes); `PITWALL_API_SCOPED_TOKENS` (a non-empty JSON object of token to known scopes, parsed by `pitwall.api.scopes`); and `PITWALL_WEBHOOK_ENCRYPTION_KEYS` with `PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY` (a JSON object of 32-byte keys that includes the current one). For the reconciler, an enabled
`PITWALL_RETENTION_MODE` also requires `PITWALL_ARCHIVE_DIR`, `PITWALL_ARCHIVE_ENCRYPTION_KEY`,
and `PITWALL_ARCHIVE_ENCRYPTION_KEY_VERSION` (`incomplete-retention`). The API, webhook, and
cost-exporter entry points run `require_valid_service_env()` before reading their own keys.

Default required env for unknown services: `RUNPOD_API_KEY`, `DATABASE_URL`, `REDIS_URL` (`pitwall.config`).

### Boot-Time Domain-Config Checks (`pitwall.config`)

| Check | Inputs | Failure mode |
|---|---|---|
| Required runtime settings | Per-service set from `_REQUIRED_ENV_BY_SERVICE`; missing and whitespace-only values fail (`pitwall.config`) | error -> `SystemExit(os.EX_CONFIG)` |
| Budget settings | `PITWALL_MONTHLY_BUDGET_USD`, `PITWALL_PER_REQUEST_MAX_USD` (`pitwall.config`) | negative values are errors; per-request cap above monthly cap is a warning |
| Timeout and port settings | Lease/audit timeout settings plus `PITWALL_WEBHOOK_RECEIVER_PORT` and `PITWALL_COST_EXPORTER_PORT` (`pitwall.config`) | non-positive timeout, invalid port, or audit max below exec timeout is an error; image-pull timeout below startup timeout is a warning |
| Embedding URL | `PITWALL_BASE_URL` when `PITWALL_EMBEDDING_VIA_PITWALL=true` (`pitwall.config`) | missing base URL is an error |
| R2 temp credentials | `R2_TEMP_CREDENTIALS_ENABLED`, temp credential TTL, and required R2/Cloudflare fields (`pitwall.config`) | invalid mode/TTL or required missing fields are errors; partial `auto` config is a warning |
| Optional integration groups | Langfuse, Resend alerts, and Tailscale/webhook pairing (`pitwall.config`) | partial config is a warning |

### Cached Settings Singleton (`pitwall.config`)

`@lru_cache(maxsize=1)` on `get_settings()` ensures the same `PitwallSettings` object is returned on every call within a process (`pitwall.config`).

---

## 4. Public Interfaces

| Function / Class | Module | Signature |
|---|---|---|
| `PitwallSettings` | `config` | `BaseModel` subclass; `extra="forbid"` |
| `load_settings_from_env()` | `config` | `() -> PitwallSettings` |
| `get_settings()` | `config` | `() -> PitwallSettings` (cached) |
| `resolve_config_file()` | `config` | `(environ: dict[str, str] \| None = None, cwd: Path \| None = None) -> Path \| None` |
| `check_domain_config()` | `config` | `(service: str = "api", *, settings: PitwallSettings \| None = None) -> ConfigCheckResult` |
| `format_config_check_result()` | `config` | `(result: ConfigCheckResult) -> str` |
| `format_settings_load_error()` | `config` | `(exc: ValueError) -> str` |
| `require_runtime_env(service)` | `config` | `(service: str) -> None`; raises `SystemExit` |
| `required_runtime_env_vars(service)` | `config` | `(service: str) -> tuple[str, ...]` |
| `Capability`, `Provider`, `Lease`, `Workload`, etc. | `core/models` | `PitwallModel` subclasses |
| `ULID` type alias | `core/ids` | `Annotated[str, Field(...)]` |
| `ulid_new()` | `core/ids` | `() -> str` |
| `is_valid_ulid(value)` | `core/ids` | `(value: str) -> bool` |
| `reserve_idempotency_key()` | `core/idempotency` | `(conn, *, key, body_hash, workload_id) -> IdempotencyReservation` |
| `RegistryPrefix.from_image_ref()` | `core/enums` | `(image_ref: str) -> RegistryPrefix \| None` |
| `cost_summary()` | `core/cost_reporting` | async; full sig in  |
| `recent_workloads()` | `core/cost_reporting` | async; full sig in  |

---

## 5. Configuration

All environment variables consumed by this subsystem. `PitwallSettings` fields live in `pitwall.config:PitwallSettings` through `pitwall.config:PitwallSettings`; env aliases live in `_ENV_FIELD_ALIASES` (`pitwall.config:PitwallSettings`, `pitwall.config`). Entrypoint-only bind hosts are listed separately below.

### Loader-only

| Env var | Pydantic field | Default |
|---|---|---|
| `PITWALL_CONFIG_FILE` | *(loader-only)* | unset; falls back to `./pitwall.toml` only if it exists (`pitwall.config`) |

### Required at boot (fail-closed if missing)

| Env var | Pydantic field | Default |
|---|---|---|
| `RUNPOD_API_KEY` | `runpod_api_key` | *(none)* |
| `DATABASE_URL` | `database_url` | *(none)* |
| `REDIS_URL` | `redis_url` | *(none)* |

### RunPod

| Env var | Pydantic field | Default |
|---|---|---|
| `RUNPOD_REST_API_URL` | `runpod_rest_api_url` | `https://api.runpod.io/v2` |
| `RUNPOD_REST_V1_API_URL` | `runpod_rest_v1_api_url` | `https://rest.runpod.io/v1` |
| `PITWALL_RUNPOD_MARKET_CACHE_TTL_S` | `runpod_market_cache_ttl_s` | `300.0`; non-negative seconds for the in-process RunPod market catalogue and balance cache (`0` refreshes on every read) |
| `RUNPOD_NETWORK_VOLUME_ID` | `runpod_network_volume_id` | `""` |
| `RUNPOD_DATA_CENTER_ID` | `runpod_data_center_id` | `""` |
| `RUNPOD_REGISTRY_AUTH_ID` | `runpod_registry_auth_id` | `""` |
| `RUNPOD_REGISTRY_AUTH_ID_GHCR` | `runpod_registry_auth_id_ghcr` | `""` |
| `RUNPOD_REGISTRY_AUTH_ID_GITLAB` | `runpod_registry_auth_id_gitlab` | `""` |
| `RUNPOD_REGISTRY_AUTH_ID_DOCKER_HUB` | `runpod_registry_auth_id_docker_hub` | `None` |

### Model catalogue

The CLI catalogue planner (`serve --plan-only`) may run with an empty `DATABASE_URL`; it
loads the configured or packaged catalogue and may read the existing GPU price snapshot.
`PITWALL_MODELS_DIR`, `RUNPOD_API_KEY` (optional read-only price enrichment), and
`RUNPOD_NETWORK_VOLUME_ID` can affect the rendered plan. If pricing is unconfigured or unavailable,
the planner uses the canonical VRAM table, leaves the estimate unpriced, and labels the source
`fallback`. Live launches and registry-backed `--dry-run` still require `DATABASE_URL`.
For the complete operator sequence and the required live endpoint checks, see
[`docs/operator/serve-quickstart.md`](../operator/serve-quickstart.md).

| Env var | Pydantic field | Default |
|---|---|---|
| `PITWALL_MODELS_DIR` | `pitwall_models_dir` | packaged `pitwall/models/data` |

### R2 / Cloudflare

| Env var(s) | Pydantic field | Default |
|---|---|---|
| `R2_ENDPOINT` | `r2_endpoint` | `""` |
| `R2_ACCESS_KEY` | `r2_access_key` | `""` |
| `R2_SECRET_KEY` | `r2_secret_key` | `""` |
| `R2_PARENT_ACCESS_KEY_ID` / `CLOUDFLARE_R2_PARENT_ACCESS_KEY_ID` / `R2_ACCESS_KEY_ID` / `R2_ACCESS_KEY` | `r2_parent_access_key_id` | `""` |
| `R2_BUCKET_STAGING` | `r2_bucket_staging` | `pitwall-staging` |
| `R2_TEMP_CREDENTIALS_ENABLED` / `PITWALL_R2_TEMP_CREDENTIALS_ENABLED` | `r2_temp_credentials_enabled` | `"auto"` |
| `R2_TEMP_CREDENTIALS_REQUIRED` / `PITWALL_R2_TEMP_CREDENTIALS_REQUIRED` | `r2_temp_credentials_required` | `False` |
| `R2_TEMP_CREDENTIAL_TTL_S` + 7 aliases | `r2_temp_credential_ttl_s` | `21_600` |
| `R2_TEMP_CREDENTIAL_PERMISSION` + 3 aliases | `r2_temp_credential_permission` | `"object-read-write"` |
| `R2_TEMP_CREDENTIAL_PREFIXES` / `PITWALL_R2_TEMP_CREDENTIAL_PREFIXES` | `r2_temp_credential_prefixes` | `""` |
| `PITWALL_DEBUG_LOG_PREFIX` | *(direct read in `pitwall.r2_temp_credentials`)* | `debug-logs/`; the one prefix a temporary credential is scoped to when no prefixes are configured |
| `R2_TEMP_CREDENTIAL_OBJECTS` / `PITWALL_R2_TEMP_CREDENTIAL_OBJECTS` | `r2_temp_credential_objects` | `""` |
| `CLOUDFLARE_ACCOUNT_ID` / `CF_ACCOUNT_ID` / `R2_ACCOUNT_ID` | `cloudflare_account_id` | `""` |
| `CLOUDFLARE_API_TOKEN` / `CF_API_TOKEN` / `R2_TEMP_CREDENTIAL_API_TOKEN` | `cloudflare_api_token` | `""` |

### Budget

| Env var | Pydantic field | Default |
|---|---|---|
| `PITWALL_MONTHLY_BUDGET_USD` | `pitwall_monthly_budget_usd` | `50.0` |
| `PITWALL_PER_REQUEST_MAX_USD` | `pitwall_per_request_max_usd` | `10.0` |
| `PITWALL_BUDGET_LOCK_KEY` | `pitwall_budget_lock_key` | `5494545452575544` |
| `PITWALL_BUDGET_BREACH_KILL_MODE` | `pitwall_budget_breach_kill_mode` | `"disabled"`; `disabled` never escalates, `shadow` logs what it would do, `armed` fires the kill switch when a circuit-breaker budget block leaves headroom at or below the floor |
| `PITWALL_BUDGET_BREACH_KILL_HEADROOM_FLOOR_USD` | `pitwall_budget_breach_kill_headroom_floor_usd` | `0.0`; headroom in USD at or below which an armed breach fires (`0` means only when the budget is fully exhausted) |

### Lease / timeouts

| Env var | Pydantic field | Default |
|---|---|---|
| `PITWALL_DEFAULT_LEASE_TTL_S` | `pitwall_default_lease_ttl_s` | `7200` |
| `PITWALL_LEASE_MAX_LIFETIME_MIN` | `pitwall_lease_max_lifetime_min` | `1440` |
| `PITWALL_LEASE_ADVANCE_WARNING_MIN` | `pitwall_lease_advance_warning_min` | `"15,5"` |
| `PITWALL_VOLUME_ATTACH_TIMEOUT_S` | `pitwall_volume_attach_timeout_s` | `300` |
| `PITWALL_IMAGE_PULL_TIMEOUT_S` | `pitwall_image_pull_timeout_s` | `600` |

### Audit

| Env var | Pydantic field | Default |
|---|---|---|
| `PITWALL_AUDIT_GPU_IDS` | `pitwall_audit_gpu_ids` | `"NVIDIA H100 80GB HBM3,NVIDIA L4,NVIDIA A100-SXM4-80GB"` |
| `PITWALL_AUDIT_CLOUD_TYPE` | `pitwall_audit_cloud_type` | `"SECURE"` |
| `PITWALL_AUDIT_EXEC_TIMEOUT_S` | `pitwall_audit_exec_timeout_s` | `3600` |
| `PITWALL_AUDIT_EXEC_TIMEOUT_MAX_S` | `pitwall_audit_exec_timeout_max_s` | `7200` |
| `PITWALL_AUDIT_QUEUE_TIME_S` | `pitwall_audit_queue_time_s` | `300` |
| `PITWALL_AUDIT_STARTUP_TIMEOUT_S` | `pitwall_audit_startup_timeout_s` | `600` |

### Worker / operator

| Env var | Pydantic field | Default |
|---|---|---|
| `PITWALL_CLOUD_WORKER_IMAGE` | `pitwall_cloud_worker_image` | `""` |
| `PITWALL_RUNPOD_CAPACITY_ERROR_SUBSTRINGS` | `pitwall_gpu_broker_capacity_error_substrings` | `""` |
| `PITWALL_EMBEDDING_VIA_PITWALL` | `pitwall_embedding_via_pitwall` | `False` |
| `PITWALL_BASE_URL` | `pitwall_base_url` | `""` |
| `PITWALL_ROUTING_CLI` | `pitwall_routing_cli` | `pitwall agents` |
| `PITWALL_PRE_SPEND_MODE` | `pitwall_pre_spend_mode` | `"balanced"`; `balanced` blocks secrets and redacts supported PII, `block` denies every finding, `redact` rewrites every safely supported finding |
| `PITWALL_ENDPOINT_PROBE_TIMEOUT_S` | `pitwall_endpoint_probe_timeout_s` | `10`; integer seconds, at least 1, bounding one public-endpoint readiness probe |
| `PITWALL_SATURATION_WINDOW_S` | `pitwall_saturation_window_s` | `60`; integer seconds, at least 1; the window over which consumer 4xx replies are counted per provider, token fingerprint, and capability (telemetry only; it never changes provider health) |
| `PITWALL_SATURATION_4XX_THRESHOLD` | `pitwall_saturation_4xx_threshold` | `30`; integer, at least 1; 4xx replies per window at which a `consumer saturation detected` warning is logged |
| `PITWALL_GATEWAY_URL` | `pitwall_gateway_url` | `http://127.0.0.1:20130/v1`; OpenAI-compatible base URL of the loopback free-tier gateway |
| `PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST` | `pitwall_webhook_loopback_allowlist` | `()`; comma-separated exact `127.0.0.1:<port>` or `[::1]:<port>` targets that may receive plain-HTTP webhooks ([Webhooks](../webhooks.md)); any other host, a missing port, or user information is a validation error |

### Bind hosts (entrypoint-only)

These host settings are direct `os.environ` reads in the bare `python -m ...` entrypoints, not `PitwallSettings` fields. Their loopback defaults support the inbound trust-boundary posture described in `14-security.md` (`docs/sdlc/14-security.md`).

| Env var | Pydantic field | Default |
|---|---|---|
| `PITWALL_API_HOST` | *(entrypoint-only)* | `127.0.0.1` (`pitwall.api.__main__`) |
| `PITWALL_WEBHOOK_HOST` | *(entrypoint-only)* | `127.0.0.1` (`pitwall.webhook_receiver.__main__`) |
| `PITWALL_COST_EXPORTER_HOST` | *(entrypoint-only)* | `127.0.0.1` (`pitwall.cost.__main__`; console entrypoint `pitwall-cost-exporter` in `pyproject.toml`) |

### Observability / webhooks

| Env var | Pydantic field | Default |
|---|---|---|
| `LANGFUSE_HOST` | `langfuse_host` | `""` |
| `LANGFUSE_PUBLIC_KEY` | `langfuse_public_key` | `""` |
| `LANGFUSE_SECRET_KEY` | `langfuse_secret_key` | `""` |
| `PITWALL_WEBHOOK_RECEIVER_PORT` | `pitwall_webhook_receiver_port` | `8082` |
| `PITWALL_MCP_TRANSPORT` | `pitwall_mcp_transport` | `"stdio"` |
| `PITWALL_COST_EXPORTER_PORT` | `pitwall_cost_exporter_port` | `9109` |
| `PITWALL_ALERT_FROM` | `pitwall_alert_from` | `""` |
| `PITWALL_ALERT_TO` | `pitwall_alert_to` | `""` |
| `RESEND_API_KEY` | `resend_api_key` | `""` |
| `RESEND_SENDER_EMAIL` | `resend_sender_email` | `""` |
| `RESEND_BUDGET_ALERT_EMAIL` | `resend_budget_alert_email` | `""` |
| `PITWALL_ADMIN_SECRET` | `pitwall_admin_secret` | `""` |
| `PITWALL_API_TOKEN` | `pitwall_api_token` | `""` |
| `PITWALL_HF_TOKEN` | `pitwall_hf_token` | `""` | Control-plane secret; injected as launch-only `HF_TOKEN` only for gated serve-model pods; never stored in provider config or emitted by dry-run/results/logs |
| `PITWALL_ENDPOINT_KEY` | `pitwall_endpoint_key` | `""` | Registry pod endpoint credential; injected launch-only as `PITWALL_ENDPOINT_KEY` and referenced by provider `config.api_key_env`; required for authenticated pod serving, optional for non-pod deployments |
| `PITWALL_INBOUND_RATE_LIMIT` | `pitwall_inbound_rate_limit` | `""` |
| `PITWALL_PRICE_MAX_AGE_S` | `pitwall_price_max_age_s` | `None` | Optional positive GPU-price freshness limit in seconds; unset preserves existing admission behavior, while a stale non-dry serve launch is refused before pod creation after active-lease replay resolution |

`config.self_hosted` is extra-forbid validated; saturation window/threshold and endpoint probe
timeout follow the normal `PITWALL_*` settings mapping, while live-test gate variables are
test-only.

The focused J23 release harness is an exception to the normal database-backed journey bootstrap:
`DATABASE_URL='' uv run --frozen bash scripts/release/run-user-journeys.sh J23` runs only mocked
in-process coverage and therefore neither reads a database URL nor requires Redis configuration.

`PITWALL_HF_TOKEN` joins the centrally redacted secret environment values collected by
`configure_logging_redaction()`; its setting and explicit environment alias are defined in
`pitwall.config` and `pitwall.config`, and its redaction entry is in
`pitwall.security.redaction`.

Template diagnostics redact token-shaped values, bearer credentials, and `KEY=value` assignments before logging.

---

## 6. Failure Modes & Error Types

### `SystemExit(os.EX_CONFIG)` — invalid boot config

`require_runtime_env()` catches settings load errors, prints sanitized text, and raises `SystemExit(os.EX_CONFIG)`; after a successful load, it prints warnings but exits only when `check_domain_config()` returns errors (`pitwall.config`).

Required-runtime-env sets (`_REQUIRED_ENV_BY_SERVICE`): `api` requires `DATABASE_URL + REDIS_URL` (the RunPod credential is not needed to boot; it is resolved when the first RunPod operation runs); `cost-exporter` requires only `DATABASE_URL`; `webhook` has no required env; `reconciler` and `mcp` require `DATABASE_URL` and `REDIS_URL` as well as the RunPod credential named RUNPOD_API_KEY. There is no `worker` service, and an unrecognised service name falls back to the core three (`pitwall.config`). The broader domain-config errors above apply to every service.

### `ConfigFileError` — invalid TOML config file

Raised when an explicit `PITWALL_CONFIG_FILE` is missing, a discovered config path is not `.toml`, `TomlConfigSettingsSource` cannot read/parse the file, or the `[agents]` table is malformed; `require_runtime_env()` maps it to `SystemExit(os.EX_CONFIG)` (`pitwall.config`). The message names the path and, for a syntax error, the line and column (`invalid TOML at line 3, column 9`); it never contains the file's content, because tomllib's own text can quote keys.

### `ValueError` — invalid boolean or enum env parsing

`_get_bool_env()` raises `ValueError` if a non-boolean string is assigned to a boolean-typed env var (`pitwall.config`).

`Strict*` validators in `core/models.py` raise `ValueError` if enum values are not strings.

### Invalid setting value (`ConfigFileError`)

When an env var or `pitwall.toml` value fails `PitwallSettings` validation (e.g., a non-numeric string for an `int` field, or an unknown key), `load_settings_from_env()` raises `ConfigFileError` listing `loc (ENV_NAME): msg` lines rendered from `errors(include_input=False, include_url=False)`, with no exception chained, so neither the message nor a traceback echoes the value (`pitwall.config`).

### `IdempotencyMismatch` (`pitwall.core.idempotency:IdempotencyMismatch`)

Raised when an idempotency key is replayed with a different request body hash. Carries `original_workload_id`. API layer maps this to HTTP 422.

### Lease lifecycle validator (`pitwall.core.models`)

`ValueError` raised during `.model_validate()` if:
- `expires_at <= created_at`
- State is `ACTIVE` but `endpoints` is `None`
- State is `ACTIVE` but readiness signals are incomplete

### R2 temp credential errors (`r2_temp_credentials.py`)

`R2TempCredentialConfigError` — TTL out of range, missing required fields in `required` mode, invalid permission literal.

`R2TempCredentialError` — HTTP errors, non-success Cloudflare responses, missing required result fields, malformed JSON.

---

## 7. GitOps Desired-State Planning

`src/pitwall/gitops/` plans the difference between versioned YAML and the live capability and provider registry. It is plan-only: nothing in this package applies a plan. The required root version is `apiVersion: pitwall.dev/v1`; each document can declare `capabilities` and `providers`. The loader reuses the seed-file YAML/JSON parser for hermetic operation, then validates a stricter GitOps schema (`pitwall.gitops.schema`). Unknown fields fail closed through the shared `PitwallModel` policy.

### Plan model

`pitwall.gitops.differ:build_reconcile_plan` compares desired YAML against live `Capability` and `Provider` models and returns a deterministic `ReconcilePlan`. Operations are structured as `create`, `update`, or `delete`, keyed by `capability` or `provider`, with field-level `changes`, old/current snapshots, desired snapshots, and a `destructive` flag.

GitOps only plans delete operations for YAML-owned live rows: rows with `source == YAML` or a non-null `last_applied_yaml_hash`. API/MCP-owned rows outside the desired files are left alone. Desired rows with the same name as an existing live row adopt the existing ID unless the YAML explicitly declares a conflicting ID, which fails at planning time. The MCP `pitwall_copilot_propose` tool reads the same plan model to propose changes; it never applies them.

### Example

```yaml
apiVersion: pitwall.dev/v1
capabilities:
  - name: embedding.demo
    class: embedding
    cost_mode: per_second
    input_schema: {"type": "object"}
    output_schema: {"type": "object"}
providers:
  - name: embedding-demo-lb
    capability: embedding.demo
    provider_type: serverless_lb
    runpod_endpoint_id: eptest00000000
    region: US-KS-2
    priority: 10
    config:
      lb_base_url: https://eptest00000000.api.runpod.ai
```

---

## 8. Testing

Tests directly targeting this subsystem:

| File | What it covers |
|---|---|
| `tests/test_config_runtime_env.py` | `require_runtime_env()` / `required_runtime_env_vars()`; TOML default and `PITWALL_CONFIG_FILE`; env-over-file precedence; `check_domain_config()` errors; missing env -> `EX_CONFIG`; whitespace -> missing; service-name normalization; `cost-exporter` reduced env set; `mcp` full env set |
| `tests/gitops/test_reconcile_plan.py` | Versioned desired-state loading; deterministic create/update/delete plans; YAML-owned delete filtering |
| `tests/property/test_gitops_properties.py` | Property coverage that create-plan ordering remains deterministic for any generated YAML order |
| `tests/test_r2_temp_credentials.py` | `R2TempCredentialEnvConfig.from_env()`; TTL validation (zero, negative, excessive, max-ok); prefix scoping; HTTP errors; non-success responses; `mint_r2_temp_credentials()` |
| `tests/test_smoke_packages.py::test_config_exports_require_runtime_env` | `require_runtime_env` and `required_runtime_env_vars` exports present |
| `tests/cost/test_budget_gate.py::test_budget_rejected_response_body_is_http_402_contract` | Budget config rejects non-positive values |
| `tests/reconciler/test_init_coverage.py::test_validate_redis_dsn` | Redis config missing/invalid/valid paths |
| `tests/runpod_client/test_pods.py` | `PITWALL_RUNPOD_CAPACITY_ERROR_SUBSTRINGS` env var override |
| `tests/leases/test_lease_landmines.py::test_mcp_lease_pod_attach_hang_cools_down_provider` | Provider config used in MCP lease pod mount path |
| `tests/db/test_config_audit_migration.py` | `config_audit` schema migration correctness |
| `tests/db/test_repository.py::test_patch_name` | JSONB `config` round-trip patching on capabilities/providers |
| `tests/audit/test_runtime_config.py` | `RuntimeAuditConfig` (audit CLI wrapper over `AuditConfig` + env); all defaults, env overrides, protocol method presence |

Tests that **exercise but do not exclusively target** the subsystem (consumers of `PitwallSettings` or domain models):

The session migration-ledger finalizer is a backstop only; inline raw-schema
fixtures remain responsible for restoring their own module's database state.

- `tests/api/test_openai_proxy.py` / `test_openai_proxy_routes.py` — model serialization in API responses
- `tests/routing/test_*.py` — `Provider`/`Capability` routing logic
- `tests/leases/test_launch.py` — provider config in launch template
- `tests/cost/test_estimator.py` — cost config from provider `config` field

---

## 9. Dependencies

### Internal Pitwall packages

| Importing module | Imports from |
|---|---|
| `core/idempotency.py` | `asyncpg` only |
| `config.py` | `pitwall.r2_temp_credentials` (`R2TempCredentialPermission`, `DEFAULT_R2_TEMP_CREDENTIAL_TTL_S`, `_validate_permission`) |
| `core/models.py` | `pitwall.core.enums` |
| `core/__init__.py` | `core.enums`, `core.idempotency`, `core.ids`, `core.models` |
| `models/` (public package) | `pitwall.config`, `pitwall.core.enums`, `pitwall.core.models`, `pitwall.runpod_client.gpu` |

### External dependencies

| Library | Where used |
|---|---|
| `pydantic` | All models (`BaseModel`, `Field`, `ConfigDict`, `AfterValidator`, `BeforeValidator`, `model_validator`, `AliasChoices`) |
| `pydantic-settings` | `config.py` (`BaseSettings`, custom settings sources, `TomlConfigSettingsSource`); direct runtime dependency in `pyproject.toml` |
| `PyYAML` | `models/catalogue.py` (strict YAML front-matter parsing) |
| `asyncpg` | `core/idempotency.py`, `core/cost_reporting.py` |
| `python-ulid` | `core/ids.py:ulid_new()` — lazy import |
| `httpx` | `r2_temp_credentials.py` |
| `pydantic` (second-party re-export) | `pitwall.r2_temp_credentials` re-exports from `config.py` |

## Production routing configuration

`PitwallSettings` defines the production planner controls:

| Setting / environment variable | Type and bound | Default |
| --- | --- | --- |
| `pitwall_routing_mode` / `PITWALL_ROUTING_MODE` | `priority \| weighted` | `priority` |
| `pitwall_routing_weights` / `PITWALL_ROUTING_WEIGHTS` | JSON mapping to strict Decimal cost/latency/quota/reset weights, each 0–1000 | `{}` |
| `pitwall_routing_max_attempts` / `PITWALL_ROUTING_MAX_ATTEMPTS` | integer 1–10 | `3` |
| `pitwall_job_event_limit` / `PITWALL_JOB_EVENT_LIMIT` | integer 1–100 | `25` |

Each mapping value uses `RoutingWeights` defaults (`pitwall.config`):
`cost=1`, `latency=0.001`, `w_quota=10`, and `w_reset=2.5`. The same defaults
apply when no mapping matches. TOML accepts the field name or its environment-style
alias; use one spelling per field. Environment values override matching nested TOML
values while TOML-only keys remain available.

Weight lookup precedence is capability ID, capability name, capability class, then `*`. Unknown
fields, non-finite numbers, invalid JSON, out-of-range weights, and invalid limits fail settings
validation. `.env.example` documents priority-compatible rollout and an opt-in weighted example
(`pitwall.config`).

### `pitwall.r2_temp_credentials` (separate package)

Imported by `config.py` to pull in `R2TempCredentialPermission` type, defaults, and the `_validate_permission` helper. This is the only external package in `src/pitwall/` that is not stdlib, pydantic, or asyncpg.
