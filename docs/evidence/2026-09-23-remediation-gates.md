# Remediation gates after Phases 1–4 (2026-09-23)

Candidate: local `main` at 1ca690a (plan
[`2026-09-23-review-remediation.md`](../superpowers/plans/2026-09-23-review-remediation.md),
Tasks 1–17). Host: Linux, Python 3.14.7, Node 22.22.1, Docker test stack (Postgres 16, Redis 7).

| Gate | Command | Result |
| --- | --- | --- |
| Lint | `uv run --frozen ruff check .` | All checks passed |
| Format | `uv run --frozen ruff format --check .` | 1261 files already formatted |
| Types | `uv run --frozen mypy src` | no issues in 315 source files |
| Hermetic | `uv run --frozen pytest -q -m "not integration and not slow"` | 6165 passed, 62 skipped |
| Integration | `pytest -q -m integration` with the test stack | 162 passed, 3 skipped |
| Journeys | `scripts/release/run-user-journeys.sh` | 27 passed, 0 failed |
| Agent Routing | `.venv/bin/python -m unittest discover -s tests` | 809 tests OK (4 skipped) |
| Gateway | `npm run typecheck && npm run lint && npx vitest run` | 121 passed |
| Pi Workbench | `npx tsc --noEmit && npx vitest run` | 261 passed, 1 skipped |
| Security | `make sec` | bandit clean; pip-audit no known vulnerabilities; 520 reviewed secret findings; license policy pass |
| Docs | `make docs-check` | 407 files pass |
| OpenAPI | `make openapi-check` | compatibility passed |
| Workflows | `make ci-tools` | 17 passed |
| Text policy | `repo_text_policy.py` over tracked files | pass |
| Images | trivy 0.70.0, HIGH/CRITICAL, ignore-unfixed, vuln+secret+misconfig, five images | 0 findings each |
| External links | `check_markdown_links.py --external` | pass |

`make sec` first failed on 138 unaudited detect-secrets findings added by the integrated
branches; each was audited (98 sha256 fields, 40 variable names, canaries, placeholders, and
the local test DSN) before the baseline was regenerated.
