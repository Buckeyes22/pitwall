# Release acceptance execution

Status: in progress. No release acceptance claim has been made.

This is a dated log. It keeps the command names of the day: `pitwall-agent-routing` is now
`pitwall agents`, `routes` is now `profiles`, and `pitwall-mcp` is now `pitwall mcp serve broker`.

September 21 installed routing follow-up: the installed `pitwall-agent-routing`
entrypoint completed its default offline doctor with 125 PASS, 48 SKIP, 21 WARN,
and zero FAIL across 194 checks. The installed `routes list --json` parsed 20
configured routes. A bounded `routes probe q38-qwen --json --timeout 12` reached
the documented authenticated LAN llama-swap endpoint but received HTTP 401 because
the current shell lacks `LLAMA_SWAP_API_KEY`; this route is **not** counted as a
working inference journey. Raw JSON is private at
`/tmp/subagent-model-routing-finish-20260921/installed-{doctor,routes,q38-probe}.json`.
The earlier installed broker-to-Gateway-to-LAN inference evidence remains the
separate positive proof. No authentication configuration was changed.
The bounded DeepSeek source review for a routing doctor/list/probe defect exited
124 after source reads without a finding; it is not counted as completed review.

## Machine handback priority — September 20 correction

The user has paused other development on their main workstation for whole-project
validation. Tooling expansion displaced that priority; it must not prolong machine
exclusivity. Stop new inventory/evidence framework authoring. Preserve incomplete
artifacts and results. Prioritize installed component and cross-harness journeys,
fix actual product defects inline, rerun affected checks, and verify cleanup of
owned test resources. Complete the acceptance matrix from retained evidence without
representing static discovery, mocked behavior, startup or authorization rejection
as successful end-to-end inference. Matrix work remains in scope.

Machine exclusivity ended after the successful September 20 upgrade and postcheck.
The dedicated PostgreSQL 55444/Redis 56380 test containers, volumes and network
are removed. The persistent local stack is healthy on the reviewed candidate;
its data and credentials are preserved. Other development lanes may resume.
Whole-project release acceptance remains incomplete; matrix work continues.

## Current machine validation — September 20, 21:05 UTC

These results supersede earlier diagnostic statuses only for their stated scope.
The candidate remains a dirty working tree; this is not a release certification.

- Refreshed installed routing links and Claude/Codex plugin copies match the
  candidate. OpenCode completed a real fixture edit and independent test; Codex
  completed its read-only fixture. Claude authentication exists but its weekly
  quota prevented the real work turn. Running clients need a restart to load new
  MCP configuration.
- Repeated canonical journey runner: 27 groups passed. Separate retained logs
  contain 5,707 unit passes (57 skips, 152 deselections) and 115 security passes
  (121 deselections). RA-032 fixes the prior output-overwrite evidence defect.
- Fresh database integration: 148 passes and three skips, followed by the
  PostgreSQL-16 backup/restore case passing separately with the correct client.
  Routing ran 733 tests with four skips. Release tests: 42 passes, eight live
  skips. Workflow policy and OpenAPI compatibility passed.
- Fresh installed broker → installed Gateway → existing LAN model passed exact
  response checks, idempotency, durable completion, OpenAI proxy, SSE completion,
  durable disconnect cancellation, and restart persistence. Initial upstream
  timeouts remain retained; the successful retry ran when the LAN worker was
  idle. Its dedicated database, Redis state, API process and HOME were removed.
- Pi's installed restricted startup exposed RA-033: npm-hoisted dependencies
  were outside the read-only runtime mount. Fixed in source, with focused
  OS-boundary tests and fresh normal/restricted installed startup proof. The
  first real Pi cycle used source imports and is retained as source evidence.
  A corrected runner bound to installed modules and runtime subsequently passed
  all seven gates; root verified its diff and provenance hashes.
- Loaded-data installed TUI navigation covered ten views at two terminal sizes.
  Visual inspection found RA-034: Serve cloud/TTL fields overflowed the view.
  The fix passes all 190 TUI tests and seven independent focused root tests;
  fresh installed visual reproof passed all 20 captures and root viewed both
  corrected Serve screenshots.
- Five rebuilt broker images after the Serve fix pass the configured
  High/Critical, ignore-unfixed Trivy policy. Earlier Compose smoke and cleanup
  passed. Durable local upgrade/restore verification passed after two procedural
  failures exercised successful rollback. All 22 Postgres tables, schema and
  sequences matched the restore; Redis snapshot content matched. Post-cutover
  authenticated inference and durable completion passed.

Raw logs, receipts and checksums are retained privately under
`$PITWALL_EVIDENCE_ROOT/release-acceptance`.
The canonical release matrix, unresolved discovery issues and final acceptance
packet remain incomplete. Machine handback is complete; no whole-project release
completion is asserted here.

## Candidate boundary

Implementation starts from public main commit
`66f323109f67fec47317def53bd3a879067dd91a` on the `feat/release-acceptance` branch.
This is an initial source baseline, not the final frozen artifact candidate.
The separate Pi Workbench checkout contains unpublished implementation and local fixes;
its evidence is historical and does not certify this baseline. Those differences require
reviewed disposition before final release scope can be frozen. Pi Workbench is not shipped
by this initial public baseline. The user explicitly selected including Pi Workbench
and reviewed local fixes in the final candidate; those changes will be imported as
reviewed working-tree patches, without merging private histories or reusing old passes.

## Execution units

| Unit | Executor | Owned output | Expected result | Actual result |
| --- | --- | --- | --- | --- |
| Surface extraction | DeepSeek v4.1 Flash through OpenCode/Alibaba | Inventory tool and its tests | Deterministic code-derived inventory with explicit unresolved extraction issues | HTTP/MCP extractor accepted: 27 focused tests; six other inventory domains remain open |
| Evidence validation | DeepSeek v4.1 Flash through OpenCode/Alibaba | Evidence validator/report tool and its tests | Reject false passes, stale identity, missing lanes, invalid exceptions and corrupt evidence | Known false-pass defects fixed: 91 focused tests; complete provenance and multi-framework adapters remain open |
| Baseline hermetic suite | Orchestrator | Private run log and JUnit | Establish actual source baseline without provider traffic | 5,304 passed, 1 timing-dependent failure, 63 skipped; not acceptance certification |
| Pi integration | DeepSeek; root review | Pi package, routing changes, docs and CI job | Reviewed import and fresh component proof | Imported; 196 Pi tests, 171 targeted routing tests, installed artifact startup verified; boundary-test cleanup correction independently verified |
| Broker local integration | DeepSeek; root review | Broker code, tests and operator documentation | Import reviewed fixes and close new defects inline | Imported; root selections of 274 and 101 tests, 4 release journeys and 9 PostgreSQL cases passed; log-boundary defect fixed |
| Gateway local integration | DeepSeek; root review | Gateway auth/stream fixes and tests | Reviewed import and fresh package proof | RA-017/020/021 fixed; latest independent 55 tests, typecheck, lint and build pass; final artifact rerun remains |
| Remaining fixture defects | DeepSeek | Lease persistence and rate-limit tests | Real DB roundtrip executes; clock test deterministic | Independent combined 46-test selection passed without skips |

Root reviews diffs and independently executes decisive checks. Two external workers may
run concurrently with distinct file ownership. Passing tooling tests does not certify the
product surfaces described by that tooling.

## Known discrepancies requiring disposition

- The planning document says the release fixture accepts only the exact `release` marker.
  Current source also explicitly accepts `release and not live`; the release workflow uses
  that selector. Correct the documentation and preserve executable selector regression tests.
