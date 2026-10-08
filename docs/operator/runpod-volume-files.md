# RunPod Network-Volume Files and Pod Logs

This operator feature transfers bounded files to or from an existing RunPod
network volume and reads bounded pod diagnostics. It is an S3 object operation,
not a pod shell or a replacement for the existing warm-volume workflow.

## Credentials and live safety

The feature resolves only credential *references* at the provider-client
boundary:

| Reference | Purpose |
| --- | --- |
| `RUNPOD_S3_ACCESS_KEY` | Network-volume S3 access key |
| `RUNPOD_S3_SECRET_KEY` | Network-volume S3 secret key |
| `RUNPOD_API_KEY` | Existing RunPod control-plane bearer token for bounded pod logs |

The S3 references must be distinct from `RUNPOD_API_KEY`. Do not put any of
these values in command arguments, URLs, request bodies, TUI fields, or
configuration committed to source control. The RP-04 service passes explicit
S3 values to the network-volume client and disables its compatibility AWS/env
fallbacks, so an unrelated AWS or control-plane credential cannot silently be
used for a file transfer.

All hermetic verification uses fake S3 clients or `httpx.MockTransport`; the
RP-04 tests also deny DNS while exercising the official S3/log URL forms. The
repository's RunPod-specific DNS live gate protects both control-plane hosts
and `s3api-<data-center>.runpod.io`. No live-provider validation is included in
this feature; it remains subject to the separate live countersign.

## Limits and results

The shared `VolumeFileService` is the sole business layer for REST, MCP, CLI,
and the Operations panel. Its default transfer cap is 4 MiB (8 MiB hard cap),
with one 200-item list page, 128 KiB object chunks, 64 KiB / 100 pod-log lines,
and a 30 second provider-call timeout. Hard caps are 500 items, 256 KiB chunks,
128 KiB / 200 log lines, and 60 seconds.

Upload has a stricter effective limit: it is the smaller of the transfer cap
and the pre-spend scanner's limit. The current default scanner accepts at most
256 KiB for the whole structured request, so the usable file body is slightly
smaller after metadata. Current upload content must decode as UTF-8 and be
fully inspectable; opaque binary, oversize, secret-bearing, or PII-bearing
content fails closed in both preview and live execution. REST/MCP framing is
separately capped at 128 KiB. This is suitable for bounded text/config/data
objects, not arbitrary model-weight or binary artifact transfer. Downloads
remain governed by the configured transfer cap and checksum/path controls.

Every surface returns the same stable result fields: operation, status,
volume/data-center/object or pod identifiers, transferred bytes, SHA-256 when
available, bounded objects/content/logs, a truncation flag, and ordered
progress events. Mutation previews also name the provider, target, effect,
estimated byte/request ceiling, and irreversible consequence. Provider and
local failures use safe error codes only; raw provider bodies, local paths,
object bytes, and credentials are not reflected by errors or durable audit
records.

Logs keep source order and available timestamps. Text and structured fields
are redacted before they leave the service. A bounded log response is marked
`truncated` when the byte or line cap cuts it off.

## Operator workflow

The top-level CLI dispatches the shared service through these commands:

```console
pitwall volume-files list VOL_ID DATA_CENTER --prefix configs/ --json
pitwall volume-files upload VOL_ID DATA_CENTER configs/new.json source.json \
  --root ./staging --dry-run --json
pitwall volume-files download VOL_ID DATA_CENTER configs/new.json restore.json \
  --root ./restore --dry-run --json
pitwall volume-files delete VOL_ID DATA_CENTER configs/old.json \
  --confirm-delete --dry-run --json
pitwall volume-files logs POD_ID --max-lines 100 --max-bytes 65536 --json
```

For a real upload or download over an existing destination, pass both
`--overwrite` and `--confirm-overwrite`. A delete requires `--confirm-delete`.
Run the same command with `--dry-run` first: it validates inputs and emits the
shared plan/result shape without an S3 write, local destination write, or
delete. `--json` is machine-stable; human output is intentionally summary-only.

The Operations panel is read-only by default. It lists objects or reads logs,
then invokes a dry-run preview for upload, download, or delete. Applying that
preview requires typing the exact object key in a confirmation modal that names
the provider, target, effect, ceiling, and irreversible consequence. The panel
defaults its overwrite checkbox to off and preserves that exact create-only or
overwrite choice from preview through apply; if a target appears after a
create-only preview, apply refuses replacement. The panel renders ordered
service progress. An empty result is labeled locally; a failed first read is
unavailable, while a failed refresh retains and labels the last successful
result as stale. These are presentation states, not invented provider result
statuses. A cancelled read simply has no result. Cancelling an in-flight
mutation is explicitly ambiguous: the panel says the write may have completed
and directs the operator to inspect provider/local state and durable audit
before retrying the retained preview, which preserves its original idempotency
key where supported. CLI upload/delete similarly require an explicit reusable
`--idempotency-key` for live execution.

REST and MCP use bounded base64 frames instead of unbounded streaming. Direct
REST/MCP mutations must supply explicit `intent` and `idempotency_key` fields,
plus overwrite/delete confirmation. REST mutations belong under the existing
admin authorization namespace and require its configured secret and applicable
bearer scope.

## Durable audit and retry semantics

