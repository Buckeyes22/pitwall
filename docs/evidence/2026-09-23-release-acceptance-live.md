# Release-acceptance live evidence summary (2026-09-20 to 2026-09-21)

Sanitized summaries of the out-of-repository receipts behind the release-acceptance claims
(plan [`2026-09-23-review-remediation.md`](../superpowers/plans/2026-09-23-review-remediation.md),
Task 26). The receipts stay private; each row names its path under `$PITWALL_EVIDENCE_ROOT`, or
under the operator's handoff directory where noted.
Credentials, account names, and workstation details are omitted. These runs exercised the
installed candidate of their date, not the current `main`. They are evidence for what they
observed, not proof for a later candidate.

## Installed broker, gateway, and data

| Date | What ran | Observed | Receipt |
| --- | --- | --- | --- |
| 2026-09-20 | Installed broker → gateway → LAN model: streaming, idempotency, persistence, cancellation | Nine assertions passed: exact response, idempotent replay, persisted completion, SSE `[DONE]`, disconnect terminalization, persistence across restart | `release-acceptance/installed-broker-journeys-20260920-b2/` |
| 2026-09-20 | Persistent seven-service stack upgrade to the candidate images | Third attempt succeeded; the first two rolled back to the captured prior image ids; all seven services healthy after each recovery | `release-acceptance/machine-broker-final-20260920T2040/durable-final-review.json` |
| 2026-09-20 | Backup restoration into an isolated stack | All 22 application tables, schema, and sequences matched the source; Redis snapshot digest matched | `release-acceptance/machine-receipt-index.json` |
| 2026-09-20 | `pitwall gateway doctor` against the installed wheel, then one routed inference | Four doctor checks passed (424 seed rows); broker → gateway → upstream returned `CATALOG_FIX_OK` | handoff directory: `gateway-fix-doctor.json`, `gateway-fix-inference.json` |
| 2026-09-20 | Attached MCP `pitwall_submit_inference` after a harness restart | Workload `wkl_01M30KEEMFXKWR8KZJZKJEXNCQ` completed with the exact reply `PITWALL_RESTART_OK` | handoff directory: `OUTSTANDING-WORK.md`, "Confirmed after harness restart" |
| 2026-09-21 | API and MCP discovery filters, gateway bind and port validation, installed | 27 API/MCP checks and 12 PostgreSQL filter checks passed; nine regressions failed against the original source; ten installed HTTP checks and a real inference passed | `release-acceptance/finish-20260921/installed-update-receipt.json` |
| 2026-09-21 | Installed gateway receipt (bind, port, auth) | Launcher refusals and authenticated ephemeral health verified | `release-acceptance/gateway-installed-root-b2/09-installed-receipt.json` |

## CLI, MCP, and personal serving

| Date | What ran | Observed | Receipt |
| --- | --- | --- | --- |
| 2026-09-21 | `pitwall setup --yes` twice in an isolated `HOME` with a synthetic credential | Both passed; the 0600 endpoint key and the single profile export stayed stable; eight installed config and invalid-serve checks passed | `release-acceptance/finish-20260921/installed-setup-journey.json`, `installed-cli-entrypoints.json` |
| 2026-09-21 | RA-069: invalid serve arguments and missing credentials, installed host | Six probes failed before the fix and passed after; CLI suite 281 passed | `release-acceptance/finish-20260921/personal-cli-installed-after.json` |
| 2026-09-21 | RA-070: personal gateway start order, installed host | The gateway starts once, after planning; price or availability refusals never start it; 355 CLI/personal/MCP cases passed | `release-acceptance/finish-20260921/personal-followup-installed-after.json` |
| 2026-09-21 | RA-067 and RA-068: MCP `dry_run` planning and implicit lease selection, installed image | Both host regressions failed before and passed after; MCP suite 293 passed; provider effects were mocked | `release-acceptance/finish-20260921/mcp-installed-receipt.json` |

## Pi Workbench and harness journeys

| Date | What ran | Observed | Receipt |
| --- | --- | --- | --- |
| 2026-09-20 | Installed Pi Workbench runner | Normal and restricted startup, repair, compaction and continuation, vision, thinking-off, cancellation and queue clearing, and accounting passed | `release-acceptance/machine-pi-final-luna-20260920-203137-r2/baseline-installed-workbench.json` |
| 2026-09-21 | Pi session continuation with independent oracles | Missing-history fresh fallback and malformed-record exclusion verified | `release-acceptance/pi-session-root-b2/independent-oracles.json` |

## Open at the time of these receipts

- **RA-050:** the test's masked early-request errors and its leaked request were fixed
  (`release-acceptance/ra050-root-20260921/REVIEW.md`). The original trigger at the
  stream-readiness event was unresolved.
- **Real RunPod lifecycle:** not run in these batches. Client disconnect does not prove
  remote GPU termination.