- The invoked installed routing skill still names the legacy `model-routing` launcher.
  Its local symlink target is absent. The repository skill and working runtime use
  `pitwall-agent-routing`; the OpenCode shim successfully uses that runtime. Installed
  plugin upgrade and stale launcher behavior remain acceptance cases.

## Remaining phase gates

- [ ] Source/support declarations reconciled and candidate identity frozen.
- [ ] All exposed interfaces discovered and classified, including extraction uncertainties.
- [ ] All applicable behavioral cases mapped to meaningful tests and proof lanes.
- [ ] Required hermetic, persistence, installed, interactive and operator-live evidence recorded.
- [ ] Defects fixed inline with regression proof and affected evidence rerun.
- [ ] Packaging, upgrade/restore, cleanup and final-candidate reruns complete.
- [ ] Independent review and accurate final acceptance packet complete.

## Baseline findings and verification

- Strict audit: 19/19 passed.
- Database integration: 139 passed, 5 skipped. Two lease cases are permanently skipped for
  unavailable fixture/seed setup; correction is required. Backup/restore initially skipped
  due to PostgreSQL 18 host tools versus PostgreSQL 16 server. A focused rerun using client
  binaries from the pinned test container passed the full restore/checksum assertion.
- Agent Routing: 717 tests run, 4 skipped, no failures.
- Gateway: 18 tests passed.
- Release suite: 35 passed, 2 failed, 8 live cases skipped. Failures concern obsolete SBOM
  filename and removed Dependabot configuration assumptions.
- Artifact build: failed building wheel from sdist because a required wheel inclusion
  (`docs/models`) is absent from the source archive.
- Evidence-tool draft: independent probes found empty-matrix, no-case surface, and
  contradictory-result false passes. These are open defects, despite its initial 32
  focused tests passing. Structured test-result verification is required.

All numbers above describe source-baseline diagnostic runs. They are not final-candidate
acceptance results. Failed original runs and subsequent reruns remain separate.

## Delegation review

The first inventory dispatch was cancelled after extended exploration without a code artifact;
the assignment is being split by interface. The first evidence-validator dispatch timed out
with a draft and 32 passing focused tests. Independent counterexamples reject that draft for
release use. Corrective work remains with DeepSeek; no model fallback or completion claim was
made. Steering messages were sent during those runs, but no acknowledgement was observed, so
these dispatches do not prove sustained bidirectional steering.

The initial flat dispatches did not enable the prompt-level ask-support contract or
explicitly instruct the workers to poll steering. The installed OpenCode channel MCP
server is present, but sent messages without acknowledgements are not delivery proof.
Future interactive dispatches must enable that contract and explicitly verify effects.

The complete source-baseline journey wrapper finished with 27 passed, 0 failed.
The isolated-stack services were used; this is still not a final artifact-candidate run.
Baseline workflow policy, reviewed secret scan, and Bandit checks passed.

Packaging defect fixed by DeepSeek and independently verified: the model-data subtree now
survives sdist creation, the archive allowlist remains narrow, wheel-from-sdist succeeds,
installed artifact smoke succeeds, four new regression cases pass, and lint/format checks
pass. Deployment documentation records the packaging contract.

Release-policy regressions fixed and independently verified (18 combined policy/packaging
cases passed). Assertions now check version-derived SBOM naming/checksums and current
independent frozen dependency authorities. Both supported release marker selectors have
regression coverage and consistent documentation.

After the policy and packaging fixes, the complete release suite reran with 42 passed
and 8 live skips. The live skips remain unresolved acceptance evidence, not passes.

The resumed lane-correction dispatch exited zero without edits or a substantive report.
It did not fix the defect and is not accepted. A fresh bounded correction is queued.
Independent review also found the JUnit parser needs the same resolved-path containment
check as the evidence hasher before opening any file.

Pi Workbench and Agent Routing integration is running in the acceptance worktree.
Broker and gateway local patches are separately queued for review and integration.
No imported component has yet earned final-candidate acceptance.

Independent review of the imported Agent Routing code passed 171 targeted adapter,
workflow, scheduler and doctor tests, Ruff, and mypy. These are working-tree checks.
The first generated HTTP/MCP draft matches the independently discovered 111 HTTP
method/path pairs and 78 MCP tools. Mounted routes, schema-change detection and
loaded-module candidate identity still require review before accepting that generator.

Independent Pi Workbench verification on the imported source: 196 package tests
passed with no skipped testcases in JUnit; typecheck and build passed. The packed
npm artifact installed outside the checkout using the local npm cache. Installed
`doctor` passed, and actual pinned Pi RPC startup selected the fixture model in
normal and Linux restricted modes. This is installation/startup evidence only,
not live inference, full interactive acceptance, or a final frozen candidate.

The Pi import dispatch hit its time limit (exit 124). Independent comparison confirms
all 89 intended Pi source files are present and unchanged from the recorded source
checkout; no source files are missing. Root-owned checks above remain valid as
exploratory verification; the dispatch timeout itself is not a successful result.
The HTTP/MCP worker acknowledged both root steering directives via the actual
OpenCode channel MCP on its current dispatch. This proves those steering receipts,
not the full sustained ASK/ANSWER/STEER/REPORT acceptance journey.

Validator correction: 82 focused tests passed independently. Root reproduced the
original cross-lane case and confirmed it now rejects the absent integration test.
Root also confirmed escaped JUnit paths no longer invoke the XML parser. A further
exact-identity defect remains: a class-method node can borrow a same-name module
function result. Explicit single-lane mappings also need to be honored. Both are
assigned for immediate correction; the overall validator remains unaccepted.

The focused validator suite now passes 91 cases independently, with Ruff passing.
Root adversarial probes confirm exact class identity, explicit single-lane mappings,
and rejection of failed prose as automated proof. The renderer now labels its
summary as unchecked recorded statuses rather than asserting readiness. The known
validator false-pass defects are fixed; multi-framework result adapters, complete
candidate provenance, the real matrix and its CI integration remain unfinished.

Broker local patches are imported. Independent focused verification passed 274
tests covering the affected launch, cleanup, serve, proxy, pod and seed behavior,
plus lint on changed production code. The new bounded-log line-cap defect remains
under correction, and the candidate-specific PostgreSQL checks remain pending.

The broker-port release-journey rerun failed all four J23/J24 test cases on old
fixtures; that failed run is retained. Their corresponding local fixture updates
are assigned to the broker worker, preserving the separately reviewed policy fixes.
Actual inventory extraction after the broker port still finds 111 HTTP operations
and 78 MCP tools. Review found eight advertised wildcard/converter operations
missing schema digests due to OpenAPI path normalization; correction is active.

The bounded pod-log defect is fixed and independently verified with the original
local HTTP counterexample and two new regressions; the affected config/security/API
selection passed 101 tests. The imported J23/J24 fixture updates now pass all four
release-journey cases independently. Their failed and passing runs are preserved
separately; no full release-suite reproof is claimed from those four cases.

The broker worker returned a full scoped report and exited zero while the root
stop request was pending. Independent PostgreSQL verification then passed all
nine provisioning/budget/cost/export cases with no skips. The audit-rollback
fixture gap is closed by the executing integration replacement. The separate
lease-controls persistence skip is still open and assigned for correction.

The bounded HTTP/MCP inventory unit is accepted after 24 independent tests and
Ruff passed. Actual extraction still reports 111 HTTP operations and 78 MCP tools;
all advertised HTTP operations now have schema digests. Six other-domain issues
remain explicitly deferred by this unit and must be closed by subsequent work.
This is discovery tooling, not invocation/behavior coverage of those interfaces.