A live upload, delete, or local download requires the service-owned PostgreSQL
audit sink. Upload/delete processing holds a session-level PostgreSQL advisory
lock for one idempotency key, serializing concurrent equal-key callers through
the exact-key S3 call. Database transactions remain short: one reads/appends
the credential/content-free `started` record, then commits before S3
`PutObject` or `DeleteObject`; another appends `completed` afterward. No
provider I/O occurs inside a database transaction.

A create-only upload sends S3 `PutObject` with `If-None-Match: *`; the earlier
lookup is explanatory validation, not the write guard. Thus a different
idempotency key or external writer that wins after validation cannot be
overwritten. That provider precondition failure returns the stable
`volume_file_precondition_conflict` response and is durably terminal for the
request.

A completed exact request replays its bounded result without a provider call.
Reusing a key with any different operation, volume, data center, object key,
body SHA-256/length, overwrite choice, or expected checksum returns
`volume_file_idempotency_conflict` before a provider mutation. If the provider
call succeeded but the process/connection failed before completion evidence was
known, PostgreSQL releases the session lock. The surviving `started` row then
permits automated recovery only where the provider operation remains safe. An
ambiguous create-only retry repeats the same conditional PutObject; if the
provider reports that the key already exists, Pitwall counts it as recovered
only when bounded reads prove the exact byte length and SHA-256 requested.
Different or unverifiable bytes produce `volume_file_precondition_conflict`
without replacement. An ambiguous overwrite or delete instead returns
`volume_file_mutation_outcome_ambiguous` without another provider mutation:
the original call may have landed, and a later write/recreation is a different
generation that Pitwall must not replace or delete. Inspect provider state and
durable audit manually. No generic retry is offered for reads, logs, or local
file publication.

Local download publication is deliberately non-replayable. Its audit append is
attempted only after the atomic local write. If that append fails, the stable
`volume_file_audit_failed_after_change` response includes `changed: true`, so
the operator inspects the destination rather than assuming rollback or blindly
repeating an overwrite.

## Local-file safety and recovery

Local CLI/TUI paths must be relative to an existing non-symlink root. Traversal,
backslashes, symlinks, special files, and unsafe parent components are rejected.
The service walks the parent chain through no-follow directory descriptors and
publishes a download through an atomic temporary file in that held directory.
Without overwrite confirmation it uses a no-replace link operation, so a target
created concurrently is preserved. Partial files are removed on timeout,
cancellation, checksum mismatch, and provider failure. A checksum mismatch
never replaces an existing destination.

On filesystems without hard-link support (exFAT, some CIFS and FUSE mounts),
the non-overwrite publish checks for an existing file and then moves the
temporary file into place. An existing file still requires overwrite
confirmation, but a file created by another process between the check and the
move is replaced.

The stable error codes, and the HTTP status the REST routes answer with, are:

| Code | HTTP | Meaning |
| --- | --- | --- |
| `invalid_volume_file_request` | 422 | A request field failed validation |
| `volume_file_checksum_mismatch` | 422 | The transferred bytes did not match `--expected-sha256` |
| `pre_spend_payload_rejected` | 422 | The pre-spend scanner blocked an upload before any I/O |
| `volume_file_limit_exceeded` | 413 | A size, line, or item bound was exceeded |
| `volume_file_not_found` | 404 | The object does not exist |
| `volume_file_confirmation_required` | 409 | An overwrite or delete needs its explicit confirmation |
| `volume_file_idempotency_conflict` | 409 | The idempotency key was committed for a different request |
| `volume_file_precondition_conflict` | 409 | A create-only write found an object at the key |
| `volume_file_mutation_outcome_ambiguous` | 409 | A prior mutation may have landed and cannot be repeated safely |
| `volume_file_provider_error` | 502 | The provider call failed |
| `volume_file_timeout` | 504 | The transfer timed out |
| `volume_file_not_configured` | 503 | The volume-file service is not configured |
| `volume_file_audit_unavailable` | 503 | A mutation was stopped because no durable audit sink is configured |
| `volume_file_audit_failed_after_change` | 503 | The mutation completed but its audit write failed; the body adds `"changed": true` |
| `volume_file_error` | 500 | Any other volume-file failure |

If a transfer fails, inspect the stable error code, preserve the existing target,
and retry only after correcting the source, expected SHA-256, credentials, or
provider condition. Do not manually recover a removed temporary file: it was
intentionally private and incomplete.

## Supported and unsupported operations

Supported operations are one bounded S3 object list, upload, chunk read,
safe-path download, delete, and the existing provider-supported pod-log read.
Upload currently supports only bounded, inspectable UTF-8 content under the
pre-spend limit; opaque binary upload is unsupported. The retained warm-volume
path remains unchanged.

This feature does not provide remote execution, SSH, private-key storage,
`croc`, a web terminal, arbitrary S3 endpoints, a new provider endpoint, or
unbounded REST/MCP streaming.

## Surface availability

The production API registers the bounded file/log router, the canonical MCP
registry exposes all five RP-04 tools, the top-level CLI exposes the
`volume-files` command group, and the existing Operations view embeds the
guarded volume-file panel. Route inventory, OpenAPI/auth, MCP registry, CLI
dispatch/help, TUI Pilot, and live-egress contracts pin that integration.
