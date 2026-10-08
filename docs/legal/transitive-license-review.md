# Transitive dependency license review

- Status: Approved by the project owner
- Approval date: 2026-07-18
- Scope: Frozen runtime dependency graph for `pitwall`
- Exact inventory: `docs/sbom/pitwall-sbom.cdx.json` and release-generated report

The automated policy permits common permissive licenses and rejects unknown,
AGPL, GPL-2.0, GPL-3.0, and SSPL terms unless policy is deliberately changed in
a reviewed pull request. Three runtime packages received explicit approval:

| Package | Detected expression | Approved disposition |
| --- | --- | --- |
| `paramiko` 5.0.0 | `LGPL-2.1` | Unmodified pure-Python source and upstream license are preserved in each published image; Pitwall imposes no restriction on replacement, modification, or reverse engineering of the library |
| `certifi` 2026.5.20 | `MPL-2.0` | Unmodified covered files and upstream license are preserved in each published image; Pitwall files remain separately licensed |
| `tqdm` 4.67.3 | `MPL-2.0 AND MIT` | Unmodified source files and the upstream combined license notice are preserved in each published image; Pitwall files remain separately licensed |

The GitHub Release wheel and sdist do not vendor these dependencies. Exact wheel
inspection found only Pitwall's own LICENSE and NOTICE; installers obtain the
declared dependencies separately. The five GHCR service images do redistribute
the packages. Exact local image inspection confirmed that each installed
distribution preserves its upstream license file, and the packages' Python
source form is present rather than compiled into a combined work. The project
NOTICE names the reviewed components and the SBOM records their exact versions.

On 2026-07-18, the project owner approved these dependencies and this treatment
for the GitHub-first alpha, including GHCR publication. This is the project's
release decision, not an independent legal opinion. Approval is invalidated by
a version/license change, modification or vendoring of a covered component, or
a distribution change that removes source, license, replacement, or notice
availability.

`tools/security/license-policy.json` pins these expressions. A version whose
metadata changes fails CI instead of inheriting an earlier conclusion. Vendored
or modified third-party code is prohibited until its source, changes, license,
notices, and distribution obligations are recorded separately.

This engineering classification and owner approval are not independent legal
advice.

## Install profiles (0.3.0a1)

The checker inventories each install profile separately, following requested
extras transitively (`jsonschema[format]` reached only through
`schemathesis[format]` appears in `dev` and not in `base`). `profile_review_required`
in `tools/security/license-policy.json` pins exact reviewed exceptions per profile,
and the denied list includes the `GPLv` classifier spelling.

| Profile | Command | Packages | Reviewed exceptions |
| --- | --- | --- | --- |
| base (wheel runtime and all five images) | `check_licenses.py` | 110 | `certifi`, `paramiko`, `tqdm` (above) |
| extras | `check_licenses.py --extra storage --extra email --extra tracing` | 121 | the base three |
| dev | `check_licenses.py --extra dev` | 213 | the base three, plus `rfc3987`, plus nine MPL-2.0, PSF-2.0, ISC, Apache-2.0, and ZPL-2.1 packages whose installed metadata spells the license in a form outside the allowlist |
| npm (CI only) | `check_licenses.py --npm-lock tools/pi-deps/package-lock.json` | 232 | 98 package versions whose lock entry carries no `license` field (or the permissive `BlueOak-1.0.0`); each entry records the license verified in the installed package's `package.json` (Apache-2.0, MIT, BSD-3-Clause, 0BSD, BlueOak-1.0.0) |

`rfc3987` 1.3.8 (GPLv3+) is a test-only dev dependency that is not redistributed.
It is absent from the wheel, sdist, and images (`uv export --frozen --no-dev
--no-hashes` has no `rfc3987`), so its presence in `uv.lock` places no license
obligation on Pitwall's distributed artifacts. The other dev exceptions are
`bidict`, `fqdn`, `hypothesis`, `hypothesis-jsonschema`, `pathspec` (MPL-2.0),
`defusedxml` (PSF-2.0), `detect-secrets` (Apache-2.0), `isoduration` (ISC), and
`zope-event`, `zope-interface` (ZPL-2.1). Each is test or tooling only and not
distributed; the policy file records the verified license and its PyPI source.

The npm profile covers the four Pi packages that CI's workbench tests and extension
check load (`tools/pi-deps/package-lock.json`, installed with `npm ci --ignore-scripts`).
They are test dependencies and are not shipped in the wheel, sdist, or images.