After the final inventory regressions landed, the combined acceptance-tool suite
passed 118 cases independently (91 evidence, 27 HTTP/MCP inventory). The current
implementation still lacks the complete declaration/matrix dataset, remaining
inventory domains, multi-framework evidence adapters and CI drift integration.

Pi restricted-mode test correction is independently verified: three actual OS/runtime
cases and typecheck pass. The home-read assertion now uses a known owned fixture;
cleanup waits for a terminal child and rejects its hard deadline instead of falsely
resolving success. Temporary workspaces are removed only after confirmed exit.

Lease-control persistence now has executing PostgreSQL insert and conflict-update tests.
The rate-limit case uses a controlled monotonic clock and verifies depletion, partial
refill and full refill. Seed credential references reject raw non-string values before
coercion or database writes. Root independently passed the combined 46-test selection
with zero skips, and verified the lease transition documentation against the source.
These close RA-003, RA-007, RA-014 and RA-015; final candidate reruns remain required.

Post-import diagnostic integration run: 149 passed, two live-gated skips. The
permanently skipped fixture cases and PostgreSQL client-version skip no longer
occur. The broader hermetic run is still active. Tracked local source comparison
finds all 81 changed tracked paths present; 65 are byte-identical and the others
have review fixes, formatting, documentation-link, or pending secret-baseline
dispositions. Neither this comparison nor a component test pass is a release claim.

The post-import hermetic diagnostic run finished with 5,484 passed, 81 skipped,
and no failures. Skip accounting: 24 real-psql cases, 50 exact-release-selector
cases, five individually live-gated cases, and two live collection skips. Root
reran all 24 SQL nodes against the owned PostgreSQL stack using matching client
binaries: 24 passed. Root-wide Ruff and strict mypy (310 source files) passed.
Gateway independent checks passed 23 tests, TypeScript and ESLint. These recorded
component/working-tree checks remain exploratory, not frozen-candidate evidence.

The exact release selector rerun passed 42 cases with eight operator-live skips.
Gateway's build also passed independently. The CLI inventory worker was stopped
(exit 143) after extended exploration without a code artifact; a narrower static
AST-only implementation is queued. No cancelled dispatch is counted as success.
Candidate identity implementation is active with DeepSeek. RA-016 records the
upgrade guide's obsolete unconditional PyPI-yanking instruction and remains open.

Gateway import worker stopped with exit 143 after adding six more regressions.
Root retained and ran them: 28 passed, one failed. The failing test proves that
client disconnection does not abort an embeddings upstream request. RA-017 is
assigned immediately to the identity worker as a small priority correction; the
earlier 23-test result does not certify the expanded gateway change.

Root's full Agent Routing run completed: 733 tests run, four skipped, no failures.
Three skips require a live endpoint; one requires explicit installed Claude/Codex
binaries. The latter is now being exercised with the installed clients and the
existing test's isolated homes. Mutation testing reached the configured 85% gate:
86.7% (2,051 of 2,365 covered mutants killed). Four timeouts remain in the
not-killed denominator; ten uncovered mutants are separately reported by policy.
The mutation result is not a claim of complete branch or surface coverage.

RA-017 is fixed and independently verified: all 29 gateway tests pass with
TypeScript and ESLint checks. Embeddings now shares the request cancellation
lifecycle and closed-response protection already used for chat; gateway SDLC
documents the behavior. The worker acknowledged steering and its source change
has the requested observable effect. Full channel ASK/ANSWER/REPORT proof remains
a separate unfinished journey. Agent Routing wheel and sdist built, passed archive
inspection and installed smoke outside the checkout; owned install directories
were removed and a cleanup/digest receipt retained.

Candidate identity draft timed out after writing an untested module. Root reproduced
tracked-binary omission and ignored-file inclusion (RA-019); the draft is rejected
and a Git-filelist-based rewrite is assigned. The second CLI extraction attempt
timed out without artifacts. Both remain open implementation units. Coverage runs
passed: 83% hermetic against 74%, 84% combined against 77%; risk-domain evaluation
is pending. Installed-client rehearsal found a stale Claude marketplace name
(RA-018); its corrective worker is active.

RA-018 is fixed: root independently passed the actual installed Claude/Codex
marketplace refresh test plus the separate fake Copilot command test (two tests).
Temporary homes/configuration isolated the rehearsal from user installations.
Combined risk-domain coverage checks pass all seven configured domains. RA-016
documentation correction is now assigned as a single-paragraph focused unit.

RA-016 is closed after source-grounded documentation correction and independent
text-policy/link checks. Remediation now addresses immutable GitHub/GHCR artifacts
and states that PyPI/TestPyPI are not current channels. The candidate identity
rewrite remains under review, including parent-symlink containment and fail-closed
readiness. CLI work is narrowed to dispatcher names and console entrypoints first;
nested command/option coverage will remain explicitly unresolved until implemented.

The bounded CLI core dispatch timed out after producing code and tests. Its
first root run failed eight cases; the retained final artifact now passes nine
independent tests, but lint and metadata/source-reference corrections remain.
It covers dispatcher names and console entrypoints only. Candidate identity's
21 focused tests pass, but four independent adversarial checks still fail:
parent/lock symlink containment, remote query/fragment redaction, and no-output
CLI JSON emission. A four-fix worker is active; this unit is not accepted.

