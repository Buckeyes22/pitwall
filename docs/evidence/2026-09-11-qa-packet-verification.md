# QA packet command verification — 2026-09-11

- **Branch:** `docs/qa-onboarding` at `24907d9`
- **Clean room:** empty home, no `RUNPOD_*` or `PITWALL_*` variables, fresh clone, shared `uv`
  caches, and read-only `gh` through `GH_CONFIG_DIR`
- **Host:** `Linux 7.0.0-30-generic`, Docker `29.7.1`, uv `0.12.2`, Python 3.14.7
- **README Quick Start environment:** every row that needed the stack or the API ran with the
  five README `export` lines (placeholder values only) on the local test stack.

Result `fixed` means the command or its `Expected:` line was wrong and was corrected, or the
product behaved wrongly and was fixed; the row was re-run after the fix.

## Commands

| File | Block | Category | Result | Notes |
| --- | --- | --- | --- | --- |
| `qa/maintainer-setup.md` | 1 | run | pass | gh label list: found-by-qa needs-qa qa-failed qa-packet qa-passed qa-verified and severity:1-4 |
| `qa/maintainer-setup.md` | 2 | syntax | pass | gh label create --color --description --force; labels already exist |
| `qa/bootcamp/B0-welcome.md` | 1-2 | run | pass | after the Start here bootstrap: ls qa/.work shows evidence notes progress.md; first line '# QA progress — (tester's name)' |
| `qa/bootcamp/B1-terminal-basics.md` | 1-4 | run | pass | pwd ends /pitwall; ls has README.md qa src tests, ls -a has .git; qa lists START-HERE.md bootcamp missions; head -5 qa/README.md starts '# QA program'; less is the tester's pager |
| `qa/bootcamp/B1-terminal-basics.md` | 5 | tui | pass | tmux: Tab completes qa/START-HERE.md, Up recalls it, sleep 30 stops on Ctrl-C with ^C |
| `qa/bootcamp/B1-terminal-basics.md` | 6-8 | run | pass | uname -sr starts Linux; ls qa \| wc -l 9; tee wrote qa/.work/evidence/B1-ls.txt; cat missing exit=1, ls exit=0 |
| `qa/bootcamp/B2-install-and-clone.md` | 1-2 | run | pass | ID=ubuntu ID_LIKE=debian; git, gh, curl, make, ss, script, docker, compose, uv all print versions |
| `qa/bootcamp/B2-install-and-clone.md` | 3-5 | install | pass | apt-cache policy: curl make iproute2 util-linux installed; get.docker.com 200; uv installer 200; scripts never executed; script(1) is needed by harness J09 |
| `qa/bootcamp/B2-install-and-clone.md` | 6-7 | run | pass | Logged in to github.com; PRIVATE; origin github.com/Buckeyes22/pitwall; check-ignore prints qa/.work/progress.md |
| `qa/bootcamp/B2-install-and-clone.md` | 8-10 | run | pass | uv sync --frozen --extra dev completes; pitwall 0.1.0a2; 'Usage: pitwall {setup\|...'; six version lines |
| `qa/bootcamp/B3-fresh-eyes-readme.md` | 1-2 | run | pass | docker ps; scratch folder ~/qa-scratch/<date> |
| `qa/bootcamp/B3-fresh-eyes-readme.md` | 3 | browser | pass | gh repo view resolves the repo; --web opens a browser, not opened here |
| `qa/bootcamp/B3-fresh-eyes-readme.md` | 4-6 | run | pass | commands match README Quick Start lines 91-105 and 128-139; API 'Uvicorn running on http://127.0.0.1:8080'; compact JSON with "dry_run":true |
| `qa/bootcamp/B3-fresh-eyes-readme.md` | 7-8 | run | pass | create-capability, seed --mark-healthy, set-provider-health: exit=0 each; compose down stops the stack |
| `qa/bootcamp/B4-git-and-github.md` | 1 | run | fixed | git log --oneline -5; expected no longer pins this branch's commit subjects |
| `qa/bootcamp/B4-git-and-github.md` | 2-4 | run | pass | from a post-merge main: show --stat; 'Switched to a new branch practice/<user>', 'Switched to branch main', 'Deleted branch'; '?? scratch-test.txt' then nothing |
| `qa/bootcamp/B4-git-and-github.md` | 5-6 | run | pass | gh issue list exit 0 (no issues yet); labels found-by-qa needs-qa severity:1-4 exist; search exit 0 |
| `qa/bootcamp/B4-git-and-github.md` | 7-8 | run | fixed | qa/ files found with grep, not head -20 of an alphabetical list; merged PRs show a CI check named CI; the packet PR title and QA notes are C13 |
| `qa/bootcamp/B4-git-and-github.md` | 9-10 | run | pass | Signed-off-by trailer printed per commit; gh repo view --web (browser) |
| `qa/bootcamp/B5-pitwall-tour.md` | 1-2 | run | pass | docker ps; compose up; migrate; init prints an embedding.demo smoke command |
| `qa/bootcamp/B5-pitwall-tour.md` | 3 | run | fixed | help lists the R2 commands; the tier-5 list now matches rule R2 (serve --dry-run and named status forms are allowed earlier) |
| `qa/bootcamp/B5-pitwall-tour.md` | 4 | browser | fixed | env -u PITWALL_API_TOKEN: 'API authorization disabled for loopback development'; /docs 200; /healthz {"ok":true,...} |
| `qa/bootcamp/B5-pitwall-tour.md` | 5 | tui | pass | p, m, ?, q are PitwallApp bindings; views open in the T2-07 tmux run |
| `qa/bootcamp/B5-pitwall-tour.md` | 6-7 | run | pass | README curl dry run selects prov_demo_runpod_lb; make down stops the stack |
| `qa/bootcamp/B6-qa-fundamentals.md` | 1 | run | fixed | E is set in the working clone before cd to the scratch clone; echo prints .../pitwall/qa/.work/evidence |
| `qa/bootcamp/B6-qa-fundamentals.md` | 2 | syntax | pass | template line: the finding's own command with tee to $E |
| `qa/bootcamp/B6-qa-fundamentals.md` | 3-4 | run | pass | gh issue list --state all --search exit 0; bug-report template copies to qa/.work/notes |
| `qa/bootcamp/B6-qa-fundamentals.md` | 5 | syntax | pass | gh issue create --title --body-file --label; labels bug, documentation, found-by-qa, severity:3-medium exist |
| `qa/missions/tier1-docs/T1-01-contributing-walkthrough.md` | 1 | run | pass | sync ok; Python 3.14.7 |
| `qa/missions/tier1-docs/T1-01-contributing-walkthrough.md` | 2 | run | pass | make test 5001 passed |
| `qa/missions/tier1-docs/T1-01-contributing-walkthrough.md` | 3 | run | pass | no rows before; make up exit 0; both services healthy |
| `qa/missions/tier1-docs/T1-01-contributing-walkthrough.md` | 4 | run | pass | make test-int 138 passed |
| `qa/missions/tier1-docs/T1-01-contributing-walkthrough.md` | 5 | run | pass | make down exit 0; no 5444/6380 rows |
| `qa/missions/tier1-docs/T1-01-contributing-walkthrough.md` | 6 | run | pass | all 11 Quality Gates targets present |
| `qa/missions/tier1-docs/T1-01-contributing-walkthrough.md` | 7 | run | pass | 113 passed, 116 deselected |
| `qa/missions/tier1-docs/T1-01-contributing-walkthrough.md` | 8 | run | pending | PR template matches CONTRIBUTING only after lane 17 (F4); re-run then |
| `qa/missions/tier1-docs/T1-02-install-checklist-part-1.md` | 1 | run | fixed | evidence saves used relative paths inside the scratch clone; now $E set at the checkout root |
| `qa/missions/tier1-docs/T1-02-install-checklist-part-1.md` | 2 | run | fixed | was cp .env (ignored by Pitwall, K1); now the checklist exports; count 5 |
| `qa/missions/tier1-docs/T1-02-install-checklist-part-1.md` | 3 | run | pass | both healthy; accepting connections |
| `qa/missions/tier1-docs/T1-02-install-checklist-part-1.md` | 4 | run | pass | 32 applied, 0 pending |
| `qa/missions/tier1-docs/T1-02-install-checklist-part-1.md` | 5 | run | pass | init complete; health updated |
| `qa/missions/tier1-docs/T1-02-install-checklist-part-1.md` | 6 | run | fixed | was llm.demo + jq (K3); now embedding.demo + json.tool; dry_run true, selected_provider_id set |
| `qa/missions/tier1-docs/T1-03-install-checklist-part-2.md` | snippets | run | fixed | seven python3 snippets could not import pitwall; now uv run python; exporter snippet sets app.state.budget; all seven print their expected lines |
| `qa/missions/tier1-docs/T1-03-install-checklist-part-2.md` | 1 | run | fixed | audit expected passed/cost_estimate_usd (not in response) and failed on default caps; checklist, mission, and audit code fixed; ready_to_invoke true |
| `qa/missions/tier1-docs/T1-03-install-checklist-part-2.md` | 2 | run | fixed | bare python (K2); now uv run python; exit 0 |
| `qa/missions/tier1-docs/T1-03-install-checklist-part-2.md` | 3 | run | fixed | bare python (K2); now uv run python |
| `qa/missions/tier1-docs/T1-03-install-checklist-part-2.md` | 4 | run | pass | down twice exit 0; ports clear |
| `qa/missions/tier1-docs/T1-03-install-checklist-part-2.md` | 5 | run | pass | four evidence files exist via $E |
| `qa/missions/tier1-docs/T1-03-install-checklist-part-2.md` | 6 | run | pass | the four files are written by T1-02 steps 1 and 6 and T1-03 steps 7a and 11 |
| `qa/missions/tier1-docs/T1-04-cli-help-vs-reference.md` | 1 | run | pass | Usage line with 27 commands |
| `qa/missions/tier1-docs/T1-04-cli-help-vs-reference.md` | 2 | run | fixed | grep had unescaped backticks in double quotes (command substitution) and matched no headings; now help-vs-exit-table comm; 13 commands lack rows -> lane 17b completes 18-cli.md |
| `qa/missions/tier1-docs/T1-04-cli-help-vs-reference.md` | 3 | run | pass | models list usage |
| `qa/missions/tier1-docs/T1-04-cli-help-vs-reference.md` | 4 | run | pass | missing-runtime-config, exit=78 |
| `qa/missions/tier1-docs/T1-04-cli-help-vs-reference.md` | 5 | run | pass | unrecognized arguments, exit=2 |
| `qa/missions/tier1-docs/T1-04-cli-help-vs-reference.md` | 6 | run | pass | arguments are required: command, exit=2 |
| `qa/missions/tier1-docs/T1-04-cli-help-vs-reference.md` | 7 | run | pass | Refusing destructive database reset, exit=1 |
| `qa/missions/tier1-docs/T1-04-cli-help-vs-reference.md` | 8 | run | pass | valid JSON config report |
| `qa/missions/tier1-docs/T1-05-journey-catalog-vs-harness.md` | post | syntax | fixed | gh issue create used a nonexistent severity:<n> label and no title/body |
| `qa/missions/tier1-docs/T1-05-journey-catalog-vs-harness.md` | 1 | run | pass | both paths |
| `qa/missions/tier1-docs/T1-05-journey-catalog-vs-harness.md` | 2 | run | pass | 27 functions |
| `qa/missions/tier1-docs/T1-05-journey-catalog-vs-harness.md` | 3 | run | pass | saved |
| `qa/missions/tier1-docs/T1-05-journey-catalog-vs-harness.md` | 4 | run | pass | init, start_bg, /v1/inference, one json_assert over both fields |
| `qa/missions/tier1-docs/T1-05-journey-catalog-vs-harness.md` | 5 | run | pass | saved |
| `qa/missions/tier1-docs/T1-05-journey-catalog-vs-harness.md` | 6 | run | pass | j07 at line 253, body printed |
| `qa/missions/tier1-docs/T1-06-configuration-docs.md` | 1 | run | pass | both paths |
| `qa/missions/tier1-docs/T1-06-configuration-docs.md` | 2 | run | pass | matches in both files |
| `qa/missions/tier1-docs/T1-06-configuration-docs.md` | 3 | run | pass | OK report, exit=0 |
| `qa/missions/tier1-docs/T1-06-configuration-docs.md` | 4 | run | pass | names DATABASE_URL, exit=78 |
| `qa/missions/tier1-docs/T1-07-troubleshooting-messages.md` | 1 | run | pass | both paths |
| `qa/missions/tier1-docs/T1-07-troubleshooting-messages.md` | 2 | run | pass | no RunPod credential message, exit=2 |
| `qa/missions/tier1-docs/T1-07-troubleshooting-messages.md` | 3 | run | pass | warm-volume needs DATABASE_URL, exit=2 |
| `qa/missions/tier1-docs/T1-07-troubleshooting-messages.md` | 4 | run | pass | error missing_database_url, exit=2 |
| `qa/missions/tier1-docs/T1-07-troubleshooting-messages.md` | 5 | run | pass | 7 entries incl. refused, orphaned, dead route |
| `qa/missions/tier2-manual/T2-01-api-in-the-browser.md` | 1 | run | pass | stack, migrate, init |
| `qa/missions/tier2-manual/T2-01-api-in-the-browser.md` | 2 | browser | pass | API without token logs "API authorization disabled for loopback development"; /docs 200 |
| `qa/missions/tier2-manual/T2-01-api-in-the-browser.md` | 3 | run | fixed | provider is at result.plan.selected_provider_id (an earlier review had flattened the path); dry_run true |
| `qa/missions/tier2-manual/T2-01-api-in-the-browser.md` | 4 | run | pass | 404 capability_not_found |
| `qa/missions/tier2-manual/T2-01-api-in-the-browser.md` | 5 | browser | fixed | 401 body is compact JSON; README now documents the /docs token gate |
| `qa/missions/tier2-manual/T2-02-api-with-curl.md` | 1-2 | run | pass | setup; API with token on 8080 |
| `qa/missions/tier2-manual/T2-02-api-with-curl.md` | 3 | run | fixed | compact JSON "dry_run":true and "selected_provider_id":"prov_demo_runpod_lb" |
| `qa/missions/tier2-manual/T2-02-api-with-curl.md` | 4 | run | pass | healthz 200 without token |
| `qa/missions/tier2-manual/T2-02-api-with-curl.md` | 5 | run | pass | 404 capability_not_found |
| `qa/missions/tier2-manual/T2-02-api-with-curl.md` | 6 | run | pass | 422 |
| `qa/missions/tier2-manual/T2-02-api-with-curl.md` | 7 | run | pass | three 404 workload_not_found |
| `qa/missions/tier2-manual/T2-03-api-locks.md` | 1-2 | run | pass | setup; API with token |
| `qa/missions/tier2-manual/T2-03-api-locks.md` | 3 | run | pass | 401, 200, 200 |
| `qa/missions/tier2-manual/T2-03-api-locks.md` | 4 | run | pass | 401, 401 |
| `qa/missions/tier2-manual/T2-03-api-locks.md` | 5 | run | pass | 201 create; 200 disable, enable, audit |
| `qa/missions/tier2-manual/T2-03-api-locks.md` | 6-7 | run | pass | calls 1-3 200, 4-6 429, retry-after 20 |
| `qa/missions/tier2-manual/T2-04-money-safety.md` | 1-3 | run | fixed | API now in its own terminal; healthz 200; 402 budget_rejected reason monthly_budget, estimate_usd 0.060000 > monthly_budget_usd 0.000001 |
| `qa/missions/tier2-manual/T2-04-money-safety.md` | 4 | run | pass | proxy with exhausted budget: 402 |
| `qa/missions/tier2-manual/T2-04-money-safety.md` | 5 | run | fixed | placeholder replaced by J16's literal path: 400 invalid_proxy_path |
| `qa/missions/tier2-manual/T2-04-money-safety.md` | 6 | run | fixed | allow, block (openai_style_token), redact (email) exit 0; malformed JSON 'payload must be valid JSON' exit 2 |
| `qa/missions/tier2-manual/T2-04-money-safety.md` | 7 | run | fixed | missing secret 401 'invalid or missing X-Pitwall-Secret'; wrong secret 401; report pods_terminated 0, errors []; healthz 200; down -v removes the volume |
| `qa/missions/tier2-manual/T2-05-cli-off-the-happy-path.md` | 1-3 | run | fixed | probe function; db status bogus 2, no-DB 1, json 0 valid 0, help 0 (the lane's 'every command accepts --json' was wrong) |
| `qa/missions/tier2-manual/T2-05-cli-off-the-happy-path.md` | 4 | run | fixed | eight commands match the measured table; config check no-DB 78; guardrails and models need no DB |
| `qa/missions/tier2-manual/T2-05-cli-off-the-happy-path.md` | 5 | run | fixed | status --bogus exited 0 on the registry backend -> fixed (C11): now exit 2; --json valid JSON |
| `qa/missions/tier2-manual/T2-06-database-guards.md` | 1-2 | run | pass | setup; db status '32 applied, 0 pending, 32 total.' exit 0 |
| `qa/missions/tier2-manual/T2-06-database-guards.md` | 3 | run | fixed | both migrate runs print 'All 32 migrations already applied.' exit 0 |
| `qa/missions/tier2-manual/T2-06-database-guards.md` | 4-5 | run | pass | reset without --force refuses exit 1; remote host refused naming db.example.invalid exit 1 |
| `qa/missions/tier2-manual/T2-06-database-guards.md` | 6 | run | fixed | reset --force exit 0; status then 'schema_migrations table does not exist yet' exit 1 (lane said pending); migrate Applied 32; init 0 |
| `qa/missions/tier2-manual/T2-07-tui-tour.md` | 1 | run | pass | setup |
| `qa/missions/tier2-manual/T2-07-tui-tour.md` | 2 | tui | fixed | all ten views open without traceback (160x50); Serve view focuses its Model field, so keys need Escape first; documented |
| `qa/missions/tier2-manual/T2-07-tui-tour.md` | 3 | tui | pass | ? help lists keys (count 4), Escape closes |
| `qa/missions/tier2-manual/T2-07-tui-tour.md` | 4 | tui | fixed | tmux: / filter 'orn' leaves the Ornith row; Escape clears; Enter opens the dossier; m returns (Escape does not) |
| `qa/missions/tier2-manual/T2-07-tui-tour.md` | 5 | tui | fixed | database down: the console crashed and printed the DSN and password in traceback locals -> fixed (C14); now 'Overview unavailable: [Errno 111] ...', no traceback |
| `qa/missions/tier2-manual/T2-08-mcp-agent-client.md` | 1 | run | pass | wrapper built as written; execute bits set |
| `qa/missions/tier2-manual/T2-08-mcp-agent-client.md` | 2 | agent | pass | claude mcp add usage verified; server used via --strict-mcp-config (no config written) |
| `qa/missions/tier2-manual/T2-08-mcp-agent-client.md` | 3 | agent | pass | claude mcp add -s/--scope local exists; tools verified with the --strict-mcp-config procedure, no config written |
| `qa/missions/tier2-manual/T2-08-mcp-agent-client.md` | 4 | run | pass | invalid-settings, Input should be 'stdio', exit=78 |
| `qa/missions/tier2-manual/T2-08-mcp-agent-client.md` | 5 | agent | pass | dry_run true, prov_demo_runpod_lb; no workload created |
| `qa/missions/tier2-manual/T2-08-mcp-agent-client.md` | 6 | agent | fixed | both errors reached the client as tool_execution_failed (boundary dropped codes); fixed -> capability_not_found, no_providers_available; non-dry-run does reach the fake provider (expectation reworded) |
| `qa/missions/tier2-manual/T2-09-background-services.md` | 1-10 | run | fixed | all steps as described; replay reply is compact JSON; bad REDIS_URL printed a traceback -> reconciler fixed to a clear error |
| `qa/missions/tier2-manual/T2-10-journey-harness.md` | 1 | run | pass | stack up; ports 8080 and 18080-18090 free |
| `qa/missions/tier2-manual/T2-10-journey-harness.md` | 2 | run | fixed | harness failed J01 (stale path), J07/J08 (inherited token), J09 hang without a tty, J15/J16 (budget 503), J22 (removed flags), J27 (key-dependent tests); all fixed in code and harness |
| `qa/missions/tier2-manual/T2-10-journey-harness.md` | 3 | run | pass | migrate, init, make down |
| `qa/missions/tier2-manual/T2-11-agent-routing-checks.md` | 1 | run | pass | sync ok, .venv present |
| `qa/missions/tier2-manual/T2-11-agent-routing-checks.md` | 2 | run | pass | 540 tests OK (skipped=4), same run as the handbook block |
| `qa/missions/tier2-manual/T2-11-agent-routing-checks.md` | 3 | run | pass | doctor: warn, fail 0, exit=0 (empty HOME) |
| `qa/missions/tier2-manual/T2-11-agent-routing-checks.md` | 4 | run | pass | all seven README subcommands in help |
| `qa/missions/tier2-manual/T2-11-agent-routing-checks.md` | 5 | run | pass | cd back |
| `qa/missions/tier2-manual/T2-12-exploratory-session.md` | 1 | run | pass | template copies (template exists) |
| `qa/missions/tier2-manual/T2-12-exploratory-session.md` | 2 | run | pass | short commit id |
| `qa/missions/tier3-acceptance/T3-01-practice-acceptance.md` | 1 | run | post-merge | search syntax valid; the QA onboarding PR it targets exists only after this branch merges |
| `qa/missions/tier3-acceptance/T3-01-practice-acceptance.md` | 2-5 | syntax | pass | gh pr view, git switch/pull, uv sync, template copy |
| `qa/missions/tier3-acceptance/T3-02-dependency-update-pr.md` | 1 | run | pass | open Dependabot PRs incl. #42 fastapi |
| `qa/missions/tier3-acceptance/T3-02-dependency-update-pr.md` | 2 | run | pass | gh pr checkout 43 (Dependabot) checks out its branch; suite is the branch make test row |
| `qa/missions/tier3-acceptance/T3-02-dependency-update-pr.md` | 3 | tui | pass | dashboard views, as the T2-07 rows |
| `qa/missions/tier3-acceptance/T3-02-dependency-update-pr.md` | 4 | run | pass | make openapi-check: OpenAPI compatibility passed, exit 0 |
| `qa/missions/tier3-acceptance/T3-02-dependency-update-pr.md` | 5 | run | pass | qa-report template copies; $EDITOR is interactive |
| `qa/missions/tier3-acceptance/T3-02-dependency-update-pr.md` | 6 | syntax | pass | gh pr review --approve/--comment/--request-changes/--body-file; gh pr edit --add-label |
| `qa/missions/tier3-acceptance/T3-02-dependency-update-pr.md` | 7 | run | pass | git switch and uv sync --frozen --extra dev, as B2 step 8 and T4-02 step 7 |
| `qa/missions/tier3-acceptance/T3-03-needs-qa-pr.md` | 1 | run | pass | gh pr list --label needs-qa exit 0 |
| `qa/missions/tier3-acceptance/T3-03-needs-qa-pr.md` | 2 | run | pass | same commands as T3-02 step 2 |
| `qa/missions/tier3-acceptance/T3-03-needs-qa-pr.md` | 3 | syntax | pass | the command comes from the pull request's QA notes |
| `qa/missions/tier3-acceptance/T3-03-needs-qa-pr.md` | 4 | run | pass | make test-int is the branch row; template copy |
| `qa/missions/tier3-acceptance/T3-03-needs-qa-pr.md` | 5 | syntax | fixed | gh issue create had no --title/--body-file (prompts); now matches the handbook |
| `qa/missions/tier3-acceptance/T3-03-needs-qa-pr.md` | 6 | run | pass | git switch and uv sync, as T3-02 step 7 |
| `qa/missions/tier3-acceptance/T3-04-verify-fixes.md` | 1 | run | pass | search exit 0 |
| `qa/missions/tier3-acceptance/T3-04-verify-fixes.md` | 2 | syntax | pass | gh issue view --comments exists; no issues exist yet; the repro command comes from the issue |
| `qa/missions/tier3-acceptance/T3-04-verify-fixes.md` | 3-4 | syntax | pass | gh issue comment --body-file, gh issue edit --add-label, gh issue reopen |
| `qa/missions/tier3-acceptance/T3-04-verify-fixes.md` | 5 | run | pass | git switch and uv sync, as T3-02 step 7 |
| `qa/missions/tier3-acceptance/T3-05-release-candidate-pass.md` | 1 | run | pass | rev-parse; clean status; compose up |
| `qa/missions/tier3-acceptance/T3-05-release-candidate-pass.md` | 2 | run | pass | checklist 1-4: ruff clean, audit --strict 19/19 exit 0, -m release 37 passed 8 skipped, kill switch 11 passed; 5-7 targets exist (coverage 77% floor, mutation-gate, alpha harness) |
| `qa/missions/tier3-acceptance/T3-05-release-candidate-pass.md` | 3 | run | pass | same full harness run: 27 passed, 0 failed |
| `qa/missions/tier3-acceptance/T3-05-release-candidate-pass.md` | 4 | run | pass | rev-parse; gh issue list --state open --label found-by-qa exit 0; $EDITOR is interactive |
| `qa/missions/tier3-acceptance/T3-05-release-candidate-pass.md` | 5 | syntax | pass | gh pr comment and gh issue comment --body-file |
| `qa/missions/tier3-acceptance/T3-05-release-candidate-pass.md` | 6 | run | pass | git switch and uv sync, as T3-02 step 7 |
| `qa/missions/tier4-automation/T4-01-reading-the-test-suite.md` | 1-6 | run | pass | folders, markers, hermetic env, 24 passed, single id 1 passed |
| `qa/missions/tier4-automation/T4-01-reading-the-test-suite.md` | 7 | run | pass | security and not fuzz: 113 passed, 116 deselected |
| `qa/missions/tier4-automation/T4-01-reading-the-test-suite.md` | 8-9 | run | pass | broken literal -> 1 failed; restored -> 24 passed; status clean |
| `qa/missions/tier4-automation/T4-02-pre-pr-routine.md` | 1-5 | run | fixed | ruff prints "All checks passed!" / "N files already formatted" (expected text fixed); gh pr list/checks/run view work; practice branch created and deleted |
| `qa/missions/tier4-automation/T4-02-pre-pr-routine.md` | 6 | run | pass | gh run view <id> --log prints job steps |
| `qa/missions/tier4-automation/T4-02-pre-pr-routine.md` | 7-8 | run | pass | practice branch deleted; git status --short empty |
| `qa/missions/tier4-automation/T4-03..T4-06` | — | syntax | fixed | templates with placeholders; git diff <oid>^ <oid> proven on a merge commit; break-by-hand recipe; T4-06 uses full harness runs (single-journey filter only J23/J24/J25/J27) |
| `qa/missions/tier4-automation/T4-07-property-tests.md` | 1-3 | run | fixed | tests/property has conftest.py and *_properties.py; 3 register_profile; pytestmark; @given; example rule now names the real cooldown_duration_for_trip |
| `qa/missions/tier4-automation/T4-07-property-tests.md` | 4 | run | pass | halved_capacity broken: 'Falsifying example', exit 1; restored: 10 passed, exit 0; status clean |
| `qa/missions/tier4-automation/T4-07-property-tests.md` | 5 | run | pass | HYPOTHESIS_PROFILE=ci property lane: 173 passed, 2 skipped, exit=0 |
| `qa/missions/tier4-automation/T4-07-property-tests.md` | 6 | syntax | pass | git push -u; gh pr create --title --body-file; gh pr checks |
| `qa/missions/tier5-live/T5-00..T5-05` | — | live | help | every command and flag used exists: setup, serve (personal and registry), status, stop, leases renew/stop, models list, register-template, register-endpoint, runpod catalogue, audit checks; never executed |
| `qa/missions/tier5-live/T5-01-read-only-live-checks.md` | 3 | live | fixed | CLI cache is per process, so every CLI read is refreshed; mission no longer promises a cache hit |
| `qa/missions/tier5-live/T5-02-personal-serving-smoke.md` | — | live | fixed | rewritten: no DATABASE_URL, pitwall setup first, routing CLI prerequisite (serve now refuses routing_cli_missing before a pod), curl with endpoint key |
| `qa/missions/tier5-live/T5-03-pod-lease-lifecycle.md` | — | live | fixed | rewritten on pitwall serve --capability, leases renew --extends-minutes 5, leases stop |
| `qa/missions/tier5-live/T5-04-live-audit.md` | — | live | fixed | rewritten on the fixed audit runbook (both auth headers, no printed secret) |
| `qa/missions/tier5-live/T5-05-serverless-endpoints.md` | — | live | fixed | rewritten on the fixed LB/vLLM runbooks; serverless_lb provider type, canonical GPU name, disable cleanup |
| `qa/concepts/background-services.md` | 1 | run | pass | exit=0 |
| `qa/concepts/capabilities-and-providers.md` | 1 | run | pass | embedding.demo listed |
| `qa/concepts/cost-budget-and-guardrails.md` | 1 | run | pass | mode line + Guardrail rules table, exit 0 |
| `qa/concepts/docker-and-compose.md` | 1 | run | pass | 5444 and 6380 listed |
| `qa/concepts/dry-run-and-hermetic.md` | 1 | run | fixed | response is compact JSON "dry_run":true; Expected said "dry_run": true |
| `qa/concepts/environment-variables.md` | 1 | run | pass | hello, then empty in a new shell |
| `qa/concepts/expected-vs-actual.md` | 1 | run | pass | 0.1.0a2 twice |
| `qa/concepts/exploratory-testing.md` | 1 | run | pass | 14 |
| `qa/concepts/files-paths-and-permissions.md` | 1 | run | pass | path ends /pitwall; -rw-rw-r-- |
| `qa/concepts/flaky-tests.md` | 1 | run | pass | 24 passed |
| `qa/concepts/git-basics.md` | 1 | run | pass | three commits then short id |
| `qa/concepts/github-issues-and-prs.md` | 1 | run | pass | issues empty, PR list shown (room remote set to GitHub, as a tester's clone has) |
| `qa/concepts/http-and-status-codes.md` | 1 | run | pass | 200 |
| `qa/concepts/json-and-apis.md` | 1 | run | pass | {"ok":true,"backend":"runpod"} |
| `qa/concepts/json-and-apis.md` | 2 | run | pass | pretty-printed |
| `qa/concepts/kill-switch.md` | 1 | run | pass | J21 row |
| `qa/concepts/logs-tracebacks-and-exit-codes.md` | 1 | run | pass | No such file, exit=1 |
| `qa/concepts/logs-tracebacks-and-exit-codes.md` | 2 | run | pass | [missing-runtime-config], exit=78 |
| `qa/concepts/mcp-and-agent-clients.md` | 1 | run | pass | usage, exit 0 |
| `qa/concepts/pitwall-in-one-page.md` | 1 | run | pass | usage and command list |
| `qa/concepts/pods-leases-and-serving.md` | 1 | run | pass | empty Active leases table |
| `qa/concepts/processes-ports-and-localhost.md` | 1 | run | pass | 5444, 6380, 8080 listening |
| `qa/concepts/pytest-fixtures-and-fakes.md` | 1 | run | pass | runpod.py and mcp.py listed |
| `qa/concepts/python-uv-and-venvs.md` | 1 | run | pass | Python 3.14.7 |
| `qa/concepts/regression-and-smoke-tests.md` | 1 | run | pass | 5001 passed, 52 skipped |
| `qa/concepts/reproducing-a-bug.md` | 1 | run | pass | No such file, exit=1 |
| `qa/concepts/routing-and-plans.md` | 1 | run | pass | selected_provider_id prov_demo_runpod_lb |
| `qa/concepts/severity-and-priority.md` | 1 | run | pass | four severity labels |
| `qa/concepts/terminal-and-shell.md` | 1 | run | pass | /bin/bash (host terminal; the env -i room has no SHELL) |
| `qa/concepts/test-lanes-and-markers.md` | 1 | run | fixed | output starts at markers = [, not the [tool.pytest.ini_options] header; Expected reworded |
| `qa/concepts/the-tui.md` | 1 | tui | fixed | same Serve-view focus note added |
| `qa/handbook/acceptance-testing.md` | 1 | run | pass | 540 tests OK (skipped=4) |
| `qa/handbook/bug-reports.md` | 1 | run | pass | exit 0 |
| `qa/handbook/bug-reports.md` | 2 | syntax | pass | --title/--body-file/--label in help; bug, found-by-qa, severity:3-medium exist |
| `qa/handbook/evidence-standard.md` | 1 | run | pass | make test 5001 passed; exit 0 |
| `qa/handbook/live-testing-safety.md` | 1 | live | n/a | key-file steps; never executed |
| `qa/handbook/regression-and-release.md` | 1 | run | pass | make test-int 138 passed, 5 skipped |
| `qa/handbook/regression-and-release.md` | 2 | run | pass | full harness with README exports: 27 passed, 0 failed, exit=0 (after C4, C6) |
| `qa/handbook/test-automation.md` | 1 | run | fixed | git show on a merge commit yields no diff; now git diff <fix>^ <fix>, plus the by-hand path for contract tests |
| `qa/handbook/test-automation.md` | 2 | run | fixed | ruff check and format clean; the sign-off check printed a blank line (tail -1 of %B) and now prints the Signed-off-by trailer |
| `qa/handbook/test-automation.md` | 3 | syntax | pass | git push and gh pr create flags in help |
| `qa/handbook/test-automation.md` | 4 | run | pass | gh pr checks lists jobs; --log-failed in help |
| `qa/handbook/triage-and-labels.md` | 1 | run | pass | three queries exit 0 |
| `(branch)` | make-test | run | pass | code at 78061b9 (later commits are Markdown only): make test 5028 passed, 52 skipped; make test-int 138 passed, 5 skipped; property lane (HYPOTHESIS_PROFILE=ci) 173 passed, 2 skipped |
| `(branch)` | static-gates | run | pass | ruff check and format clean; mypy --strict src/ clean on 290 files; make docs-check passed; secret scan passed (304 reviewed findings); text policy clean on 118 changed Markdown files; shape check 99 qa files |
| `(branch)` | links-external | run | pass | two moved research links replaced earlier; the 8 remaining failures are Buckeyes22/pitwall URLs that 404 without a login while the repo is private; each exists (gh api: pulls 30 and 32, docs/sdlc, README.md, both workflows, discussions and issue templates enabled) |
| `(branch)` | dco | run | pass | DCO sign-off passed for every branch commit: 50 non-merge commits before this evidence commit (tools/ci/check_dco.py --base 71885b9 --head HEAD) |
| `(branch)` | journeys | run | pass | bash scripts/release/run-user-journeys.sh with the README exports at 24907d9: 27 passed, 0 failed, exit 0 |

## Fixes made during verification

| File | What was wrong | Fix | Commit |
| --- | --- | --- | --- |
| `src/pitwall/personal/service.py` | serve launched a paid pod when the routing CLI was missing, then failed route attach | refuse routing_cli_missing before any pod | `42c54a8` |
| `docs/operator/16-check-audit-procedure.md` | printed the admin secret; bare python; stale worktree cd; admin call without bearer | no-print check, uv run, both headers | `e206be1` |
| `docs/operator/create-lb-endpoint.md` | provider_type runpod_serverless and gpu_class ADA_80_PRO fail validation; audit path took an ID | serverless_lb, canonical GPU name, capability name, bearer header | `e206be1` |
| `docs/operator/user-journey-catalog.md` | L3 pointed at a checklist with no L3 procedure | point at the serve quickstart database path | `e206be1` |
| `src/pitwall/cli.py` | init smoke command had no Authorization header when a token was set (401) | reference $PITWALL_API_TOKEN by name | `17d055c` |
| `src/pitwall/audit/capability.py` | audit ignored the budget defaults the spend path enforces; ready_to_invoke false on a default install | resolve caps from settings | `30a2ac3` |
| `docs/operator/install-acceptance-checklist.md` | .env ignored (K1); bare python (K2); llm.demo and jq (K3); python3 snippets could not import pitwall; stale exporter snippet; API killed before Step 7; wrong audit shape | exports, uv run python, embedding.demo with json.tool, current shapes | `b5ca7cc` |
| `qa/missions/tier1-docs/T1-02, T1-03` | followed the broken checklist; evidence saved into the scratch clone | mirror the fixed checklist; $E at the checkout root | `5812d40` |
| `qa/missions/tier1-docs/T1-04-cli-help-vs-reference.md` | grep ran a command substitution and matched nothing | compare --help with the exit-code table via comm | `cf413a2` |
| `qa/missions/tier2-manual/T2-01, T2-02; T4-06` | flattened provider path (orchestrator review error); spaced JSON; single-journey filter | result.plan.selected_provider_id; compact JSON; full harness runs | `5a0a919` |
| `src/pitwall/routing/production.py` | exhausted budget answered 503 no_providers_available on inference, proxy, preview | raise BudgetRejected (402) for the cheapest over-budget ceiling | `e43b55e` |
| `qa/missions/tier1-docs/T1-05; tier3/T3-03` | gh issue create without title/body; nonexistent severity:<n> label | title, body file, severity:<n>-<name> | `9316126` |
| `README.md` | no warning that /docs needs the token once exported | one-sentence note after "start the API" | `3ab3cd7` |
| `src/pitwall/reconciler/__init__.py` | bad REDIS_URL crashed the package import with a traceback before check ran | None at import; worker refuses with the check message | `685ad51` |
| `qa/missions/tier2-manual/T2-09; T5-05; concepts/background-services` | raw replies quoted with spaces | compact JSON | `2277f24` |
| `qa/missions/tier2-manual/T2-07; concepts/the-tui` | Serve view focus swallows view keys | press Escape first | `cbec218` |
| `scripts/release/run-user-journeys.sh` | J01 stale path; J22 removed flags; J09 hang without a tty; inherited token 401s; J27 key-dependent tests | fix each; clean run 27 passed, 0 failed | `67e22da` |
| `src/pitwall/mcp/safe_boundary.py` | MCP errors collapsed to tool_execution_failed despite the documented REST codes | keep class-level error_code | `d973abb` |
| `docs/research` | two external references had moved | replaced with the current URLs | `a154c37` |
| `README.md; docs/sdlc/18-cli.md; CONTRIBUTING.md; PR template; troubleshooting` | nine documentation findings F1-F9 (TUI row, J09 row, config check exit, dashboard bullets, PR QA notes, uv run pattern) | fixed in place | `56a00c4` |
| `src/pitwall/db/__init__.py` | pitwall db status --bogus ran the command and ignored the flag | extra arguments exit 2 like argparse | `72775ee` |
| `src/pitwall/cli_personal.py; src/pitwall/personal/backend.py` | registry backend: pitwall status --bogus exited 0 and --json printed a table; stop dropped --json | parse flags before choosing the backend; forward --json | `58b6a34` |
| `src/pitwall/db/__init__.py; src/pitwall/retention/__main__.py` | db usage listed neither --force nor --json; retention run printed an asyncpg traceback for an unreachable database | usage lists the flags; one error line and exit 1 | `aec0daa` |
| `docs/sdlc/18-cli.md` | 13 commands had no exit-code row; section 6 said parse errors return 1; pitwall with no arguments documented as usage | rows traced to handlers; argparse exits 2, the two hand-written dispatchers exit 1; opens the console | `91aec16` |
| `qa/missions/tier2-manual/T2-05; T2-06` | lane expectations never run (every command accepts --json; migrations pending after reset) | measured exit-code table; the real db status message | `2637ee3` |
| `qa/missions/tier2-manual/T2-04` | API and curl in one block; placeholder SSRF URL; a pkill pattern that never matched | two terminals; J16's literal path; Ctrl-C | `efd40e6` |
| `qa/coach/rules.md` | R2 forbade the pitwall status lines T2-05 needs | exception named | `ac97985` |
| `qa/bootcamp/B1-B6` | B4 pinned branch commit subjects and read qa/ paths from head -20 of an alphabetical list; B5's tier-5 list contradicted R2; B6 set $E in the scratch clone | fixed in place | `4e60ca2` |
| `src/pitwall/tui/app.py; src/pitwall/tui/errors.py; docs/sdlc/18-cli.md` | database down: the console crashed and printed the DSN and password in traceback locals; docs described generic messages the code no longer shows | pool opened per refresh; crash reports without locals; redacted failure lines | `78061b9` |
| `qa/missions/tier2-manual/T2-07; tier4 T4-04 to T4-07; qa/handbook/test-automation.md` | Escape does not leave a dossier; tail -1 of %B printed a blank line; T4-07's example function did not exist | m returns; Signed-off-by trailer format; cooldown_duration_for_trip | `cacb5ee` |

## Weak-model comprehension

Run on 2026-09-17 with Qwen 3.8 27B (`qwen3.8-27b-huihui-int8-mtp`, route profile `q38-pi`, Pi
harness) through `~/.claude/scripts/route-shim.sh`. Each prompt ran from a fresh clone of the
branch, told the model to read files only, and was graded against the answer key in plan Task 22.
Every run ended `SHIM-DONE exit=0` and left the clone unchanged.

| Prompt | Files read | Round | Correct | Files changed |
| --- | --- | --- | --- | --- |
| core | `qa/START-HERE.md` and the four `qa/coach/` files | 1 | 8 of 8 | none |
| B0 | `qa/bootcamp/B0-welcome.md` | 1 | 5 of 5 | none |
| B1 | `qa/bootcamp/B1-terminal-basics.md` | 1 | 5 of 5 | none |
| B2 | `qa/bootcamp/B2-install-and-clone.md` | 1 | 5 of 5 | none |
| B3 | `qa/bootcamp/B3-fresh-eyes-readme.md` | 1 | 5 of 5 | none |
| B4 | `qa/bootcamp/B4-git-and-github.md` | 1 | 5 of 5 | none |
| B5 | `qa/bootcamp/B5-pitwall-tour.md` | 1 | 4 of 5 | `qa/bootcamp/B5-pitwall-tour.md` (`bd83a34`) |
| B5 | `qa/bootcamp/B5-pitwall-tour.md` | 2 | 5 of 5 | none |
| B6 | `qa/bootcamp/B6-qa-fundamentals.md` | 1 | 5 of 5 | none |

B5 round 1, question 5 ("anything you are not allowed to do"): the model listed the never-run
commands but missed the lesson's own trap, that `pitwall dashboard` in a terminal without the
README `export` lines starts `pitwall setup`. The warning sat in step 7's coach text; it now also
opens the lesson under "Before you start". Round 2 named it first.
