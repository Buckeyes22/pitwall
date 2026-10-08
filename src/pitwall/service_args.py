"""Console-script entry points for the long-running services, and their shared arguments.

The services are configured by environment variables and ``pitwall.toml``; their command
line only answers ``--help`` and refuses anything it does not know. The console scripts
point here, not at each service's ``__main__``, because several service packages refuse
to import without their runtime settings: parsing first lets ``--help`` answer on an
unconfigured host, and an unknown argument is refused before any service code loads.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence

_EPILOG = (
    "Configured by environment variables and pitwall.toml; "
    "run `pitwall config check` to validate the configuration."
)

# prog -> (entry-point module, description)
SERVICES: dict[str, tuple[str, str]] = {
    "pitwall-api": (
        "pitwall.api.__main__",
        "Serve the Pitwall REST API on PITWALL_API_HOST:PITWALL_API_PORT.",
    ),
    "pitwall-reconciler": (
        "pitwall.reconciler.__main__",
        "Run the Pitwall reconciler worker, or check its Redis configuration.",
    ),
    "pitwall-webhook": (
        "pitwall.webhook_receiver.__main__",
        "Receive RunPod webhooks on PITWALL_WEBHOOK_HOST:PITWALL_WEBHOOK_RECEIVER_PORT.",
    ),
    "pitwall-cost-exporter": (
        "pitwall.cost.__main__",
        "Serve Pitwall cost metrics for Prometheus on "
        "PITWALL_COST_EXPORTER_HOST:PITWALL_COST_EXPORTER_PORT.",
    ),
}


def parse_service_args(prog: str, argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse a service's arguments; ``argv=None`` reads the process's own arguments."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=SERVICES[prog][1],
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    if prog == "pitwall-reconciler":
        parser.add_argument(
            "command",
            nargs="?",
            choices=("check",),
            help="validate the Redis configuration and exit",
        )
    return parser.parse_args(list(sys.argv[1:] if argv is None else argv))


def _run(prog: str, argv: Sequence[str] | None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parse_service_args(prog, arguments)
    importlib.import_module(SERVICES[prog][0]).main(arguments)


def api(argv: Sequence[str] | None = None) -> None:
    _run("pitwall-api", argv)


def reconciler(argv: Sequence[str] | None = None) -> None:
    _run("pitwall-reconciler", argv)


def webhook(argv: Sequence[str] | None = None) -> None:
    _run("pitwall-webhook", argv)


def cost_exporter(argv: Sequence[str] | None = None) -> None:
    _run("pitwall-cost-exporter", argv)


__all__ = [
    "SERVICES",
    "api",
    "cost_exporter",
    "parse_service_args",
    "reconciler",
    "webhook",
]
