# Contributing to Pitwall

Welcome! Pitwall is a broker for GPU inference spend and AI coding-agent routing. This
document covers everything you need to start contributing.

## Dev Environment

```bash
# Clone the repo
git clone https://github.com/<your-github-user>/pitwall.git   # your fork of Buckeyes22/pitwall
cd pitwall

# Create the venv and install all dependencies (including dev)
uv sync --frozen --extra dev --python 3.14.7

# Python 3.14 is the only supported series (requires-python >=3.14,<3.15); the pin is 3.14.7
uv run python --version
```

Pitwall is one project: one `pyproject.toml`, one `uv.lock`, and one `.venv`. Agent Routing, the
gateway, and the Pi workbench live in `src/pitwall/{agents,gateway,workbench}`, not in separate
packages. Run Python through `uv run` or `.venv/bin/python`, never a bare `python`.

The Pi extensions under `src/pitwall/workbench/pi_extensions/` are TypeScript committed with their
compiled JavaScript. After editing one, run `make pi-extensions-check` (Node 22.22.1 or later); it
recompiles with the version in `pi_extensions/COMPILER` and fails if the committed `.js` differs.

Any change to a dependency constraint in `pyproject.toml` must be accompanied by
a regenerated `uv.lock`:

```bash
uv lock            # regenerate after editing a constraint
uv lock --check    # the gate CI runs; fails when the lock is stale
```

`uv sync --frozen` installs whatever the lock already pins and does *not*
validate it against `pyproject.toml`, so a constraint bump without a relock
would otherwise be tested against the old version.

## Running Tests

### Hermetic lane (no GPU billing, no external services)

All unit/integration tests that do not touch real cloud infrastructure run under
the hermetic lane. This is the default `test` target and avoids any RunPod spend.

```bash
make test          # alias for test-fast
```

`test-fast` runs pytest with `-n auto -m "not integration and not slow"` — no Postgres,
no Redis, no external network.

### Integration lane (requires local Postgres + Redis)

The integration suite needs a running Postgres 5444 and Redis 6380. Use the
testinfra compose to bring them up:

```bash
make up            # docker compose -f docker-compose.testinfra.yml up -d --wait
make test-int      # PITWALL_TEST_DATABASE_URL=... PITWALL_TEST_REDIS_URL=... pytest -q -m integration
make down          # docker compose -f docker-compose.testinfra.yml down
```

`make test-db` is an alias for `make test-int`. The suite resets the `pitwall_test` schema, so
run the integration, harness, and coverage suites one at a time against the same database.

## Quality Gates

Run all gates before pushing. CI enforces every one of these on each PR,
except mutation testing and combined coverage, which CI runs only on the
weekly `schedule` trigger (`mutation-smoke` and `coverage-combined` jobs in
`.github/workflows/ci.yml`) — run them locally before pushing changes that
could affect them.

| Gate | Make target | What it does |
|---|---|---|
| Lock | `uv lock --check` | `uv.lock` matches `pyproject.toml` |
| Lint | `uv run ruff check .` | Whole-tree ruff lint |
| Format | `uv run ruff format --check .` | Whole-tree ruff format, including Python blocks in Markdown |
| Types | `uv run mypy --strict src/` | Strict type check |
| Unit tests | `make test` | Hermetic pytest (`-m "not integration and not slow"`) |
| Integration tests | `make up && make test-int && make down` | Real PostgreSQL and Redis behavior |
| Coverage report | `make test-cov` | pytest + coverage HTML report |
| Security scan | `make sec` | bandit SAST, pip-audit CVE check, secrets scan, and license policy |
| License policy | `make license-check` | Runtime dependency license inventory |
| Docs links | `make docs-check` | Every repository-local Markdown link and anchor |
| Pi extensions | `make pi-extensions-check` | Committed `.js` matches the recorded TypeScript compiler output (Node 22.22.1 or later) |
| Semgrep | `make sec-semgrep` | Registry rules plus repository-local policy |
| Security unit tests | `make sec-test` | pytest `-m "security and not fuzz"` |
| API fuzzing | `make sec-fuzz` | schemathesis `-m fuzz` |
| Mutation testing (scheduled CI only) | `make mutation-gate` | mutmut run + export-cicd-stats + >=85% kill floor |
| Benchmark smoke | `make load-smoke` | pytest `-m slow` (locustfile import check) |
| Combined coverage (scheduled CI only) | fast + integration `coverage run` | ratchet `fail_under=77` (see `docs/sdlc/17-testing-strategy.md` §6) |
| Pattern scrub | `uv run python tools/guards/repo_text_policy.py <files>` | banned literals, real RunPod IDs/hosts |
| OpenAPI compatibility | `make openapi-check` | no incompatible public API change |
| Workflow policy | `make ci-tools` | workflow syntax, trust, release, and DCO policy |
| Agent Routing | `uv run pytest tests/agents -q -p no:randomly -p no:cov \| tail -40`, plus the `tools/agents/` checks in [docs/agents/CONTRIBUTING.md](docs/agents/CONTRIBUTING.md) | runtime, plugin, registry, and source contracts |

