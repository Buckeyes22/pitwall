# Releasing Pitwall

The project is pre-1.0 alpha software. A tag is not permission to publish:
production jobs also require protected environments and the repository variable
`PITWALL_RELEASE_ENABLED=true`. Keep that variable unset until the concise
checklist in `docs/release/external-release-gates.md` is complete.

## Version and compatibility

`pyproject.toml` is the version source of truth. Alpha tags use
`v0.MINOR.PATCHaN`; the changelog must contain the same version and a date no
later than the run date. Follow `docs/compatibility.md`. Never change an applied
migration or overwrite an existing package version or image digest.

## Prepare the candidate

1. Start from a clean clone of `main` and run `uv sync --frozen --extra dev`.
2. Update the version, lockfile, changelog, support/capability matrix, and any
   compatibility notes in one reviewed pull request.
3. Run the local quality, security, integration, build, Compose, and
   artifact-smoke checks. The user journey harness result is
   required: run `scripts/release/run-user-journeys.sh` against a disposable
   Postgres and Redis (`DATABASE_URL`, `REDIS_URL`) and confirm every journey
   passes. Pull-request CI does not run the harness; the tag's
   `release-readiness.yml` runs it again in its `journeys` job. Live-provider testing is optional and always uses the
   operator's own credentials outside release automation.
4. Confirm the protected release environments allow only `v*` tags.
5. Confirm the public repository's history is the single approved root:
   `git rev-list --count HEAD` prints `1` and `git cat-file -p HEAD` has no `parent` line.
6. Confirm a successful `CI` run for that exact commit on the public repository:
   `gh run list --repo Buckeyes22/pitwall --workflow ci.yml --commit "$(git rev-parse HEAD)" --status success --json databaseId --jq length`
   prints at least `1`. CI results from any other repository or commit do not count;
   `release-readiness.yml` enforces the same check against `GITHUB_SHA`.
7. Create and verify a signed annotated tag, then push it:

   ```bash
   git tag -s v0.3.0a1 -m "Pitwall 0.3.0a1"
   git tag -v v0.3.0a1
   git push origin v0.3.0a1
   ```

   The tag signature is verified locally with `git tag -v`. GitHub's tag ruleset controls who may
   create, update, or delete `v*` tags; its "Require signed commits" rule checks commit
   signatures, not tag objects.

## Automated state machine

The tag-triggered release workflow performs the GitHub-first state machine:

1. Full hermetic, integration, and release-readiness checks.
2. Tag, version, date, and clean-source validation.
3. Reproducible wheel/sdist build, metadata/content inspection, and clean install
   of both artifact forms with packaged migrations.
4. Build, scan, inventory, and save the five supported service images: API,
   reconciler, webhook receiver, cost exporter, and MCP.
5. Checksums, SBOMs, GitHub artifact attestations, and immutable candidate upload.
6. Staging GHCR push/pull smoke, followed by publication and attestation of the
   same verified image bytes.
7. A prerelease GitHub Release containing the wheel, sdist, checksums, SBOM, and
   evidence, created only after GHCR publication succeeds.

PyPI and TestPyPI are not part of this release workflow. A future decision may
add them after accounts and trusted publishing are configured. The first alpha
publishes Python artifacts on the GitHub Release and service images on GHCR.

There is no project GPU worker image in the alpha. See ADR 0002.

## After the workflow publishes

- First publication only: for each of `api`, `reconciler`, `webhook`, `cost-exporter`, and
  `mcp`, open the package `ghcr.io/buckeyes22/pitwall/<service>`. In **Package settings**, confirm
  "Inherit access from repository" names `Buckeyes22/pitwall`. Then under **Danger Zone ->
  Change visibility**, select **Public**. Public is irreversible, and new packages start private
  even from a public repository. Leave `pitwall-staging/*` private.
- Anonymous pulls: run `docker logout ghcr.io` and then, for each service,
  `docker pull "$(cat <service>.digest)"` using the downloaded digest files. Every pull succeeds.
- Assets: `gh release download v0.3.0a1 --repo Buckeyes22/pitwall -D /tmp/v0.3.0a1 && cd /tmp/v0.3.0a1 && sha256sum -c SHA256SUMS`
  prints `OK` for every line. Then run
  `gh attestation verify pitwall-0.3.0a1-py3-none-any.whl --repo Buckeyes22/pitwall`.
  For each image, run `gh attestation verify oci://"$(cat api.digest)" --repo Buckeyes22/pitwall`.
- Install: in a clean `python:3.14.7-slim` container, run the release-wheel install string
  (`uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl`),
  then `pitwall --version` prints `0.3.0a1`.

## Verification and rollback

After publication, download the wheel and sdist from the GitHub Release, install
each into a fresh environment, verify `pitwall --version`, run the
artifact/migration smoke, and verify checksums and GitHub attestations. Pull every
GHCR image by digest, confirm non-root operation, and verify documented health
behavior. Record the results in the release evidence bundle.

If a GitHub release artifact is defective, mark the prerelease withdrawn and
publish a corrected version; never reuse the version or tag. If a later PyPI
package is defective, yank it and publish a new version. If an image is
defective, mark its tag unsupported and move consumers to a new digest; never
replace an immutable digest. Stop GHCR publication, or a later optional Python
registry channel, when its rehearsal or any earlier gate fails. Follow
`docs/operator/upgrade-recovery.md` for database-aware rollback.

For a security hotfix, branch from the last acceptable tag, make the smallest
reviewed change, update the changelog/version, rerun the complete state machine,
and publish an advisory. Security urgency does not authorize mutable artifacts
or bypass provenance evidence.
