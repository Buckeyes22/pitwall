# Release support declarations

Status: source-grounded static declarations. No live provider call, credential, spend,
build, artifact, or acceptance run was performed to produce this file or
[`declarations.json`](declarations.json). No release acceptance claim is made here.

## What this is

`docs/support-matrix.md` remains the **human-authored source authority** for release
support. `declarations.json` is the **machine-readable extraction** of that authority
into the `declarations.v1` schema, together with the cited component docs it names. It
does not supersede or generate the public support matrix. The draft assembler at
`tools/release_acceptance/matrix.py` can generate an external `acceptance-matrix.md`
**evidence report** and its canonical JSON/JSONL inputs. Its rows remain `not_run`
until execution evidence is attached, unresolved gaps remain explicit, and no
acceptance or release claim is made. Treat any disagreement as a defect in whichever
source or generated input drifted.

Every declaration row carries the exact published wording it normalizes
(`source_wording`), a `path:line` source, and six named boundaries: platform, install,
provider, backend, harness, and transport. The `scope.patterns` selector hints are
provisional and imply no tested coverage; this file does not claim exhaustive
all-interface mapping. Inventory units for CLI, TUI, gateway, routing, config, and ops
remain separate.

## Status vocabulary

The `status` field is exactly one of `supported`, `hermetic`, `limited`, `pending`,
`deferred`, or `unsupported`. Each is a normalization of the source phrasing, not a new
judgment:

| Status | Source phrasing normalized |
| --- | --- |
| `supported` | Supported; Existing supported path; Supported component |
| `hermetic` | Supported hermetically |
| `limited` | Alpha/limited; Hosted portable lane; Limited component |
| `pending` | Code-complete; countersign pending |
| `deferred` | Deferred/unavailable |
| `unsupported` | Not provided |

No row records an approval, exception, countersign, credential, or spend authorization.
The absence of such records is deliberate: `pending` means exactly that no provider
credential, spend, or resource authorization was supplied for this release candidate.
That is the quoted source status, not an inspection of this machine's accounts or
authentication. A status normalization must never soften or strengthen the published
wording it quotes.

## No live verification yet versus provider unavailable

These are different facts and must not be collapsed:

- **No live verification yet** (`no_live_verification_yet`) — the code path exists, but
  no live provider call was made because no credential, spend, or resource
  authorization was supplied. This is an unclosed verification gap; it is not a proven
  provider failure and not an approval.
- **Provider capability absent** (`provider_capability_absent`) — the provider contract
  does not offer the capability at all (for example Vast.ai has no inference or
  actual-cost path). There is no positive capability to verify, so this must not be
  reported as a live-credential gap. The static registry must deterministically reject
  the unsupported capability, and deterministic unsupported-operation rejection and
  no-provider-write tests remain required.
- **Capability deferred with no artifact** (`capability_deferred_no_artifact`) — no
  image, workflow, listener, or entry point exists in this repository. Nothing can be
  executed or certified until it is implemented; this is not a provider gap either.

`verification_distinctions` in the JSON assigns each declaration to its kind and names
the source. When reporting coverage, count these separately.

## Required proof lanes

`required_proof_lanes` lists the lanes implied by the *actual* claim in the source, not
an aspiration: `hermetic` for executable contracts/fixtures/security tests/CI, `live`
only where a real provider interaction is what the claim describes, `release` where the
claim is about a built or published artifact, and `manual` where a human observation is
the only possible proof. A lane being required does not mean it was run.

## Coverage in this file

- Every capability row of `docs/support-matrix.md` (33 declarations, including the Pi
  Workbench and Alibaba Cloud Model Studio rows).
- Every provider-capability tuple from the matrix's provider table (exactly six adapter
  rows; the all-adapter contract note is separate metadata so consumers cannot count it
  as a sixth adapter).
- Separate Python, plugin, source, and image artifact forms.
- Current release-channel exclusions (PyPI, TestPyPI, component image/registry, stable
  promotion, GPU worker image).
- Network-MCP rejection and the absent worker image.
- Native host exclusions and the Pi continuation/delegation and sandbox limits.

## Pi Workbench row

The Pi Workbench declaration is `limited` and states the boundaries the support matrix
gives for `pitwall workbench`: the pinned Pi installed by `pitwall agents setup pi`, so Node
22.22.1 or later on Linux with `flock` (util-linux); optional Bubblewrap restricted mode
additionally requires permitted user namespaces and util-linux 2.41 or later for `setpriv
--seccomp-filter`; unfinished native-child resume is not advertised; and there is no network
allowlist and no per-child credential sandbox. Every quote is a substring of
`docs/support-matrix.md` row 37, not live certification. The declaration carries no dated
measurement; any future measurement belongs in its own dated report.

## Sources checked

- `CONTRIBUTING.md`
- `docs/support-matrix.md`
- `docs/release/external-release-gates.md`
- `docs/sdlc/25-pi-workbench.md`
- `docs/agents/routing-readme.md`
- `docs/agents/releasing.md`

## Explicit gaps

The following prose domains were **not** reviewed as declaration sources and remain
open; their absence here is an explicit gap, not a claim that they are unsupported or
unsupported-by-omission:

- CLI, TUI, gateway, routing, config, and ops inventory units (kept separate; no
  exhaustive all-interface mapping is claimed).
- Provider pricing arithmetic, dossier quality, and live quota behavior.
- Agent Routing prompt references, capability cards, and example outputs.
- Operator runbooks, QA lessons, release notes, and dated measurement reports.
