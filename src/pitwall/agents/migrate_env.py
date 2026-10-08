"""Legacy environment-variable names and the startup guard that refuses them.

Agent Routing's variables were renamed to ``PITWALL_AGENTS_*`` with no aliases. A leftover legacy
value (for example ``SUBAGENT_MODEL_ROUTING_UNRESTRICTED=0``) would otherwise be silently ignored
and the default applied, so every ``pitwall agents`` entry point refuses to run while one is set.

``PITWALL_API_TOKEN`` is deliberately absent: it is the broker's own API token variable. Only the
agents-side alias for it was removed; the broker client reads ``PITWALL_AGENTS_API_TOKEN``.
"""

from __future__ import annotations

from collections.abc import Mapping

# Where the standalone Agent Routing kept things before the merge. Only `pitwall agents migrate`
# reads these; nothing else may build or resolve a path from them.
LEGACY_PATHS: tuple[str, ...] = (
    "~/.config/subagent-model-routing",
    "~/.local/state/subagent-model-routing",
    "~/.claude/subagent-model-routing",
    "~/.local/share/subagent-model-routing",
)

# Dispatch worktree branches were `<prefix><dispatch_id>`; only `pitwall agents migrate` renames them.
LEGACY_BRANCH_PREFIX = "model-routing/"
#: Every branch prefix an earlier release gave a dispatch worktree, oldest first.
LEGACY_BRANCH_PREFIXES = (LEGACY_BRANCH_PREFIX, "pitwall-agent-routing/")

NEW_PREFIX = "PITWALL_AGENTS_"
LEGACY_PREFIXES = ("SUBAGENT_MODEL_ROUTING_", "PITWALL_AGENT_ROUTING_")

# Legacy names whose replacement is not a plain prefix swap.
LEGACY_ENV_RENAMES = {
    "SHIM_TIMEOUT_SECS": "PITWALL_AGENTS_TIMEOUT_SECS",
    "SUBAGENT_MODEL_ROUTING_ROUTES": "PITWALL_AGENTS_PROFILES",
    "SUBAGENT_MODEL_ROUTING_PROVIDER": "PITWALL_AGENTS_HARNESS",
}

# The Pi workbench variables were renamed from ``PI_WORKBENCH_*`` to ``PITWALL_WORKBENCH_*`` with no
# aliases. These are every name the shipped workbench read.
_LEGACY_WORKBENCH_SUFFIXES = (
    "ACCOUNTING_PATH",
    "ACCOUNT_BUDGET_DIR",
    "AGENT_DIR",
    "APPROVED_CHECKS",
    "CANDIDATE_B_EXTENSION",
    "HOSTED_MAX_COMPLETION_TOKENS",
    "NATIVE_PROFILE",
    "PI_BIN",
    "PROVIDER_PROFILE",
    "RESOURCE_DIR",
    "RESTRICTED",
    "RESTRICTED_CREDENTIAL_ENV",
    "RESTRICTED_RUNTIME_ROOT",
    "RUNTIME_DIR",
    "TINTIN_EXTENSION",
)
LEGACY_ENV_RENAMES.update(
    {
        f"PI_WORKBENCH_{suffix}": f"PITWALL_WORKBENCH_{suffix}"
        for suffix in _LEGACY_WORKBENCH_SUFFIXES
    }
)

# Every legacy variable the shipped code ever read or exported, with its replacement.
_LEGACY_SUFFIXES = (
    "ABORT_GRACE_SECS",
    "ASK_SUPPORT",
    "ATTEMPT",
    "CHANNEL_ATTEMPT",
    "CHANNEL_DISPATCH_ID",
    "CHANNEL_STATE_ROOT",
    "DISPATCH_ID",
    "EFFORT",
    "EVENT",
    "HOME",
    "HOOK_DEPTH",
    "INSTALL_DIR",
    "LEDGER",
    "MANAGED_EXPECTED_HARNESS",
    "MAX_ASKS",
    "MODEL",
    "REF",
    "REPO_URL",
    "RESUME",
    "ROUTING_MARKER_DIR",
    "SCRIPTS_DIR",
    "STATE_HOME",
    "TASK_ID",
    "UNRESTRICTED",
    "WORKFLOW_ID",
)
_PITWALL_AGENT_ROUTING_SUFFIXES = (
    "API_TOKEN",
    "BIN_DIR",
    "HOME",
    "LAUNCHER",
    "REF",
    "RELEASE_ENABLED",
    "REPO_URL",
    "SCRIPTS_DIR",
    "SOURCE_ROOT",
    "SUBSCRIPTION_TOKEN",
    "USAGE_LIVE",
    "USAGE_TOKEN",
    "WEBHOOK_SECRET",
)


def replacement_for(name: str) -> str | None:
    """Return the replacement for a legacy variable name, or None when ``name`` is not legacy."""
    if name in LEGACY_ENV_RENAMES:
        return LEGACY_ENV_RENAMES[name]
    for prefix in LEGACY_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            return NEW_PREFIX + name[len(prefix) :]
    return None


LEGACY_ENV_REPLACEMENTS: dict[str, str] = {
    name: replacement
    for name in (
        "SHIM_TIMEOUT_SECS",
        *(f"{LEGACY_PREFIXES[0]}{suffix}" for suffix in (*_LEGACY_SUFFIXES, "PROVIDER", "ROUTES")),
        *(f"{LEGACY_PREFIXES[1]}{suffix}" for suffix in _PITWALL_AGENT_ROUTING_SUFFIXES),
    )
    if (replacement := replacement_for(name)) is not None
}


def legacy_env_conflicts(env: Mapping[str, str]) -> dict[str, str]:
    """Map every legacy variable set in ``env`` to its replacement, in sorted order."""
    return {
        name: replacement
        for name in sorted(env)
        if (replacement := replacement_for(name)) is not None
    }


def legacy_env_refusal(env: Mapping[str, str]) -> str | None:
    """Return the refusal message when ``env`` sets a legacy variable, else None."""
    conflicts = legacy_env_conflicts(env)
    if not conflicts:
        return None
    lines = ["pitwall agents: refusing to run; legacy environment variables are set (rename each):"]
    lines.extend(f"  {name} -> {replacement}" for name, replacement in conflicts.items())
    return "\n".join(lines)
