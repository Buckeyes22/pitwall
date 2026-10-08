# Upgrade, rollback, and recovery

## Before an upgrade

Read the changelog and compatibility report, pin every container by digest, take
and verify a Postgres backup, preserve Redis if queued/idempotency state matters,
and retain webhook/archive encryption keys. Run `pitwall db status` and
resolve checksum drift before changing application bytes.

Stop write traffic and background services for any release whose notes require
a maintenance window. Run exactly one migration job; the CLI also holds a
Postgres advisory lock so accidentally concurrent runners serialize. Never edit
an applied SQL file.

## Rollback

Application-only changes can roll back to the previous immutable wheel or image
digest when its schema compatibility is documented. Database migrations are
forward-only unless a release explicitly supplies and tests a reverse path.
When an incompatible migration has run, restore the pre-upgrade backup into a
new database, validate it, then point the prior application version at the
restored database. Do not improvise destructive SQL in production.

## Recovery validation

The backup drill uses credential-safe `pg_dump`/`pg_restore` invocation,
restores the complete application schema to an isolated target, and compares
every source table's row count and content checksum. A release
operator should record recovery point, recovery time, tool versions, encrypted
artifact location, and cleanup. Test passwords containing reserved URL
characters because quoting mistakes often appear only during a restore.
The installed `pg_dump` and `pg_restore` clients must have the same major
version as the PostgreSQL server (minor versions may differ); install the
matching `postgresql-client-<server-major>` package before running a drill.

If a release is defective, stop publication, revoke compromised credentials,
mark the affected releases withdrawn or deprecated in their release notes, and
deprecate the affected image references so consumers move to a new digest.
Publish an advisory and a new version; never replace published artifacts, tags,
or digests in place. PyPI and TestPyPI are not current release channels, so
current remediation acts on the immutable GitHub release assets and GHCR images
described in `docs/release/external-release-gates.md`. See `RELEASING.md` and
`docs/operator/incident-response.md`.