The packaged gateway PATH-launcher probe passed authenticated health, exact
chat/embeddings fixtures, incremental SSE and upstream-key separation; cleanup
was independently verified. Subsequent boundary probes found RA-020 (malformed
messages causes 500 under compression) and RA-021 (Responses passthrough
contradicts the SDLC's always-OpenAI-shape statement). Their combined corrective
worker is active. Earlier package success remains scoped and does not erase
these new failing cases.

Gateway RA-020/021 corrections passed independent 55-test, typecheck, lint and build verification. Malformed JSON/message structures reject before upstream invocation; actual Responses passthrough and limited projection/compression semantics are now documented. Root dependency audit reported no known vulnerabilities for auditable dependencies; Pitwall itself was unavailable on PyPI. These are exploratory checks, not final candidate certification.

RA-019 identity corrections now pass 25 independent tests and all six real-Git counterexamples, including parent/lock symlink containment and URL-secret redaction. This accepts the bounded identity tool, not the candidate. Gateway repackaged-launcher verification also passed after RA-020/021, including isolated-install cleanup. Pi audit exposed RA-022: moderate Vitest4.1.9 development-tool advisory; a patch worker is active.

CLI dispatcher/entrypoint core now passes 16 independent tests plus Ruff/format. It emits 42 rows (29 root commands, five flags, one default, seven console entrypoints); nested commands/options/confirmations/output modes remain unresolved. A steering acknowledgement was observed for this dispatch; this alone does not prove ASK/ANSWER/REPORT or sustained continuation.

RA-022 fixed: Vitest and seven family packages pinned to 4.1.11. Independent clean npm install-from-lock, 196 Pi tests, typecheck/build and zero-vulnerability audit passed. No runtime dependency versions changed. npm10 lock resolution failure is retained; npm11 generated the lock. The first root rerun used system npm9.2.0; worker npm10 checks passed, and the separate root npm10.9.4 clean-install rerun also passed all 196 tests. No force or peer-check bypass was used.

Independent exploratory interface probes: 222/222 absent/wrong-auth checks over 111 HTTP method/path rows matched the documented exemptions and 401 boundary. A real MCP stdio session invoked 59 tools with missing required arguments; all 59 returned the exact safe validation error. The 19 tools with no required fields were explicitly not invoked. These prove rejection envelopes only, not authorized handler success, durable state or live provider behavior. Reproducible repository tests and matrix bindings remain assigned.

Installed broker wheel and sdist both passed the real local database-backed smoke: migrations, initialization, API readiness, capabilities response, dry-run inference, and graceful API shutdown. The dedicated database and Redis DB7 were removed/cleared and independently checked. Logs and artifact hashes are retained in the private broker-installed-db receipt. These current-tree runs remain exploratory.

Reproducibility diagnostics passed: two clean builds of each Node component produced identical full dist trees and npm packages (Gateway 29 files, Pi 116). Broker and Agent Routing wheel/sdist pairs also matched across two builds using the recorded SOURCE_DATE_EPOCH. Artifact hashes remain distinct from earlier installed runs where timestamps or lockfiles differed; no cross-artifact acceptance is inferred.

The broad routing extractor dispatch was stopped after 76 exploration calls without code. Its failed dispatch evidence is retained. A smaller registry/host/shim-only extractor is active; plugin, adapter and MCP source contracts remain separate open units.

Support declaration extraction is accepted for its bounded scope: all 32 support-matrix rows, five provider adapter tuples, 173 valid source references, 12 artifact forms, and explicit unreviewed prose domains. Root text-policy and Markdown-link checks pass. The first declaration dispatch timed out; a focused corrective dispatch completed the remaining prose rather than counting the timeout as success.

Routing registry/host/shim extraction passed 12 independent tests, Ruff and formatting. It emits 30 rows: 13 providers, three host declarations and 14 shims. Adapter class contracts, plugin/marketplace manifests and channel/routing MCP tools remain explicitly unresolved.

A controlled real OpenCode/DeepSeek channel sequence passed ASK, orchestrator ANSWER, separate STEER, observed document change, ACK and successful REPORT/sentinel. The private channel-declarations receipt retains correlated mailbox records and checksums. It used the existing installed checkout; eight relevant launcher/channel source files match this candidate byte-for-byte. This is one route and one bounded sequence, not final-candidate or all-harness acceptance.

An independent Textual Pilot probe navigated all ten views by keyboard and resized each from 140x45 to 80x24. Twenty SVG snapshots are retained. The owned HOME was removed. Database access was refused and 56 attempted TCP connections were blocked; DNS was not explicitly blocked, so this is not described as a complete no-egress fixture. This proves navigation and unavailable-state rendering without an app crash, not loaded-data actions or completed visual review.

Node and TUI extraction drafts both timed out and remain unaccepted. Root TUI validation failed five of 17 tests. Node validation failed one of 23, and an independent same-line indirect tool-schema change left its contract digest unchanged (RA-023). Focused correction remains required. RA-025 is a newly reproduced Gateway method defect: POST was accepted on three documented GET-only routes; a dedicated corrective worker is active. No aggregate pass or timeout is counted as acceptance.

All five candidate broker images built and the canonical Compose smoke passed actual readiness, unauthenticated rejection, image/runtime hardening, package separation and graceful shutdown. Existing local ports were preserved; the private derived smoke changed only the checkout path and loopback host to 127.0.0.2, with its exact diff retained. The test project containers, volumes, networks and isolated HOME were independently confirmed removed. Built image IDs and raw-log digests are in the private compose-smoke receipt. This remains exploratory candidate evidence. Workflow policy (17 tests) and OpenAPI compatibility passed; load-smoke was one import test pass and one skip, not load evidence.

RA-025 is fixed and independently verified: 61 Gateway tests, typecheck, lint and build pass. The four original loopback counterexamples now return structured 405 responses with correct Allow and request IDs, with zero upstream calls. The five-route method table tests include HEAD and supported-method controls. The dedicated corrective worker was stopped after repeated reads without edits; root completed this review correction directly.

The rebuilt Gateway package passed the installed wrong-method and HEAD regressions as well as chat, embeddings, SSE and credential separation; owned process/directory cleanup passed. Evidence retention mistake: the archive step used an interpreter relative to the wrong directory and a shell continued, overwriting the prior top-level install/launcher logs. The prior receipt survived in its retained log, and its exact tarball was recovered from the npm content-addressed cache. The prior raw install/launcher logs are not recoverable and that run is not complete acceptance evidence. The new method-specific installed run uses its own directory and retained logs. The run recorder still must make unique output directories mandatory.

Five SPDX image documents were generated. Local container vulnerability scanning remains blocked: installed Grype has no database, Trivy is not on PATH, and the located Trivy cache is dated 2026-08-28 and expired. This does not affect the separate completed pip/npm audits; neither audit proves the OS image scan.

RA-024 bounded TUI inventory correction now passes 23 root tests, Ruff and formatting. The worker timed out after writing its rewrite; root corrected inheritance precedence against installed Textual, retained multiple same-class bindings, fixed declaration source references, recursively included nested modules, and removed the inference that a policy reference proves enforcement. It emits 240 rows with 4 explicit unresolved issues. These are discovery records, not action-success evidence.

RA-023 bounded Node inventory corrections pass 29 independent tests, Ruff and formatting. The indirect-schema counterexample now changes the digest. It emits 47 rows and eight explicit extraction issues. The corrective worker ended without edits; root completed the review corrections. These records are discovery, not behavioral acceptance.

Routing plugin, adapter and channel-MCP contracts pass 17 independent tests, including role-specific schema conflict retention. The runtime probe checks candidate module origins before constructing servers. Its 24 declaration rows comprise 13 adapters, six plugin/marketplace records and five MCP tool-role records; no extraction issues were reported. This does not prove actual provider calls.

The earlier local image-scanner gap was resolved using the repository-pinned Trivy action version, verified release archive and fresh private database. The real gate exposed RA-026: 18 fixable High/Critical findings in the old API image. All five Dockerfiles now pin the refreshed same-Python base and remove runtime pip/ensurepip; immutable services do not install packages. The five rebuilt images pass the configured High/Critical, ignore-unfixed vulnerability/secret/misconfiguration gate. Fresh SPDX documents, report hashes and image IDs are retained. The actual Compose runtime smoke also passed, including the new packaging-boundary checks; its containers, volumes, network and isolated HOME were independently confirmed removed. This closes RA-026 for the exploratory tree, not the final release candidate.

Root reviewed 15 narrow API/MCP assertions against their test bodies and actual collection; 36 tests in those files passed. The source-reviewed bindings explicitly reject authorization-gate traversal and tool listing as handler-success proof. No final-candidate status was inferred.

Visual review of all ten saved narrow TUI snapshots exposed RA-027 (Overview metric cards have zero content height despite stored values) and RA-028 (Loading placeholders survive completed source failures). DeepSeek is correcting Overview with rendered-layout assertions. Root reproduced the latter in Cost, Operations, Resources and original Providers code, corrected those four views, and independently passed their 69 tests plus Ruff. Overview and fresh visual reproof remain pending. The first Providers log named red actually ran after the edit and passed; the separate counterexample log reran original code and failed, with corrected source restored afterward.

RA-027/028/029 are independently verified after correction: the full TUI suite passes 188 tests, Ruff and formatting pass, and fresh source snapshots confirm the Overview metric values, terminal unavailable states, Resources buttons and Operations guardrail preview. Controls below a populated scrollable panel are tested after scrolling rather than falsely requiring every control in the initial viewport. These are bounded fixture/rendering results, not real provider action acceptance.

The Overview DeepSeek dispatch applied and acknowledged all four review steers and left independently verified changes, but its outer shell exited 143 without a final run record. This is retained as an unresolved dispatch anomaly. Controlled real-shim process probes (empty output, 64KiB, larger output and SIGTERM) all retained terminal records and sentinels; no speculative production fix was made without reproducing the cause.

The CLI-argument worker timed out with no files; that unit remains open. Test-index authoring left a draft before cancellation, with no tests, and a dedicated completion worker remains active. Configuration extraction initially passed 16 author tests but failed four independent semantic/drift counterexamples (RA-030); a corrective DeepSeek unit is active. No tooling draft or timed-out worker is counted as acceptance.

RA-030 is fixed for the bounded configuration extractor: all four independent counterexamples now pass, with 22 tests and Ruff. It emits 233 declarations and 18 explicit unresolved issues.

RA-031 test-index correction passes 21 independent fixtures, including real TypeScript parsing, file-qualified Python class identities, imported unittest aliases, C3 inherited-method precedence, cycle handling, dynamic templates and module-level skip/parameterization. All 33 static Gateway test IDs match the retained JUnit names. The product Python comparison found one uncollected live module because it calls module-level skip when unconfigured; the index now marks that collection condition explicitly. Existing test outcomes do not imply final-candidate acceptance. Root authored the regression fixtures after the DeepSeek completion attempt timed out without tests, then fixed the exposed draft defects.

RA-035 fixed: five independent counterexamples exposed duplicate JUnit collection,
unrelated XML roots and contradictory suite counts accepted as pass proof. The
validator now rejects them; all 96 focused tests and Ruff pass. This strengthens
evidence checking without converting aggregate suite results to surface coverage.

RA-036 requires a required case or validated scoped disposition for each exposed
surface; an optional unrun row cannot satisfy release coverage. RA-037 operations
inventory corrections pass 17 independent tests, and RA-038 discovery composition
corrections pass 12. Discovery limitations remain explicit unresolved work.

RA-039 interrupted-run evidence now retains flushed output and a terminal receipt
before re-raising the interrupt. Root independently verified all 14 recorder tests,
including a real disposable process interrupted by SIGINT. Capture loss is explicit.

RA-040 fixed: the validation CLI previously discarded discovery report diagnostics
when extracting rows. It now preserves the report and rejects unresolved status or
issues in release mode. Five red regressions were retained; all 102 validator tests
and Ruff pass. These tooling corrections do not imply product release acceptance.

RA-041 fixed: JUnit nonpassing status attributes and unrecognized result elements
(including retry/flaky results) can no longer silently prove a pass. Five red
counterexamples were retained; all 107 validator tests, Ruff, and mypy pass.

The combined draft now preserves 1,303 discovered surfaces and 1,330 required
`not_run` cases, including 46 source-reviewed bindings. Two parameterized bindings
still need machine-consumable collection proof. All 81 discovery issues and 1,435
gaps remain explicit; zero case passes are claimed. The retained JSON, JSONL,
Markdown, candidate projection and gap ledger have independently verified digests.

RA-042 prevents matrix generation from overwriting an earlier packet or following
an output symlink into candidate source. Two independent red cases now pass;
all 10 assembler tests pass. RA-043 corrected 65 strict typing errors in the
acceptance tools. All 17 tooling modules pass mypy; affected inventory checks
passed in independent groups of 43 and 119. No product release gate was weakened.

Component results now bind exact Vitest and unittest identities through the
matrix validator. Root verified 26 adapter tests, 116 validator tests and parsing
of the retained 197-case Pi report; parsing retained results is not new execution.

Source-to-test review now has 213 partial mappings. Root corrected CLI and REST
oracles that overstated idempotency, unbuffered timing, ordering, or real provider
effects, and excluded extractor-only checks from product route coverage. CLI
argument-forwarding assertions were strengthened; 47 focused checks passed and
five volume-file checks passed again after adding exact object-key assertions.
These mappings remain `not_run` pending final candidate execution bindings.

RA-044 corrected a date-dependent quota test fixture whose fixed October 2026
reset would erase the exhausted-pool lockout. A 2035-clock counterexample failed
the reconstructed original fixture; all six current quota checks pass with a
relative current window. Product runtime behavior was unchanged.

User priority correction: acceptance tooling expansion has displaced product testing.
Further framework expansion is stopped. Current work executes product suites and
user journeys, fixes observed failures inline, and records expected versus actual
results in this existing ledger. Source collection is not execution.

Fresh component execution: Gateway 61/61 tests passed; onboarding, personal setup,
configuration and runtime configuration 125/125 passed. These are hermetic checks,
not proof of real cloud operations or complete user-surface acceptance. Raw logs
and JUnit reports are retained under
`$PITWALL_EVIDENCE_ROOT/release-acceptance/product-execution-20260920`.

The same product execution batch completed CLI/TUI with 437 passed and the
three serve journey modules with six passed. Expected CLI dispatch, validation,
TUI interaction/layout behavior, and simulated serve launch/proxy/teardown,
automation revival, and self-hosted eviction/re-warm assertions succeeded.
These results do not establish all exposed surfaces or real provider lifecycle
coverage. Logs: `cli-tui.log` and `serve-journeys.log` in the absolute evidence
directory above; corresponding JUnit files preserve individual test results.

Direct installed CLI checks used the retained wheel environment from
`$PITWALL_EVIDENCE_ROOT/release-acceptance/machine-broker-final-20260920T2040/tui-installed-candidate-50mOgE/venv`,
a fresh temporary HOME/cwd and an explicit credential-free environment. All 29
advertised group help commands returned exit 0 and usage text. Five behavior
checks passed: packaged Qwen dossier JSON, unknown-model rejection, missing API
configuration rejection with DATABASE_URL diagnostic, unknown command rejection,
and malformed-option rejection. These prove the named behaviors only, not every
command operation. Temporary homes were removed. Individual output and verdicts
are in `installed-cli-help` and `installed-cli-behavior` beneath the product
execution directory.

Security fuzz execution completed with 109 passed and 127 non-fuzz deselections.
It exercises schema-generated requests against the in-process application and
parser invariants with hermetic provider/database doubles; it does not prove
provider operations or persistence. Log: `security-fuzz.log` and per-test
JUnit: `security-fuzz.xml` beneath the product execution directory.

Pi product execution passed 197 tests, typecheck and build. Installed negative
checks rejected symlinked accounting input and a missing credential. Root review
of the latter output found RA-045: routine missing-account configuration produces
an uncaught stack trace. Safe refusal is established, but the user-facing defect
is being fixed inline and will be rechecked in a fresh installed package.

RA-046 fixed an existing J09 false pass: early process exit without a traceback
previously counted as successful TUI boot. Five shell-level scenarios now verify
exit 124, nonempty output, and traceback rejection. Initial unselected test runs
were skipped/deselected; the explicitly release-marked run passed all five.
Journey documentation now describes the real checks rather than claiming six
screen registrations or exact MCP tool-count coverage absent from the harness.

A direct fresh-home installed launch then exposed RA-047: EOF during first-run
setup caused an uncaught traceback. The CLI now cancels with exit 2, without
authorizing the pending confirmation. Nine setup/personal tests pass. A freshly
built wheel, installed offline into a separate Python 3.14.7 environment, passed
the same PTY closed-input scenario with a concise diagnostic and no profile
write. This is a cancellation check, not a successful dashboard boot. Original
failure and corrected installed output remain in the product execution folder.

Luna executed MCP: 228 passed, and API: 520 passed with three skips on the
clean rerun. The original API run had a cancellation synchronization timeout;
RA-050 remains under investigation rather than being erased by rerun success.
A real stdio catalog call now compares returned models with the repository
dossiers. These are hermetic transport/handler checks, not cloud operations.

Agent Routing unittest reported 733 tests run, four skipped: 729 passed, not
733 passes. It exposed RA-048 subprocess stdout leaks; root independently
verified the fix across 32 doctor tests. Root review also found RA-049, a
blocking read that evades the intended MCP probe deadline; its fix is underway.

RA-049 is independently verified: the probe now uses bounded receive waits,
terminates its owned child, closes streams and joins its reader. The real
silent-child regression and all 33 doctor tests passed in the root rerun.

Root independently verified all six stdio transport checks. API skipped cases
are the real RunPod async queue/webhook, lease launch/teardown, and synchronous
inference journeys, all requiring explicit live enablement. They remain unrun
in this batch. RA-050 investigation excluded provider retry/backoff: the failure
was before cancellation at the test stream readiness event. Repeated isolated
measurements did not reproduce it. It remains unresolved, with the original
failure retained and no timeout/assertion weakening.

RA-045 is independently verified after review correction: Pi now emits concise
setup errors and safe generic diagnostics for malformed profile/auth JSON, so
parser excerpts cannot leak credential canaries. Root passed all eight CLI
subprocess checks. A newly packed/installed artifact returned the exact missing
credential diagnostic, exit 1, no stack, and zero requests to the instrumented
local endpoint. Temporary runtime fixtures were removed; receipt is retained at
`$PITWALL_EVIDENCE_ROOT/release-acceptance/machine-pi-missing-account-final-20260920T223300Z/installed-missing-account-receipt.json`.

This batch completes its component executions and the five verified inline
fixes RA-045 through RA-049. RA-050 remains unresolved. It does not complete
release acceptance: untested live provider journeys, other exposed surface
scenarios, final candidate freeze, and the final acceptance matrix remain open.
The acceptance framework drafts are retained but further expansion is paused
at the user's direction in favor of direct product testing.

Product/installed continuation: fresh isolated PostgreSQL/Redis and a wheel
installed outside the checkout passed 34 migrations, repeat migration, repeat
onboarding without duplicate capability/provider, configuration validation,
guarded-reset refusal, authenticated HTTP discovery/dry-run/errors and real
stdio MCP health, capability/provider reads and dry-run inference. Independent
SQL confirmed dry-runs created no workloads. Initial assertions incorrectly
expected reset exit 2 and top-level dry-run fields; observed exit 1 and nested
`result.plan` match the product contract. Original observations remain retained.

Installed retention archive/purge testing found RA-051 (traceback on invalid
configuration) and RA-052 (missing CLI JSON codecs bypassed object-reference
purge protection). Both are fixed inline. Sixteen retention tests pass, including
a real subprocess CLI regression. Fresh wheel proof preserves the object-bearing
workload, structured JSON and uncommitted archive state while refusing purge.
Ordinary dry-run, archive, purge, repeat purge, decryption, digest and file modes
were checked independently. Raw evidence is retained under
`$PITWALL_EVIDENCE_ROOT/release-acceptance/installed-broker-journeys-20260920-b2`.

Agent Routing installed source-link setup/repeat/remove and documented manual
uninstall passed in an isolated home, preserving the foreign file digest. This
is source-link installation, not wheel installation. A separate installed CLI
workflow with a local fake OpenCode executable failed then resumed correctly:
call order seed/1, flaky/1, flaky/2, consumer/1; dependency output reached retry
and consumer; completed seed was not rerun. Receipts:
`$PITWALL_EVIDENCE_ROOT/release-acceptance/installed-routing-final-QBcWLf/run-receipt.json` and
`$PITWALL_EVIDENCE_ROOT/release-acceptance/installed-workflow-final-5oz2iw/run-receipt.json`.

Root independently reran all 15 fresh installed Gateway checks, including
upstream TCP EOF after downstream SSE disconnect before fixture cleanup. RA-053
missing-token startup refusal is verified. All owned listeners and temporary
install files were removed. Evidence:
`$PITWALL_EVIDENCE_ROOT/release-acceptance/gateway-installed-root-b2/09-installed-receipt.json`.

Expanded real installed MCP checks against PostgreSQL passed 11 semantic
oracles: empty cost/quota snapshots, persisted workload/job status and result,
bounded event response, guardrail status and benign preview, missing job/lease
and invalid event limit. The configured installed TUI survived a six-second
PTY boot with title rendered and no traceback. Separate PostgreSQL and Redis
outages returned readiness 503 with the correct failed dependency, retained
liveness 200 and recovered readiness 200 without restarting the installed API.
A data-read route during PostgreSQL loss returned an opaque 500 and recovered
the original response after restart; this does not assert a 503 data-route contract.
The owned broker test containers, volumes and network were removed. All seven
persistent local services were observed healthy. New source fixes and fresh
package proofs have not been rolled into those persistent container images.

Installed Pi session continuation is independently verified. Root repeated the
actual installed CLI journey in a fresh private directory and retained all four
raw synthetic OpenAI request bodies. Their digests match the receipt. A real
write tool created the expected file; the reopened --continue request included
the earlier user message, assistant tool call and tool result; a separate
workspace/agent had none of that history. All three CLI processes exited zero.
Root removed owned fixtures after inspecting the file and request contents.
Evidence: `$PITWALL_EVIDENCE_ROOT/release-acceptance/pi-session-root-b2/independent-oracles.json`.

The original Luna Pi attempts included eight terminal-driving timeouts. Six
retained child logs support trust-dialog/input-driving problems; the first two
lack child logs and request counts and therefore remain incompletely diagnosed.
They are not converted to product passes or conclusively ruled out as product
failures. The subsequent independently repeated positive journey is separate
evidence. Missing/corrupt-session probes opened an empty session and exited;
that does not establish recovery of corrupt conversation content.

This installed-journey batch verified new behavior and fixed RA-051–053 inline.
RA-050 remains an unresolved intermittent API test failure. Release acceptance,
all-surface scenario coverage and unexecuted real-provider lanes remain open.
No paid resources or real user credential/configuration changes were made.

RA-050 follow-up (September 21): root fixed two demonstrated cancellation-test defects: readiness-only waiting masked early request errors/status, and a readiness timeout skipped owned-request cleanup. The original one-second bound and all existing cancellation assertions remain. Three diagnostic regressions fail against the old wait; the cleanup regression fails against the exact original test. Updated module: 37 passed; fixed-seed API: 524 passed, three live skips. The historical timeout trigger was not reproduced and remains unresolved. Evidence: `$PITWALL_EVIDENCE_ROOT/release-acceptance/ra050-root-20260921/REVIEW.md`.

September 21 follow-up: fixed live subprocess output buffering in Agent Routing.
The pump now forwards available bytes with read1 rather than waiting for a complete
64 KiB read. A real-child regression requires both short partial-line streams to
reach logs and terminal before allowing child exit, then verifies full large output.
Old code fails; eight process tests, 19 dispatch/channel tests, and eight installed
runtime tests pass. Runtime deployed to the independent local snapshot with backup;
no broker restart. Private evidence: `$PITWALL_EVIDENCE_ROOT/release-acceptance/live-output-20260921`.

Installed Alibaba DeepSeek/OpenCode tier-1 ASK/ANSWER returned the non-default beta;
a separate enabled-channel run consumed and acknowledged a scope steer and returned
the requested changed marker. Both exited zero. Retain the initial invalid fixture
argument and expired first steering attempt as separate observations, not passes.
The previous diagnosis agents lacked --routing-ask-support, so their missing steer
acknowledgement did not by itself demonstrate a channel defect. Installed Claude
Sonnet one-shot inference also passed; earlier quota block no longer reproduced.
These checks do not certify every harness/model or sustained unattended compliance.
Private evidence: `$PITWALL_EVIDENCE_ROOT/release-acceptance/installed-channel-20260921`.

Claude installed tool/channel follow-up completed in a disposable fixture: a real
Sonnet dispatch asked which action to take, received the non-default repair answer,
read and acknowledged a scope directive, repaired addition, and returned the changed
final marker with exit zero. Root independently reran the unchanged two-case test.
Evidence: `$PITWALL_EVIDENCE_ROOT/release-acceptance/installed-channel-20260921/claude-tool-journey`.
Browser Swagger rendering/Execute remains blocked by the selected browser client's
localhost navigation refusal. HTTP unauthenticated documentation access correctly
returned 401. No auth or production service configuration was changed.

Installed Pi negative-session evidence now verifies missing-history fresh fallback
and malformed-record exclusion, with the corrupt source preserved before fixture
cleanup. Root independently checked raw request digests, installed binary identity,
no prior tool history, three successful CLI exits, fixture removal and listener
closure. Corrupt conversation reconstruction is not advertised; README and SDLC
now clarify the limit. Historical terminal-driving timeouts remain undiagnosed.
Evidence: `$PITWALL_EVIDENCE_ROOT/release-acceptance/pi-session-negative-20260921/independent-oracles.json`.

September 21 installed harness continuation: DeepSeek executed bounded disposable
repair journeys through the installed Kimi and Antigravity shims. Both read files,
repaired the fixture, ran its tests, returned the expected marker, and exited zero.
Root separately inspected the repair, verified the unchanged test/sentinel hashes,
and reran both tests successfully. This proves those exact routes and tool journeys,
not all models or live steering. Grok returned Not signed in and left its fixture
unchanged; its journey is blocked on usable authentication, not passed or rerouted.
Evidence: `$PITWALL_EVIDENCE_ROOT/release-acceptance/finish-20260921/harnesses/root-verification.json`.

Historical unexplained execution review: retained evidence still cannot establish
the cause of the first two Pi terminal-driving timeouts or the original Overview
worker missing its terminal receipt. Later successful runs and the live-output fix
do not resolve those historical causes. Root retained an explicit evidence limitation
in `$PITWALL_EVIDENCE_ROOT/release-acceptance/finish-20260921/anomalies/REPORT.md`.
The bounded DeepSeek review timed out without a final report; it is not a completed
agent audit. Browser testing, RA-050, real RunPod lifecycle tests and final candidate
finalization remain excluded from this execution by the maintainer's explicit instruction.

September 21 DeepSeek continuation found and fixed ignored discovery filters. Capability
cost/source and disabled-only capability/provider requests now filter in SQL before
pagination. Root verified nine old-source regression failures, 27 current API/MCP passes
and 12 PostgreSQL passes; wider worker slice passed 112. Corrected invalid draft test
fixtures and documentation are distinguished from product defects. Ruff/mypy/textpolicy
passed. Gateway config now rejects malformed decimal ports and loopback-looking DNS
names; 14 new cases failed pre-fix, 35 pass post-fix, and all 97 Gateway tests plus build,
types and lint pass. Root additionally verified built launcher refusal and authenticated
ephemeral health. Both fixes are installed; ten HTTP checks, fresh stdio MCP discovery,
and real broker→Gateway→LAN inference passed. Seven services healthy, only API/Gateway
recreated, disposable test infrastructure removed. The current attached MCP connection was closed
by API recreation and requires reconnect; fresh stdio works. Evidence:
`$PITWALL_EVIDENCE_ROOT/release-acceptance/finish-20260921`.

Configuration gap checks: 19 new Python cases pass, 64 with existing runtime tests.
Pi profile/native-policy/runtime settings checks pass 40 cases, including compaction
and continuation over three local synthetic protocol endpoints. These are bounded
behavioral proofs, not completion of the full surface/journey matrix. Installed Cline,
dsh and Pi also passed real LAN repair tasks, independently correlated by prompt and
workspace and rerun by root. The owned Cline hub daemon was stopped after testing.

Qwen Code and goose installed LAN repair journeys also passed independent fixture, immutable test/sentinel, and receipt verification. Grok and Muse remain authentication-blocked. The Hermes timeout trace shows tools resolving relative files under the operator home directory instead of the dispatch workspace; a targeted adapter fix is in progress. The initial diagnostic worker was stopped after failing to establish a cause; root identified the workspace mismatch from retained tool results.

A real brokered OpenCode journey exposed another defect: an existing healthy LAN-backed capability registers and syncs, but dispatch refuses exit 78 because no GPU lease exists. A targeted fix and regression tests are in progress; the journey has not passed. Neither fix is yet installed.

Configuration review now links explicit execution evidence rather than leaving tested cases marked not_run. One added first-run default case plus the R2 suite passed 38 focused cases. Historical configuration reports retain their earlier hashes and counts. This reconciliation does not complete cross-package surface discovery or the acceptance matrix.

## September 21 inline defect closure register

These IDs identify actual demonstrated defects, not inventory gaps. Execution excludes
browser testing, RA-050, real RunPod lifecycle tests and release finalization.

| ID | Defect and correction | Verification and installed state |
| --- | --- | --- |
| RA-054 | Short live subprocess output waited for a full buffer; forward available bytes with read1. | Original regression fails; focused process/channel checks pass; installed. |
| RA-055 | Capability/provider discovery ignored advertised filters; filter in SQL before pagination and forward MCP parameters. | Nine old-code failures, 27 root API/MCP and 12 PostgreSQL checks pass; installed HTTP and fresh stdio verified. |
| RA-056 | Gateway accepted malformed ports and loopback-looking DNS names. | 14 old-code failures; 35 new tests and 97-test Gateway slice pass; installed. |
| RA-057 | Hermes one-shot tools used the home directory rather than the dispatch workspace. | Two old-code regression subcases fail; 43 adapter tests and pristine installed real repair pass. |
| RA-058 | Healthy broker capabilities without GPU leases could not dispatch. | Never-leased routes now require current metadata/model probe; stopped lease history remains sticky; 11 self-heal cases pass. Installed brokered coding journey passes. |
| RA-059 | Gateway model discovery omitted its working explicit upstream. | Seven old-code failures; root corrected body-timeout classification and rejected-body cleanup; all 109 Gateway tests/types/lint/build pass. Installed model discovery and brokered repair pass. |
| RA-060 | Opening an unavailable model dossier crashed the TUI. | Old-code Pilot regression fails; eight focused root cases and installed-runtime Pilot proof pass. Installed. |
| RA-061 | Serve preview/launch error text could display bearer credentials from exceptions. | Two synthetic-canary regressions fail before correction; nine Serve cases pass. Candidate and installed runtime fixed; installed Textual Pilot proved preview and launch redaction (finish-20260921/installed-tui-edges.json). |
| RA-062 | Resizing after a failed Models/Hardware Fit refresh restored cleared stale rows. | Two regressions fail before correction; snapshot/selection/details are now cleared. Focused final regressions and installed Textual Pilot pass for Models and Hardware Fit; stale rows stay cleared after resize. |
| RA-063 | Personal Pods, Routes, and Serve wizard displayed raw exception text. | All five installed Pilot cases failed before the patch and pass afterward, covering six exception branches; 15 candidate tests pass. Backup: `~/.local/state/pitwall-backups/tui-personal-20260921/personal.py`. Evidence: `finish-20260921/tui-personal-gaps/installed-before-fixed-fixture.json`, `installed-after.json`, `root-tests.xml`. |
| RA-064 | Onboarding failure left the result labeled Loading after completion. | Three root regressions fail against original candidate and installed code; all ten onboarding cases pass after correction. Installed source path/hash and separate before/after JUnit retained under `finish-20260921/tui-onboarding-gaps`. |
| RA-065 | Failed Resources query/preview/apply left a previous success result visible. | Three real Pilot regressions fail against installed original and pass after correction. Reviewed installed patch; focused worker suite 37 passes. Evidence: `finish-20260921/tui-resource-gaps/installed-before.xml` and `installed-after.xml`. |
| RA-066 | Doctor warned that Grok lacked a supported hidden CLI flag. | Check now invokes the hidden flag with --help and checks visible output separately. 34 root doctor tests pass; installed Grok CLI contract is PASS. This does not establish Grok authentication. |
| RA-067 | Personal-mode MCP serve ignored dry_run and called the launch service. | Installed original safely reproduces the launch call using a fake provider. Corrected handler calls PersonalServeService.plan only; an autospecced regression rejects the initial incorrect preview-method patch. Candidate, installed host and API image are corrected; host regression passes and deployed image hashes match. |
| RA-068 | Implicit MCP lease selection could select a serverless provider ahead of a pod provider. | Original candidate and installed host fail the mixed-provider regression. Provider type now filters before limit; seven lease-handler cases pass. Corrected installed host passes and API image hashes match. |
| RA-069 | Personal CLI validated ServeSpec and resolved credentials after Gateway startup; invalid values escaped as ValidationError. | Validation and credential checks now precede Gateway startup. Eight worker old-code validation cases and one root credential-order case fail before correction; 24 focused tests and 281 full CLI cases pass. Six installed host probes fail before and pass after; no real Gateway/provider launched. |
| RA-070 | Personal CLI started the optional Gateway before pricing/availability refusal, and runpodctl fallback left an empty environment key in place. | Three CLI regressions fail on the earlier patch; two gateway-start failures fail at the service boundary. Service startup now follows successful planning, gateway errors return a safe refusal before pod creation, and the resolved runpodctl key replaces empty/whitespace ambient values. 355 affected CLI/personal/MCP cases pass; installed host before/after probes and source hashes match. The existing gateway CLI tests now exercise the real service with a fake provider. |

Routing regression suite after RA-057/058: 741 run, four skips, no failures. Full
TUI suite after RA-060: 194 passed; subsequent gap checks are separate evidence,
not implicitly included in that run. Root also executed Overview/Providers empty,
refresh and filtering checks (34 passes) and Models/Providers/Leases resize checks
(45 passes). Models/Hardware Fit source changes require the later focused proofs.

Installed brokered repair dispatch `0c443feb-cf4b-4d09-899a-683d5c594241` used
OpenCode -> localhost broker -> Gateway -> LAN Qwen. Root verified the repair,
unchanged fixture tests/sentinel, exact route metadata, and unchanged user routing
and OpenCode configuration hashes. This is not RunPod or continuation evidence.
Private evidence is under
`$PITWALL_EVIDENCE_ROOT/release-acceptance/finish-20260921`.

Several bounded DeepSeek workers timed out or were stopped without completion;
root implemented RA-057/058 and independently corrected/reviewed the other patches.
A source-only reviewer ran an unauthorized stash/pop on the shared candidate.
Root compared its saved worktree object and confirmed all tracked content restored,
with only the root's subsequent Serve correction differing. That audit was not
accepted. No final candidate or whole-project coverage claim is made.


September 21 final terminal regression batch: 239 tests passed, no skips/failures (`finish-20260921/tui-final.xml`). This incorporates RA-060 through RA-065 and Jobs, personal, onboarding, Resources, Hardware Fit, empty-state and resize oracles. Source/installed hash matches are retained in `finish-20260921/installed-terminal-fixes.json`. Grok doctor RA-066 independently passed 34 tests and the installed local help contract. All seven persistent Docker services remain healthy; no additional service recreation occurred in this terminal batch. Full surface/scenario reconciliation remains open.

REST gap follow-up: lease DELETE now has exact scoped teardown forwarding, empty 204 for successful/missing leases, and teardown-failure propagation checks. All ten lease-contract cases pass (`finish-20260921/lease-delete-contract.xml`); this uses a mocked teardown service and does not perform real RunPod lifecycle work.

Resource-read API gaps: nineteen new isolated HTTP cases pass across list/detail routes for pods, endpoints, templates, volumes, registry auths and Hub templates, plus exact Hub query/pagination forwarding and invalid-input zero-service-call checks. Evidence: `finish-20260921/resource-read-contract.xml`. Fake service responses prove transport contracts, not global authentication or provider behavior.

REST reconciliation follow-up: all five concrete gaps nominated by the 52-route DeepSeek review now have additional proof. Provider health happy/degraded payload checks pass in 25 provider-contract tests; lease stop now also has HTTP (not only direct-handler) proof in 12 lease-contract tests. Installed health/readiness and OpenAPI GET/HEAD auth/body checks pass (`finish-20260921/installed-health-openapi.json`). Other rows retain their documented mock/provider and auth-lane limits.

TUI action reconciliation: DeepSeek mapped 39 of47 advertised action entries to existing behavioral assertions and found eight missing key-path checks. Root added11 passing parameterized Pilot cases covering those eight gaps (`finish-20260921/tui-action-gaps/root-tests.xml`). All47 now have at least one action oracle; other TUI discovery entries/scenarios remain separate reconciliation work. The implementation worker was stopped after repeated probes without producing its owned file; root completed it. Prior239-test suite and these11 new cases are separate runs.

MCP completion batch: 293 tests pass after correcting the stale J10 tool count and the personal planning interface. Six Serve error codes now cross actual JSON-RPC stdio with safe stable responses; resource response forwarding and volume grow refusal, upload checksum, delete and replay have hermetic assertions. These do not exercise real cloud lifecycle. Two reviewed fixes were propagated to the independent snapshot and rebuilt API image; only API and network-sharing Gateway were recreated after confirming no active workload. Seven services are healthy and four authenticated HTTP checks pass. Evidence: `finish-20260921/mcp-full-interface-corrected.xml`, `mcp-installed-host-before.json`, `mcp-installed-host-after.json`, `mcp-installed-receipt.json`. Source/image backup: `~/.local/state/pitwall-backups/mcp-fixes-20260921` and `pitwall/api:before-mcp-fixes-20260921`. Existing attached broker MCP still needs reconnect. Full surface/scenario reconciliation remains open.

Additional September21 closure: eight Config CLI failure/output cases pass using the real settings loader; 57 MCP resource response/service-boundary cases pass including delete replay, missing-resource translation, partial registry replacement failure redaction and exact Hub query forwarding. Pi dynamic event contracts: 26 independent tests plus typecheck pass after root corrected two weak pending-promise assertions. Gateway registered/replaced/cleared executor selection now has actual loopback HTTP proof with fake upstream; types/lint pass. The latter two batches change tests only. The personal CLI worker timed out after its passing23-case draft; root added the missing credential-order regression, completed checks and installed the host correction. Full surface/scenario reconciliation and explicitly excluded lanes remain open.

RA-070 installed host evidence: `finish-20260921/personal-followup-installed-before.json`, `personal-followup-installed-after.json`, `personal-cli-installed-final.json`, and `personal-final-source-hashes.json`. Backups are under `~/.local/state/pitwall-backups/personal-followup-20260921`. The API image was not rebuilt for this host personal-mode CLI correction.

Installed first-run CLI: `~/.local/bin/pitwall setup --yes` ran twice in an isolated temporary home with a synthetic RunPod key. The 0600 endpoint key and single shell export persisted across rerun, with no provider request (`finish-20260921/installed-setup-journey.json`). Eight installed binary checks passed for configuration and invalid personal-serve behavior (`installed-cli-entrypoints.json`). Two new personal serve argument cases assert all advertised model, variant, GPU, cloud, TTL, rate, route and JSON/default forwarding via the real CLI dispatcher with a fake service; these pass separately from the earlier 355-case affected suite. The full 1,303-surface/scenario inventory remains unreconciled.
