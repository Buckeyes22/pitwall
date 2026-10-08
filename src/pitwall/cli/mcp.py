"""``pitwall mcp``: serve the broker or channel MCP server, register them, or relay stdio."""

from __future__ import annotations

import argparse
import json
import os
import sys

from pitwall.cli.output import add_json_argument
from pitwall.cli.output import json_mode as _json_mode


def _parse_mcp_args(argv: list[str]) -> argparse.Namespace:
    from pitwall.mcp_install import HARNESSES

    parser = argparse.ArgumentParser(
        prog="pitwall mcp",
        description="Serve, register, or relay the Pitwall MCP servers.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser(
        "serve",
        help="Start an MCP server over local stdio: broker or channel.",
        description="Start a Pitwall MCP server over local stdio.",
    )
    servers = serve.add_subparsers(dest="server", required=True)
    broker = servers.add_parser(
        "broker",
        help="Start the broker MCP server (tools over the Pitwall services).",
        description="Start the Pitwall broker MCP server.",
    )
    broker.add_argument(
        "--transport",
        choices=["stdio"],
        default=os.environ.get("PITWALL_MCP_TRANSPORT", "stdio"),
        help="MCP transport type (public alpha: stdio only)",
    )
    add_json_argument(broker)
    servers.add_parser(
        "channel",
        help="Start the orchestrator channel MCP server (needs no broker configuration).",
        description="Start the Pitwall orchestrator channel MCP server over stdio.",
    )

    relay = subcommands.add_parser(
        "relay",
        help="Relay stdio to an MCP server command and restart it when it exits.",
        description="Keep a harness connected across broker restarts: pitwall mcp relay -- docker exec -i CONTAINER pitwall mcp serve broker",
    )
    relay.add_argument("server_command", nargs=argparse.REMAINDER, help="Server command after --")

    # Arguments are spelled out per parser (no helper) so the release-acceptance
    # CLI inventory resolves every option statically.
    install = subcommands.add_parser(
        "install",
        help="Register the Pitwall MCP server with a coding-agent harness.",
        description="Register the Pitwall MCP server with Claude Code, Codex, and/or OpenCode.",
    )
    install.add_argument(
        "harnesses",
        nargs="*",
        choices=HARNESSES,
        metavar="HARNESS",
        help=f"Harness(es); one or more of {', '.join(HARNESSES)} (default: autodetect)",
    )
    install.add_argument(
        "--scope", choices=("user", "project"), default="user", help="Registration scope"
    )
    install.add_argument(
        "--project-root", default=".", help="Project root for project-scope registration"
    )
    install.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing it"
    )
    install.add_argument("--force", action="store_true", help="Replace a foreign 'pitwall' entry")
    add_json_argument(install)
    uninstall = subcommands.add_parser(
        "uninstall",
        help="Remove the Pitwall MCP server registration from a coding-agent harness.",
        description="Remove the Pitwall MCP server registration from Claude Code, Codex, and/or OpenCode.",
    )
    uninstall.add_argument(
        "harnesses",
        nargs="*",
        choices=HARNESSES,
        metavar="HARNESS",
        help=f"Harness(es); one or more of {', '.join(HARNESSES)} (default: autodetect)",
    )
    uninstall.add_argument(
        "--scope", choices=("user", "project"), default="user", help="Registration scope"
    )
    uninstall.add_argument(
        "--project-root", default=".", help="Project root for project-scope registration"
    )
    uninstall.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing it"
    )
    uninstall.add_argument("--force", action="store_true", help="Replace a foreign 'pitwall' entry")
    add_json_argument(uninstall)

    args = parser.parse_args(argv)
    if args.command == "serve" and args.server == "broker" and args.transport != "stdio":
        broker.error("network MCP transports are unavailable in the public alpha")
    return args


def cmd_mcp(argv: list[str]) -> int:
    args = _parse_mcp_args(argv)
    if args.command == "relay":
        from pitwall.mcp.relay import run_relay

        return run_relay(args.server_command)
    if args.command in {"install", "uninstall"}:
        from pitwall.cli.mcp_install import cmd_mcp_install

        return cmd_mcp_install(args)
    if args.server == "channel":
        return _serve_channel()

    from pitwall.mcp import ensure_runtime_env, mcp

    transport = args.transport
    ensure_runtime_env()
    if _json_mode(args):
        # stdout belongs to the MCP stdio transport (2026-07-28 stdio binding: nothing else
        # may be written there), so the machine-readable start record goes to stderr.
        print(json.dumps({"transport": transport}, indent=2), file=sys.stderr, flush=True)
    mcp.run(transport=transport)
    return 0


def _serve_channel() -> int:
    """Serve the orchestrator channel; it reads no broker configuration."""
    from pitwall.agents.mcp_server import main as channel_main

    return channel_main(os.environ)
