SHELL := /bin/bash

.PHONY: pi-extensions-check test test-fast test-int test-db test-cov up down sec sec-baseline sec-semgrep sec-test sec-fuzz license-check openapi-check docs-check ci-tools mutation mutation-gate bench load load-smoke engine-smoke engine-smoke-declared
test: test-fast
up:        ; docker compose -f docker-compose.testinfra.yml up -d
down:      ; docker compose -f docker-compose.testinfra.yml down
test-fast: ; uv run pytest -q -n auto -m "not integration and not slow"
test-int:  ; PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test PITWALL_TEST_REDIS_URL=redis://127.0.0.1:6380/0 uv run pytest -q -m integration
test-db:   test-int
test-cov:  ; uv run pytest -q -n auto -m "not integration and not slow" --cov=src/pitwall --cov-report=term-missing --cov-report=html
sec: ## SAST + dependency CVE + secrets scan (no network for bandit)
	uv run bandit -c pyproject.toml -r src/pitwall -ll -ii \
		-b tools/security/bandit-baseline.json
	uv run pip-audit -r <(uv export --frozen --format requirements-txt --no-hashes)
	uv run python tools/security/check_secrets.py
	uv run python tools/security/check_licenses.py
sec-baseline: ## Regenerate the bandit baseline (run only after triaging each new finding)
	uv run bandit -c pyproject.toml -r src/pitwall -ll -ii \
		-f json -o tools/security/bandit-baseline.json || test $$? -eq 1
SEMGREP_VERSION ?= 1.165.0
SEMGREP_JOBS ?= 1  # >1 can hit "io_uring_queue_init: Cannot allocate memory" on constrained hosts
sec-semgrep: ## Local Semgrep Python + security audit rules via uvx (same pin as CI; downloads registry rules; metrics disabled)
	SEMGREP_SEND_METRICS=off uvx --from semgrep==$(SEMGREP_VERSION) semgrep scan --error --jobs $(SEMGREP_JOBS) \
		--config p/python --config p/security-audit \
		--exclude-rule python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5 \
		--config tools/security/semgrep.yml src/pitwall
ci-tools: ## Validate GitHub workflow syntax, trust policy, and local release policy
	actionlint
	uv run python tools/ci/check_workflows.py
	uv run pytest -q -m release tests/release/test_release_policy.py tests/release/test_dco_policy.py
openapi-check: ## Export and compare the current API with the committed baseline
	uv run python tools/ci/export_openapi.py --output /tmp/pitwall-openapi.json
	uv run python tools/ci/check_openapi_compat.py docs/api/openapi-baseline.json /tmp/pitwall-openapi.json
docs-check: ## Validate all repository-local Markdown links and anchors
	uv run python tools/ci/check_markdown_links.py
license-check: ## Inventory every install profile (base, extras, dev, Pi npm) and enforce license policy
	uv run python tools/security/check_licenses.py
	uv run python tools/security/check_licenses.py --extra storage --extra email --extra tracing
	uv run python tools/security/check_licenses.py --extra dev
	uv run python tools/security/check_licenses.py --npm-lock tools/pi-deps/package-lock.json
sec-test: ## Security-marked tests: admin auth, webhook HMAC, SSRF, and non-disclosure
	uv run pytest -q -m "security and not fuzz" tests/security
sec-fuzz: ## Schemathesis fuzz of every public operation and every authenticated admin operation — no unexpected server errors
	uv run pytest -q -m fuzz tests/security
mutation: ## Run mutmut over the release program core trio (cost/rate-limit/lease-state)
	uv run mutmut run
mutation-gate: ## Run mutmut + enforce the >=85% kill floor on covered mutants
	uv run mutmut run
	uv run mutmut export-cicd-stats
	uv run python scripts/mutmut_score_gate.py --floor 85
bench: ## pytest-benchmark micro-benchmarks (release program perf)
	uv run pytest -q -m benchmark --benchmark-only tests/perf
load: ## Locust load profile vs a running, seeded Pitwall (PITWALL_HOST default :8080; PITWALL_LOAD_CAPABILITY default embedding.demo; dry_run writes)
	uv run locust -f tests/load/locustfile.py --headless \
		-u $${LOCUST_USERS:-50} -r $${LOCUST_SPAWN:-5} --run-time $${LOCUST_TIME:-2m} \
		--host $${PITWALL_HOST:-http://127.0.0.1:8080}
load-smoke: ## Hermetic smoke: the locustfile imports + tasks are well-formed
	uv run pytest -q -m slow tests/load
engine-smoke: ## Local dev gate: parser-check cached substitutes and run the pinned llama.cpp CPU HTTP cycle
	uv run --frozen python tools/engines/smoke_launch_shape.py --allow-image-substitution --report artifacts/engine-smoke/report.md
engine-smoke-declared: ## Certification gate: pull and parser-check every declared image, then run the pinned llama.cpp CPU HTTP cycle
	uv run --frozen python tools/engines/smoke_launch_shape.py --pull-declared --report artifacts/engine-smoke/report.md
pi-extensions-check: ## Recompile the Pi extension sources with the recorded TypeScript version; fail if the committed .js differs
	@set -eu; d=src/pitwall/workbench/pi_extensions; t=$$(mktemp -d); trap 'rm -rf "$$t"' EXIT; \
	npx --yes -p "typescript@$$(cat $$d/COMPILER)" tsc -p $$d/tsconfig.json --outDir "$$t"; \
	for f in "$$t"/*.js; do n=$$(basename "$$f"); cmp -s "$$f" "$$d/$$n" || { echo "pi-extensions-check: $$n differs from its compiled source"; exit 1; }; done; \
	for f in $$d/*.js; do n=$$(basename "$$f"); [ -e "$$t/$$n" ] || { echo "pi-extensions-check: $$n has no TypeScript source"; exit 1; }; done; \
	echo "pi-extensions-check: committed .js matches the compiler output"
