# ADR 0007: Free-tier gateway ToS posture and avoid-list contract

- Status: Accepted
- Date: 2026-09-10
- Decision owner: Project maintainer
- Amendment, 0.2.0a1: the `packages/gateway` Node fork described below was replaced by the Python
  gateway (`pitwall gateway serve`, see `docs/sdlc/24-gateway.md`). The strip-list, the vendored
  `open-sse/` subset, and `UPSTREAM.lock` no longer exist; the posture (loopback only, no
  browser-fingerprint transport, no web-session executors, avoid-list respected) and the
  catalog-data attribution stand. The text below is kept as the record of what shipped earlier.

## Context

Pitwall will route inference to free and cheap third-party pools catalogued by
OmniRoute (`open-sse/config/freeModelCatalog.data.ts`, 444 rows on v3.8.51).
Each row carries a hand-curated `tos` verdict (`ok | caution | ambiguous | avoid | unknown`,
`open-sse/config/freeTierCatalog.ts:14`), `freeType`, optional `hardStopGuaranteed`,
`trainsOnPrompts`, and `eligibilityGate`. Decision Q3 (2026-09-10) ships the full
catalog as seed data; Decision Q5 requires the posture that gates those rows to be
recorded before the seeds land.

## Decision

1. The verdict vocabulary is imported verbatim; Pitwall never re-grades a row.
2. Routing eligibility for `zero`-priced providers is:
   - `avoid` and `unknown`: never routable, never enabled by seeds.
   - `caution` and `ambiguous`: routable only when the row carries
     `hardStopGuaranteed: true` or `freeType == "keyless"`.
   - `ok`: routable subject to the same hard-stop/keyless evidence rule.
   - any row with `eligibilityGate`: never routable (counting-only upstream becomes
     routing-excluded here).
3. `trainsOnPrompts: true` is surfaced in every plan explanation, the TUI, and the
   CLI at selection time.
4. No ban-evasion, fingerprinting, or web-session executors are ported (principle 6 of
   the research document); permanent-ban signals are terminal operator-visible states.
5. The fork strip-list (ADR section "Fork strip-list") is completed by Task 18 of the
   implementation plan; until then it reads "pending".

## Fork strip-list

The fork strip-list for `packages/gateway/open-sse/` is `packages/gateway/scripts/strip-list.txt`
(Task 18) and is regenerated verbatim here for traceability. Paths are relative to the
upstream `OmniRoute` root; the import script `packages/gateway/scripts/import-upstream.sh`
copies `open-sse/` minus every entry below, records the upstream SHA, and fails the
import if any of these paths survive or if any `wreq-js` import remains in `open-sse/`.
The MIT copyright chain is preserved by `packages/gateway/LICENSE` and `packages/gateway/NOTICE`.
`UPSTREAM.lock` records the upstream tag, commit SHA, and the SHA-256 of every kept
`open-sse/` file.

- Upstream tag: `v3.8.51`
- Upstream commit SHA: `ba597b631d22d85e56db6982f24b7d1ebe238df9`
- `UPSTREAM.lock` file: `packages/gateway/UPSTREAM.lock`

Strip-list (paths relative to the upstream `OmniRoute` root):

```
src/
electron/
packages/browser-pool/
open-sse/mcp-server/
open-sse/vendor/
open-sse/utils/fingerprint*
open-sse/utils/cch*
open-sse/utils/zwj*
open-sse/executors/*-web.ts
open-sse/executors/claude-web/
open-sse/executors/chatgpt-web-codex/
open-sse/executors/antigravity*
open-sse/executors/adobe-firefly.ts
open-sse/services/browserBackedChat*
open-sse/services/browserPool.ts
open-sse/services/adobeFirefly*
open-sse/services/antigravity*
open-sse/services/chatgptWeb*
open-sse/handlers/imageGeneration*
open-sse/handlers/imageUpscale*
open-sse/handlers/mediaGeneration/
open-sse/handlers/musicGeneration.ts
open-sse/handlers/audio*
open-sse/handlers/search*
open-sse/handlers/cursorCliProxy.ts
docs/
public/
skills/
contrib/
examples/
```

Consequences of this strip-list:

- The dropped `wreq-js` / BoringSSL / ICU4X / Mozilla-CA notice chain from upstream
  is not carried by `packages/gateway/`. Browser-fingerprint transport, browser-pool
  automation, web-session executors, and image/audio/search generation paths are
  excluded from the derivative work by construction.
- `packages/gateway/open-sse/` ships only the eight files the HTTP shim imports
  (error shaping, redaction, and CORS, listed as `TYPE_CHECK_FILES` in the import
  script). The script applies the strip-list, deletes every other copied file, and
  runs `grep -rl "wreq" open-sse` at the end.
- ADR 0007 principle 6 still binds: there is no ban-evasion, fingerprinting, or
  web-session executor in the kept subset, even if a future drift re-adds a
  matching path. On 2026-09-25 a pre-release audit found modules the path
  strip-list had missed (among them `open-sse/config/claudeWebFingerprint.ts`,
  `open-sse/services/claudeTurnstileSolver.ts`, and
  `open-sse/services/sessionPool/fingerprintRotator.ts`); the keep rule above removed
  them with the rest of the unused tree.

## Consequences

- Seeds may contain `avoid` rows with `enabled: false` so the catalog stays complete.
- `routing/zero_cost.py` is the single implementation of rule 2; the planner, CLI, and
  MCP read it, never re-derive it.