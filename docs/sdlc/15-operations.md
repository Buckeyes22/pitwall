# Operations, Recovery, and Data Lifecycle

## 1. Scope

Pitwall's operational safety layer covers emergency compute teardown, R2 staging cleanup,
encrypted retention and purge, PostgreSQL restore drills, and hermetic chaos drills. Durable
evidence is written to `pitwall.kill_log`, `pitwall.retention_runs`, and/or
`pitwall.config_audit` as appropriate.

These controls are operator tools, not a hosted service. The public alpha has best-effort support
and no uptime or recovery-time SLA.

## 2. Emergency kill switch

`CloudKillSwitch` in `src/pitwall/api/admin/kill_switch.py` executes three ordered stages:

1. deny the configured Tailscale tag in the network policy;
2. revoke matching Tailscale devices;
3. terminate matching RunPod compute.

Each stage records errors and the later stages still run after a partial failure. The total target
is under 30 seconds. The authenticated `POST /v1/admin/kill-switch` route composes this control with
`persist_kill_report`, recording the actor, reason, affected resources, duration, and errors.

When complete Tailscale configuration is absent, `NoOpNetworkSever` is used. That is a deliberate
degraded boundary, not evidence that network isolation occurred. Operators relying on Tailscale
isolation must configure and drill the real adapter.

If RunPod pods were found, the kill path best-effort deletes their R2 debug staging keys. R2 cleanup
failures are attached to the report and never prevent compute termination. Short-lived R2
credentials are preferred for workloads; parent credential rotation is a separate incident action.

### RunPod control-plane rollback

The RunPod control plane defaults to REST v2. There is no dual-write, state conversion, migration,
or version switch: rollback is an ordinary Git revert of the v2 change. Existing provider
resources, leases, and database rows require no conversion. `RUNPOD_REST_V1_API_URL` is a narrowly
bounded compatibility endpoint for pod creates that require unsupported placement/resource-floor
options and for pod reset; it is not a rollback flag. Operators can identify this path through the
`using bounded RunPod REST v1 pod-create path for:` log message and its listed reason.

Before changing either RunPod base URL, validate a dry-run configuration and run the hermetic
RunPod client tests. Do not use a paid production resource as a migration probe. See the
[RunPod REST v2 operator note](../operator/runpod-rest-v2.md) and
[ADR 0006](../decisions/0006-runpod-rest-v2-control-plane.md).

## 3. Encrypted retention and purge

`archive_workloads_to_jsonl` in `src/pitwall/retention/archive.py` processes one bounded batch of
terminal workloads submitted before the cutoff. The cutoff is the UTC midnight at least the
retention age ago, so a run never splits a day by time of day. Defaults are 90 days and 1,000
records, with an enforced maximum of 10,000 records per run. Because a batch can still end part-way
through a day, the daily cost rollup treats every day before the latest purge cutoff as final and
never recomputes it from the rows left behind.

The lifecycle is:

1. lock an eligible batch with `FOR UPDATE ... SKIP LOCKED`;
2. collect related idempotency, inbound-webhook, and outbound-webhook-failure records;
3. serialize canonical JSON Lines;
4. encrypt the batch with AES-256-GCM under a versioned operator key;
5. atomically write a mode-0600 archive and manifest under a mode-0700 run directory;
6. when purge is enabled, delete the related database rows;
7. commit `retention_runs` and `config_audit` evidence in the same transaction;
8. after the commit, delete referenced object-storage keys through the supplied adapter;
9. write a separate commit-evidence file (`commit.json`) recording the commit and any
   `pending_object_keys` whose deletion failed.

If archive creation or the database transaction fails, the operation must not claim a committed
purge, and no object is deleted. If object deletion fails after the commit, the keys stay in
`pending_object_keys`, and the next run with a deletion adapter retries them before its own batch. A purge containing external object keys fails closed when no
deletion adapter is supplied. `--dry-run` selects and counts an eligible batch without writing an
archive or deleting data.

The retention CLI initializes the same PostgreSQL JSON codecs as the application
pool. JSON payloads and related records must remain structured values so nested
object-storage references are detected before purge. Missing deletion-adapter
configuration fails with a concise CLI error and leaves database rows intact.

Manual execution:

```bash
# Selection only
uv run python -m pitwall.retention run --archive-dir /secure/archive --dry-run

# Encrypted archive without source deletion
uv run python -m pitwall.retention run --archive-dir /secure/archive

# Encrypted archive followed by bounded purge
uv run python -m pitwall.retention run --archive-dir /secure/archive --purge
```

`DATABASE_URL`, `PITWALL_ARCHIVE_ENCRYPTION_KEY`, and
`PITWALL_ARCHIVE_ENCRYPTION_KEY_VERSION` are required for a real run. The canonical Compose stack
mounts `/var/lib/pitwall/archive` and defaults the reconciler to `archive-purge`; direct source
installations default retention to `off` until explicitly configured.

The archive key is not stored with the ciphertext. Key-version metadata supports rotation, but an
operator must retain old keys for the full lifetime of archives encrypted under them.

## 4. PostgreSQL backup/restore drill

`run_pit_restore_drill` in `src/pitwall/ops/backup_drill.py` verifies an actual logical backup:

