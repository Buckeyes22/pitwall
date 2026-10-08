"""Top-level Pitwall CLI dispatcher.

``main`` looks the command group up in ``GROUPS`` and imports only that group's module, so
``pitwall agents _shim`` and every other entry point pays for just the code it runs.

Usage::

    pitwall                     Open the console
    pitwall setup|serve|status|stop   Personal single-host verbs
    pitwall doctor              One report: broker, agents, gateway, workbench
    pitwall agents ...          Agent Routing (dispatch, runs, routes, workflow, setup)
    pitwall usage [serve]       Subscription usage meter
    pitwall workbench ...       Pi workbench (launch, doctor, usage, compare, acceptance)
    pitwall mcp serve broker|channel, pitwall mcp install|uninstall|relay
    pitwall db|init|seed|config|models|leases|routing|runpod ...   Operator commands
"""

from __future__ import annotations

import importlib
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import TextIO

# (group, module, function): the module is imported, and the function called with the
# remaining arguments, only when the group is invoked.
GROUPS: tuple[tuple[str, str, str], ...] = (
    ("db", "pitwall.db", "main"),
    ("leases", "pitwall.cli.leases", "cmd_leases"),
    ("register-template", "pitwall.cli.templates", "cmd_register_template"),
    ("init", "pitwall.cli.init", "cmd_init"),
    ("create-capability", "pitwall.cli.capabilities", "cmd_create_capability"),
    ("seed", "pitwall.cli.capabilities", "cmd_seed"),
    ("config", "pitwall.cli.config_check", "cmd_config"),
    ("terminate-pod", "pitwall.cli.pods", "cmd_terminate_pod"),
    ("register-endpoint", "pitwall.cli.endpoints", "cmd_register_endpoint"),
    ("set-provider-health", "pitwall.cli.endpoints", "cmd_set_provider_health"),
    ("serve", "pitwall.cli.serve_model", "cmd_serve"),
    ("status", "pitwall.cli.personal", "cmd_status"),
    ("stop", "pitwall.cli.personal", "cmd_stop"),
    ("setup", "pitwall.cli.personal", "cmd_setup"),
    ("doctor", "pitwall.cli.doctor", "cmd_doctor"),
    ("models", "pitwall.cli.models", "cmd_models"),
    ("warm-volume", "pitwall.cli.warm_volume", "cmd_warm_volume"),
    ("dashboard", "pitwall.cli.dashboard", "cmd_dashboard"),
    ("burn-rate", "pitwall.cli.burn_rate", "cmd_burn_rate"),
    ("guardrails", "pitwall.cli.guardrails", "cmd_guardrails"),
    ("routing", "pitwall.cli.routing", "cmd_routing"),
    ("runpod-onboard", "pitwall.cli.onboarding", "cmd_runpod_onboard"),
    ("cost", "pitwall.cost.cli", "cmd_cost"),
    ("budget", "pitwall.cli.budget", "cmd_budget"),
    ("runpod", "pitwall.cli.runpod_resources", "cmd_runpod_resources"),
    ("provider-ops", "pitwall.cli.provider_ops", "cmd_provider_ops"),
    ("volume-files", "pitwall.cli.volume_files", "cmd_volume_files"),
    ("gateway", "pitwall.cli.gateway", "cmd_gateway"),
    ("quotas", "pitwall.cli.gateway", "cmd_quotas"),
    ("mcp", "pitwall.cli.mcp", "cmd_mcp"),
    ("agents", "pitwall.agents.cli", "main"),
    ("usage", "pitwall.cli.usage", "cmd_usage"),
    ("workbench", "pitwall.cli.workbench", "cmd_workbench"),
    ("retention", "pitwall.retention.__main__", "main"),
)
_DEFAULT_GROUP = ("dashboard", "pitwall.cli.dashboard", "cmd_dashboard")


def _run_group(module: str, function: str, argv: list[str]) -> int:
    handler = getattr(importlib.import_module(module), function)
    return int(handler(argv))


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        return _run_group(_DEFAULT_GROUP[1], _DEFAULT_GROUP[2], [])

    if args in (["-h"], ["--help"], ["help"]):
        _usage(stream=sys.stdout)
        return 0

    if args in (["-V"], ["--version"]):
        print(_installed_version())
        return 0

    group, rest = args[0], args[1:]
    for name, module, function in GROUPS:
        if name == group:
            return _run_group(module, function, rest)

    print(f"Unknown command group: {group}", file=sys.stderr)
    _usage(stream=sys.stderr)
    return 1


def _installed_version() -> str:
    """Return the installed distribution version with a source-tree fallback."""
    try:
        return version("pitwall")
    except PackageNotFoundError:
        from pitwall import __version__

        return __version__