The table above is the local pre-push set; `docs/sdlc/17-testing-strategy.md`
§7 is the authoritative CI matrix (adds the property, chaos, and
release-readiness tiers).

## Definition of Done

Three non-negotiable standards apply to **every** change and are enforced at PR
review. They are the bar this project was built to — do not regress them.

### 1. Documentation parity (SDLC docs)

The `docs/sdlc/` chapters are the source-grounded technical map of Pitwall and
**must not drift**. Any PR that changes behavior updates the relevant chapter(s)
in the **same PR** as the code:

- Changed subsystem behavior → update its chapter. A genuinely new subsystem
  gets a new numbered chapter plus an index row in `docs/sdlc/README.md`.
- Cite real source as `path:line` and verify each citation against the code you
  are shipping. A doc that disagrees with the code is a defect, not a nit.
- Keep the README and the `00-overview` / `14-security` trust-model claims true.
- The `repo_text_policy` pattern gate stays green on changed files.

A code change with no corresponding doc update is **incomplete** and should not
be approved.

### 2. Testing parity (the comprehensive program)

`docs/sdlc/17-testing-strategy.md` defines the authoritative verification program;
every change lives up to it as applicable to the surface it touches:

- **Hermetic** unit/contract tests for new logic (default lane, no infra).
- **Property** tests (Hypothesis) for pure-logic invariants.
- **Integration** tests for any Postgres/Redis path.
- **Chaos** tests for new failure / timeout / partial-outage paths.
- **Security** tests for any auth / SSRF / secret / HMAC surface.
- **Mutation** kill-floor (≥85%) holds for the core pure-logic trio.
- **Find → fix:** every production bug ships with a hermetic regression test.
- The **combined fast+integration coverage** ratchet (`fail_under=77`) holds —
  raise it as coverage grows, never lower it.

### 3. QA packet parity (`qa/`)

[`qa/`](qa/README.md) is the agent-guided tester program. Its lessons link to the commands,
routes, screens, and doc headings they exercise. A change that renames, removes, or changes the
behavior of anything a lesson uses updates that lesson in the same PR:

- `make docs-check` fails when a heading a lesson links to is renamed or removed.
- Review catches changed commands, flags, output, and screens.
- A new user-facing surface adds its standing mission or concept card in the PR that adds it, and
  the PR's `## QA notes` drive the tester's acceptance run.

## PR Process

1. **Branch** — create a feature or fix branch:
   ```bash
   git checkout -b feat/your-feature-name
   # or
   git checkout -b fix/short-description
   ```

2. **Keep gates green** — run `make test` and `make sec` locally before pushing.
   CI runs the full suite; do not merge with failing gates.

3. **CI checks** — the `CI` workflow's required job set is lint, format, strict types, docs
   links, container build, Compose config, SAST, secret/license policy, API fuzzing, gateway
   catalog drift, hermetic tests, Pi extensions, macOS agent tests, and real-infrastructure
   integration; combined coverage and mutation testing run on the weekly schedule.
   The public-alpha release workflow additionally requires the full mutation gate, artifact
   checks, strict audit, release envelopes, and live acceptance. Repository settings define the
   exact protected check names.

   Agent Routing changes run in the same required checks: `tests/agents/` and the `tools/agents/`
   validators are part of `CI`, and root Markdown, text, secret, DCO, and release-policy gates cover
   `docs/agents/` and `plugins/`. Agent Routing shares the one `pyproject.toml` and `uv.lock`; `pitwall.agents` never imports
   broker services, repositories, or `pitwall.db`.

4. **Commit messages** — use clear, concise subject lines. Conventional-ish format
   is recommended:
   ```
   feat: add cost estimation for GPU leasing
   fix: handle Redis connection pool exhaustion
   chore: update pip-audit baseline
   ```

5. **DCO sign-off.** Every commit must be signed off. Add `-s` to the commit command:
   ```bash
   git commit -s -m "fix: handle nil pointer in lease state"
   ```
   The `Signed-off-by:` line certifies the [Developer Certificate of Origin 1.1](https://developercertificate.org/)
   for that commit. A sign-off is a statement, not a cryptographic signature; GPG or SSH commit
   signing is optional.

## Testing help

Pitwall has an agent-guided QA program in [`qa/`](qa/README.md). A tester opens an AI coding
agent at the repository root and types `Read qa/START-HERE.md and follow it.` To request acceptance
testing on a pull request, fill in its `## QA notes` section and add the `needs-qa` label.

## Code of Conduct

Pitwall follows the [Contributor Covenant](https://www.contributor-covenant.org/).
Please be respectful and constructive in all interactions.
