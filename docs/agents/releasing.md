# Agent Routing releases

Agent Routing has no release process of its own. It ships in the one `pitwall` wheel and version, with
the plugin manifests under `plugins/` at the same version, so every release goes through the root
[RELEASING.md](../../RELEASING.md).

- The version source of truth is the root `pyproject.toml`; the plugin manifests, the marketplace
  manifests, and `pitwall.__version__` must match it exactly, and `tests/test_version.py` enforces that.
- Tags are `v*` only. The earlier `agent-routing/v*` tags are historical snapshots of the standalone
  component; nothing publishes from them.
- History before the merge is in the root [CHANGELOG.md](../../CHANGELOG.md) under "History before
  unification". The [`releases/`](releases/) notes here are the standalone release notes, kept as records.
- After a release, `uv tool upgrade pitwall` followed by `pitwall agents install` refreshes the shims
  and plugin registrations on a workstation; see [Update](routing-readme.md#update).
