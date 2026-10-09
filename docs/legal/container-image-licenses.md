# Container image license obligations

Each published image (`api`, `reconciler`, `webhook`, `cost-exporter`, `mcp`) is built on
`python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d`
(Debian). The reconciler adds `postgresql-client-<major>` from apt.postgresql.org.

- **Python packages:** the base license profile (`tools/security/check_licenses.py`, no extras).
  Images install `uv export --frozen --no-dev` with no extras (`tests/test_dockerfiles.py`).
- **Debian packages:** each release attaches `<service>.spdx.json`, which lists every dpkg package
  and version in that exact image. Their license texts are in each image under
  `/usr/share/doc/<package>/copyright`. Corresponding source for any GPL- or LGPL-licensed Debian
  package is available, for the exact version listed in the SBOM, from the Debian archive
  snapshot service at `https://snapshot.debian.org/package/<source-package>/<version>/`. The
  PostgreSQL client source is at `https://apt.postgresql.org/pub/repos/apt/pool/main/p/`.
  Requests for source can also be made through a GitHub Discussion on this repository, and the
  maintainer will provide it for at least three years after the release that shipped it.
- **Paramiko (LGPL-2.1), certifi (MPL-2.0), tqdm (MPL-2.0 AND MIT):** shipped unmodified with
  their source files and license texts, as recorded in `NOTICE`.