1. create an isolated temporary database;
2. run custom-format `pg_dump` for the complete source schema;
3. run `pg_restore` into the temporary database;
4. discover every base table in the source schema dynamically;
5. compare source and restore row counts and order-independent SHA-256 content digests for every
   discovered table;
6. drop the temporary database in `finally`;
7. write drill evidence to `config_audit` and a JSON report.

A drill passes only when the dynamic inventory is non-empty and every table matches. `pg_dump`,
`pg_restore`, and `psql` receive credentials through libpq environment variables; database
passwords do not appear in process arguments. The temporary directory is required to be mode 0700
and the dump archive is mode 0600. Before dumping, the drill requires both `pg_dump` and
`pg_restore` to have the same major version as the PostgreSQL server (minor versions may differ).
Install `postgresql-client-<server-major>` or run from a host with matching client tools.

```bash
# No database mutation
uv run python -m pitwall.ops.backup_drill --dry-run

# Real isolated restore and comparison
DATABASE_URL='postgresql://...' uv run python -m pitwall.ops.backup_drill --target latest
```

The module takes `--schema` (default `pitwall`, the schema to dump and restore), `--target`
(default `latest`, a backup label recorded in the report), and `--dry-run` (print the restore plan
and exit `0`). It connects with `PITWALL_DATABASE_URL`, falling back to `DATABASE_URL`, and exits
`0` only when the drill passes, else `1`.

The logical restore drill is not a substitute for a documented backup schedule, off-host backup
storage, key escrow, or a production recovery rehearsal. Those remain operator responsibilities;
see `docs/operator/upgrade-recovery.md`.

## 5. Chaos drill

`src/pitwall/ops/chaos_drill.py` supplies a hermetic dry-run implementation of the kill-switch
contract and deterministic failure probes. It checks stage order, the 30-second duration budget,
zero live compute termination, expected provider failure containment, and expected database outage
containment. It can write a structured drill artifact.

The scheduled wrapper is disabled unless `PITWALL_CHAOS_DRILL_ENABLED` is explicitly true. A
hermetic pass proves orchestration behavior only; live RunPod and real Tailscale acceptance remain
external release and operator gates.

## 6. Principal configuration

| Variable | Purpose |
| --- | --- |
| `RUNPOD_REST_API_URL` | Primary RunPod REST v2 control-plane base |
| `RUNPOD_REST_V1_API_URL` | Bounded legacy pod create/reset base; not a rollback switch |
| `TAILSCALE_OAUTH_CLIENT_ID`, `TAILSCALE_OAUTH_CLIENT_SECRET`, `TAILSCALE_TAILNET` | Real network-sever adapter |
| `R2_ENDPOINT`, `R2_BUCKET_STAGING`, R2/Cloudflare credentials | Optional staging cleanup and temporary credentials |
| `DATABASE_URL` or `PITWALL_DATABASE_URL` | Restore drill and retention source |
| `PITWALL_RETENTION_MODE` | `off`, `archive`, or `archive-purge` |
| `PITWALL_RETENTION_DAYS` | Positive age cutoff in days |
| `PITWALL_RETENTION_BATCH_SIZE` | Bounded batch size, 1–10,000 |
| `PITWALL_ARCHIVE_DIR` | Archive root |
| `PITWALL_ARCHIVE_ENCRYPTION_KEY` | URL-safe base64 encoding of exactly 32 key bytes |
| `PITWALL_ARCHIVE_ENCRYPTION_KEY_VERSION` | Operator-managed key identifier |
| `PITWALL_DRILL_ARTIFACTS_DIR` | JSON drill report directory |
| `PITWALL_CHAOS_DRILL_ENABLED` | Opt-in scheduled hermetic chaos drill |

Never place populated credentials or archive keys in source control. Apply restrictive ownership
and backup policy to the archive and drill-artifact directories.

## 7. Failure semantics

| Surface | Failure behavior |
| --- | --- |
| Kill-switch stage | Error recorded; subsequent emergency stages still attempted |
| Kill-report persistence | Request fails rather than silently claiming durable audit evidence |
| R2 staging cleanup | Per-pod errors recorded; compute termination remains authoritative |
| Retention encryption/write | Transaction fails; no source purge is committed |
| Retention object cleanup without adapter | Fails closed before database deletion |
| Retention database commit | No commit-evidence file is written |
| Backup dump/restore/compare | Drill report fails and temporary database cleanup is attempted |
| Drill-evidence secondary write | Logged separately; the report exposes missing evidence ID |
| Scheduled chaos disabled | Returns without taking action |

## 8. Verification

Relevant tests include:

- `tests/admin/test_kill_switch.py` and `tests/admin/test_kill_switch_route.py`;
- `tests/retention/` and `tests/integration/test_retention_lifecycle.py`;
- `tests/ops/test_backup_drill.py` and `tests/integration/test_backup_restore_drill.py`;
- `tests/ops/test_chaos_drill.py` and the release kill-drill envelope;
- `tests/property/test_retention_properties.py`.

The real backup integration test seeds critical, retention, and webhook tables, uses a database
password containing URL-reserved characters, restores the complete schema, and requires every
source table to match. The retention integration test verifies archive-before-delete ordering,
rollback behavior, encryption, bounded batches, related-row cleanup, and durable evidence.