def _usage(*, stream: TextIO) -> None:
    print(
        "Usage: pitwall {setup|serve|status|stop|doctor|models|db|leases|mcp|agents|usage|retention|init|create-capability|seed|config|register-template|register-endpoint|set-provider-health|terminate-pod|warm-volume|cost|burn-rate|guardrails|routing|runpod|runpod-onboard|provider-ops|volume-files|gateway|quotas|workbench|dashboard} <command>",
        file=stream,
    )
    print("  pitwall                                Open the console", file=stream)
    print(
        "  pitwall setup                          First-run setup (credential, endpoint key, plugin)",
        file=stream,
    )
    print(
        "  pitwall serve                          Serve a catalogue model on a pod "
        "(OpenAI-compatible)",
        file=stream,
    )
    print(
        "  pitwall status                         Show what is running and when it ends",
        file=stream,
    )
    print(
        "  pitwall stop <route>                   Terminate a pod and remove its route", file=stream
    )
    print("  pitwall --help              Show this help and exit", file=stream)
    print("  pitwall --version           Show the installed version and exit", file=stream)
    print("  pitwall db migrate          Apply pending migrations", file=stream)
    print("  pitwall db reset            Drop the pitwall schema", file=stream)
    print("  pitwall db status           Show migration status", file=stream)
    print("  pitwall leases list         List active pod leases", file=stream)
    print(
        "  pitwall mcp serve broker    Start the broker MCP server over stdio",
        file=stream,
    )
    print(
        "  pitwall mcp serve channel   Start the orchestrator channel MCP server over stdio",
        file=stream,
    )
    print(
        "  pitwall agents ...          Agent Routing: dispatch to external agent harnesses",
        file=stream,
    )
    print(
        "  pitwall usage               Show subscription usage; `usage serve` publishes it",
        file=stream,
    )
    print(
        "  pitwall doctor              Check install, config, services, and spend controls",
        file=stream,
    )
    print(
        "  pitwall mcp install         Register the broker and channel MCP servers with Claude Code, Codex, or OpenCode",
        file=stream,
    )
    print(
        "  pitwall mcp uninstall       Remove those registrations",
        file=stream,
    )
    print(
        "  pitwall retention run       Encrypted bounded archive/purge",
        file=stream,
    )
    print(
        "  pitwall volume-files        Bounded volume transfers and pod logs",
        file=stream,
    )
    print(
        "  pitwall gateway              Sync, inspect, and doctor the free-tier gateway",
        file=stream,
    )
    print(
        "  pitwall quotas               Show the free-pool quota burn-down",
        file=stream,
    )
    print(
        "  pitwall workbench            Launch Pi under a workbench profile; compare, accept, doctor",
        file=stream,
    )
    print(
        "  pitwall runpod              RunPod account resource controls",
        file=stream,
    )
    print(
        "  pitwall runpod-onboard      Plan/apply resumable RunPod onboarding",
        file=stream,
    )
    print(
        "  pitwall routing             Preview and operate routed jobs",
        file=stream,
    )
    print(
        "  pitwall provider-ops        Current provider descriptors and probes",
        file=stream,
    )
    print(
        "  pitwall init                Guided local onboarding",
        file=stream,
    )
    print(
        "  pitwall create-capability   Create or update a capability",
        file=stream,
    )
    print(
        "  pitwall seed                Apply capability/provider seed files",
        file=stream,
    )
    print(
        "  pitwall config check        Validate boot-time configuration",
        file=stream,
    )
    print(
        "  pitwall register-template   Register a RunPod template",
        file=stream,
    )
    print(
        "  pitwall register-endpoint   Register a RunPod endpoint as a provider",
        file=stream,
    )
    print(
        "  pitwall set-provider-health  Mark a provider healthy/unhealthy/hibernated",
        file=stream,
    )
    print(
        "  pitwall terminate-pod        Terminate a RunPod pod by id",
        file=stream,
    )
    print(
        "  pitwall models               List, inspect, and fit catalogue models",
        file=stream,
    )
    print(
        "  pitwall warm-volume          Warm a volume with the catalogue serve launch",
        file=stream,
    )
    print(
        "  pitwall dashboard            Launch the Textual operator console",
        file=stream,
    )
    print(
        "  pitwall cost summary         Show persisted aggregate costs",
        file=stream,
    )
    print(
        "  pitwall cost workloads       Show estimate/ceiling/actual workload costs",
        file=stream,
    )
    print(
        "  pitwall burn-rate            Show the monthly budget burn-rate forecast",
        file=stream,
    )
    print(
        "  pitwall mcp relay -- CMD     Keep MCP clients connected across broker restarts",
        file=stream,
    )
    print(
        "  pitwall budget show|set     Show or change budget limits without a restart",
        file=stream,
    )
    print(
        "  pitwall guardrails status    Show pre-spend guardrail state",
        file=stream,
    )
    print(
        "  pitwall guardrails preview   Preview a JSON payload without persistence",
        file=stream,
    )
