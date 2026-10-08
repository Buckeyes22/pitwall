# ADR 0004: Public release identity and distribution channels

- Status: Accepted by the project owner; GitHub-first release scope selected
- Date: 2026-07-18
- Decision owner: Project owner

## Decision

The public identity is:

| Surface | Identifier |
| --- | --- |
| Display name | Pitwall |
| Python distribution | `pitwall` |
| Python import package | `pitwall` |
| CLI | `pitwall` |
| Repository | `https://github.com/Buckeyes22/pitwall` |
| Container namespace | `ghcr.io/buckeyes22/pitwall/<service>` |
| Environment prefix | `PITWALL_` (retained) |

Pitwall Agent Routing is a separately versioned component in the same repository:

| Surface | Identifier |
| --- | --- |
| Display name | Pitwall Agent Routing |
| Python distribution | `pitwall-agent-routing` |
| Python import package | `model_routing` |
| CLIs | `pitwall-agent-routing`, compatibility alias `model-routing` |
| Source root | `packages/agent-routing/` |
| Release tag | `agent-routing/v<version>` |
| First merged version | `0.10.0` |

The distribution and CLI do not use `pitwall`: that name and executable belong
to an unrelated motorsport package on PyPI. The neutral `pitwall`
distribution and image slug also avoid placing a third-party cloud-provider
trademark in the package or image identity. Keeping the import package avoids a
large internal namespace migration and is not a distribution or shell command.

The first public alpha channels are GitHub source, a GitHub prerelease containing
the verified wheel, sdist, checksums, SBOM, and provenance evidence, and GHCR for
the five supported service images. The project owner does not currently have
PyPI or TestPyPI accounts, so both Python registries are explicitly deferred and
do not block the GitHub-first alpha. GHCR is approved and required for the first
alpha; the GitHub prerelease is created only after the exact verified images are
published successfully. No project-hosted SaaS, package mirror, or GPU worker
image is offered.

Agent Routing publishes a GitHub prerelease wheel, sdist, `SHA256SUMS`, installed-wheel SPDX JSON,
and GitHub build attestation. It publishes no image or package-registry artifact in Stage 1. The
component release workflow is disjoint from broker `v*` tags, and broker release jobs cannot be
triggered by `agent-routing/v*`.

## Search and owner-approval record

On 2026-07-18, the project owner confirmed that the project will be published
personally and selected the neutral identity above. The PyPI and TestPyPI JSON
APIs returned 404 for `pitwall`, and the GitHub API returned 404 for
`github.com/buckeyes22/pitwall`. The bare `pitwall` PyPI distribution
is occupied, and public web searches show unrelated products using “Pitwall,”
so the project does not claim exclusive rights in the display word. The
disambiguating distribution slug and GPU-broker description remain mandatory
package identifiers.

These are point-in-time technical searches, not registry reservations. The
project owner accepts the residual naming risk for the first public alpha; no
third-party trademark is intentionally used as part of the product identity.

Before setting `PITWALL_RELEASE_ENABLED=true`, configure the protected
`ghcr-staging`, `ghcr`, and `github-release` environments. PyPI and TestPyPI are
not implemented by the current release workflow; adding them requires a later
decision and trusted-publishing setup.

On 2026-08-31, the project owner simplified the Agent Routing release control. A component release
uses a normal lightweight unsigned `agent-routing/v*` tag on reviewed public ancestry. Pushing that
tag is the publication authorization; there is no approved-signing-key dependency, annotated-tag
or signature requirement, allowed-signers file, component tag-signature ruleset, protected
environment, release-enable variable, manual approval, or disarm operation. Existing Pitwall
signing keys, `v*` tags, release environments, and rulesets remain unchanged.

The component workflow still uses immutable action pins, minimal job permissions, deterministic
double builds, checksums, installed-wheel SPDX JSON, and automatic GitHub artifact attestation.
Attestation is supplemental best-effort provenance: it requires no manual setup and does not gate
GitHub Release creation.

On 2026-08-31, after Agent Routing became a first-class component, the project owner shortened only
the GitHub repository slug from `Buckeyes22/pitwall` to `Buckeyes22/pitwall`. The Python
distribution, CLI, import package, environment prefix, component identities, release tags, and GHCR
container namespace did not change. Canonical repository and raw-content URLs use the new slug;
GitHub's old-slug redirect is compatibility for historical links and published artifacts, not the
source for new documentation or tooling.

Amendment, 2026-09-02: the Agent Routing identity table above still lists a `model-routing`
compatibility alias alongside `pitwall-agent-routing`. That alias has been removed as part of the
`pitwall` plugin-family rename; `[project.scripts]` in `pyproject.toml` now
defines exactly one console script, `pitwall-agent-routing`, and the removal is recorded under the
`0.11.0` entry under "History before unification" in the root `CHANGELOG.md`. This ADR's decision text is left as
originally recorded for the historical account; `pyproject.toml` and the CHANGELOG are authoritative
for the current CLI surface.

## Compatibility and rollback

Artifacts are immutable. A bad Python release is yanked and superseded; a bad
image tag is deprecated and consumers move to a new digest. Existing bytes are
never replaced. Pre-1.0 breaking changes require changelog and schema-diff
approval. Security removals may happen without deprecation when necessary to
make an unsafe path fail closed.
