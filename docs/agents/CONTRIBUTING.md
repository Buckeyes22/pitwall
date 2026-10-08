# Contributing to Agent Routing

Issues and pull requests are welcome. Agent Routing is the `pitwall agents` command group of the
one Pitwall project (`src/pitwall/agents/`, `tests/agents/`, `tools/agents/`, `plugins/`). It shares
Pitwall's `pyproject.toml`, `uv.lock`, CI, and [root contribution guide](../../CONTRIBUTING.md); make
changes on a branch of the Pitwall repository and submit them through its reviewed history.

## Local checks

Set up once with the root toolchain, then run the agents checks CI runs:

```bash
uv sync --frozen --extra dev --python 3.14.7
uv run mypy tools/agents
uv run python tools/agents/validate_json_schemas.py
uv run python tools/agents/validate_plugins.py
uv run python tools/agents/validate_registry.py
uv run python tools/agents/check_generated.py
uv run python tools/agents/sync_routes.py --check
uv run python -m compileall -q src/pitwall/agents tests/agents tools/agents
uv run pytest tests/agents -q -p no:randomly -p no:cov | tail -40
```

`tests/agents/` pins the shim contract: a usage error exits with `SHIM-DONE exit=64`, and every
shim, including `route-shim.sh`, is a two-line wrapper around `pitwall agents _shim`. The full CI
matrix lives in `.github/workflows/ci.yml`. Repository-wide Markdown, text-policy, secret, license,
DCO, and security checks also run from the Pitwall root.

Python 3.14 is the runtime floor and the only supported minor. `pitwall agents _shim`,
`_steer-gate`, and the hook entry points must stay lightweight: they must not import `fastapi`,
`uvicorn`, `asyncpg`, `redis`, `arq`, `textual`, `runpod`, `mcp`, or `prometheus_client`, and
`pitwall.agents` never imports broker services, repositories, or `pitwall.db`. It may import Pydantic
models from `pitwall.api`.

## Model facts

Model facts, their sources, and the files generated from them are described in
[model-facts/README.md](model-facts/README.md). Change a model's facts in
`docs/agents/model-facts/<unit>/facts.json`, then run `uv run python tools/agents/sync_model_facts.py`,
`uv run python tools/agents/sync_routes.py`, and `uv run python tools/agents/validate_model_facts.py`.
`tools/agents/check_generated.py` runs all three checks.

## Dependencies

Agent Routing adds no dependency of its own. A dependency change edits the root `pyproject.toml` and
regenerates the root `uv.lock`; a PR is incomplete until `uv lock --check` passes.

Ruff's lint selection is pinned in the root `pyproject.toml` rather than inherited from the tool's
defaults, which widen between minor releases. Adopting additional rules is a deliberate change, not a
side effect of upgrading ruff.

Doctor tests must use temporary homes, fake executables, or subprocess mocks and must prove the
default path performs no authentication or model-discovery probe. Explicit-discovery tests must bound
output and prove raw output and secrets are not retained. Worktree tests must use temporary Git
repositories.

Workflow schema changes must update `src/pitwall/agents/resources/schemas/workflow.schema.json`,
semantic validation in `src/pitwall/agents/workflow.py`, scheduler tests, both workflow examples,
all three host-specific skills, and [workflows.md](workflows.md) together.

## Conventions

The `SHIM-DONE exit=<n>` contract, the opt-in `SHIM-RESULT` receipt, the `PITWALL_AGENTS_*`
environment-variable names, and the namespaced Claude-package agent types (`pitwall:codex-shim`,
`pitwall:kimi-shim`, `pitwall:opencode-shim`, `pitwall:grok-shim`, and so on) are part of the public
contract. There are no aliases for the pre-merge names; `pitwall agents migrate` is the only place
that reads them.

Capability-card rankings and tier lists are seed examples that each user maintains through the
`distill` command. PRs that adjust tiers based only on personal experience belong in your own fork's
ledger, not here.

This project may adopt ideas and behavior observed elsewhere, but do not copy Devchain source or
ELv2-licensed text into the repository. Harness-registry changes must regenerate all host views with
`uv run python tools/agents/sync_routes.py`; update shared runtime, plugin, and documentation
contracts in the same change.
