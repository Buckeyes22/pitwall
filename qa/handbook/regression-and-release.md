# Regression and release

A regression is something that used to work and now doesn't. These checks catch regressions after
changes and before releases.

## QA smoke set

Run the items that fit the change, in this order. Item 1 always runs.

1. Hermetic tests: `make test 2>&1 | tail -15`. Expected: a summary with `passed` and no `failed`.
2. Integration lane:

   ```bash
   docker compose -f docker-compose.testinfra.yml up -d --wait
   make test-int 2>&1 | tail -15
   make down
   ```

   Expected: `passed` and no `failed`.
3. README Quick Start dry-run inference, with the stack up and the README `export` lines set:
   `uv run pitwall db migrate`, `uv run pitwall init --non-interactive`, the API in a second
   terminal, then the README `curl`. Expected: the response contains `"dry_run":true`.
4. The docs page: `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $PITWALL_API_TOKEN" http://127.0.0.1:8080/docs`.
   Expected: `200`.
5. The TUI: `uv run pitwall dashboard` in a terminal with the README `export` lines. Press each
   view key from the footer, then `q`. Expected: every view opens, and no traceback appears.
6. MCP: if the `pitwall-local` server from mission T2-08 is still registered, ask your agent to
   list its tools. Otherwise rely on J10 in the full journey harness.

## Full journey harness

`scripts/release/run-user-journeys.sh` runs every hermetic journey (J01–J27) from
`docs/operator/user-journey-catalog.md`. It resets the database it is given, so run it only against
the local test stack, with the README `export` lines set:

```bash
docker compose -f docker-compose.testinfra.yml up -d --wait
bash scripts/release/run-user-journeys.sh 2>&1 | tee qa/.work/evidence/journeys-$(date +%F).txt | tail -40
echo "exit=${PIPESTATUS[0]}"
make down
```

Expected: a `journey summary` listing each journey, a final `<n> passed, 0 failed`, and `exit=0`.
Ports 18080–18090 must be free first. The script stops if one is in use.

## Release candidate pass

Work through `docs/operator/release-testing-checklist.md` sections 1–7 in order, running each
command exactly as written there. Keep these in mind:

- Section 3 needs the exact marker expression `-m release`. Any other expression skips the gate
  silently.
- Section 6's `make mutation-gate` runs for a long time. Run it last.
- Live tiers are never part of this pass.

Then run the [full journey harness](#full-journey-harness).

## Sign-off report

Post where the maintainer asks (the release pull request or an issue):

- **Release candidate:** the tag or commit (`git rev-parse --short HEAD`)
- **Sections 1–7:** one row each, with the command, the result, and an evidence excerpt
- **Journey harness:** the summary line
- **Open `found-by-qa` issues** by severity, with links
- **Verdict:** ready, or not ready with the blocking issues listed
