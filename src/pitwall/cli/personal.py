"""pitwall serve|status|stop: personal-first verbs over the shared engine."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from pitwall.personal.backend import RegistryBackend, backend_status_line, select_backend
from pitwall.personal.service import (
    RunPodCredentialMissing,
    ServeFailed,
    ServeRefused,
    ServeSpec,
    build_personal_service,
)

_build_service = build_personal_service


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _ask_setup(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        _err("setup cancelled: input closed before an answer; rerun `pitwall setup` interactively")
        raise SystemExit(2) from None


def run_personal_setup() -> None:
    from pathlib import Path

    from pitwall.personal.setup import run_setup
    from pitwall.personal.state import default_state_root

    run_setup(
        environ=os.environ,
        home=Path.home(),
        state_root=default_state_root(),
        prompt=_ask_setup,
    )


def cmd_setup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pitwall setup")
    parser.add_argument("--yes", action="store_true", help="answer yes to every prompt")
    args = parser.parse_args(argv)
    if args.yes:
        from pathlib import Path

        from pitwall.personal.setup import run_setup
        from pitwall.personal.state import default_state_root

        run_setup(
            environ=os.environ,
            home=Path.home(),
            state_root=default_state_root(),
            prompt=lambda _question: True,
        )
    else:
        run_personal_setup()
    return 0


def _service_or_exit() -> Any:
    from pitwall.config import get_settings
    from pitwall.personal.keys import resolve_runpod_api_key

    key, source = resolve_runpod_api_key(os.environ)
    if key is None:
        if sys.stdin.isatty():
            run_personal_setup()
            key, source = resolve_runpod_api_key(os.environ)
        if key is None:
            _err("no RunPod credential: run `pitwall setup` or export RUNPOD_API_KEY")
            raise SystemExit(2)
    if source == "runpodctl":
        os.environ["RUNPOD_API_KEY"] = key
    try:
        return _build_service(get_settings())
    except RunPodCredentialMissing as exc:
        _err(f"no RunPod credential: {exc}")
        raise SystemExit(2) from None


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a decimal: {value!r}") from exc


def _invalid_spec_message(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"])
        parts.append(f"{location} {error['msg'].lower()}")
    return "invalid serve arguments: " + "; ".join(parts)


def _serve_spec(args: argparse.Namespace) -> ServeSpec:
    return ServeSpec(
        model=args.model,
        variant=args.variant,
        gpu_class=args.gpu_class,
        gpu_count=args.gpu_count,
        cloud=args.cloud,
        ttl_minutes=args.ttl_minutes,
        max_usd_per_hour=args.max_usd_per_hour,
        rate_per_second=args.rate_per_second,
        route=args.route,
    )


def cmd_serve(argv: list[str]) -> int:
    if select_backend() == "registry":
        return RegistryBackend().serve(argv)
    parser = argparse.ArgumentParser(
        prog="pitwall serve",
        description="Serve a catalogue model on a pod with a self-termination safeguard.",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--variant")
    parser.add_argument("--gpu-class", required=True)
    parser.add_argument("--gpu-count", type=int, default=1)
    parser.add_argument("--cloud", choices=["secure", "community"], default="community")
    parser.add_argument("--ttl-minutes", "--ttl", type=int, default=60)
    parser.add_argument("--max-usd-per-hour", type=_decimal)
    parser.add_argument("--rate-per-second", type=_decimal)
    parser.add_argument("--route", required=True)
    parser.add_argument(
        "--gateway",
        action="store_true",
        help="Start the loopback gateway sidecar before serving (PITWALL_GATEWAY_TOKEN required).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.max_usd_per_hour is None:
        _err("--max-usd-per-hour is required: it caps what one hour of this pod may cost")
        return 2
    try:
        spec = _serve_spec(args)
    except ValidationError as exc:
        _err(_invalid_spec_message(exc))
        return 2
    from pitwall.personal.gateway import GatewaySupervisor
    from pitwall.personal.state import StateStore

    service = _service_or_exit()
    supervisor: GatewaySupervisor | None = None
    if args.gateway:
        supervisor = GatewaySupervisor(StateStore(), env=os.environ)
    if supervisor is not None:
        service.with_gateway(supervisor)
    try:
        lease = asyncio.run(service.serve(spec, progress=None if args.json else _err))
    except ServeRefused as exc:
        _err(f"refused: {exc.code} {exc.detail}".rstrip())
        return 2 if exc.code == "gateway_start_failed" else 1
    except ServeFailed as exc:
        _err(f"failed after launch: {exc.code}; termination attempted for pod {exc.pod_id}")
        if exc.log_tail:
            _err("pod log tail:\n" + exc.log_tail)
        return 3
    except KeyboardInterrupt:
        _err("interrupted; cleanup was attempted for any pod created")
        return 130
    data = lease.model_dump(mode="json")
    data["try"] = f"route-shim.sh {lease.route} prompt.md"
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"route {lease.route} is ready: {lease.served_model_id} at {lease.endpoint_url}")
        print(f"deadline {lease.deadline_at.isoformat()} (self-termination scheduled)")
        print(f"try it: {data['try']}")
    return 0


def _registry_status(json_output: bool) -> int:
    """Registry status, with the backend named: a line for humans, a key for ``--json``."""
    if not json_output:
        print(backend_status_line("registry"))
        return RegistryBackend().status(json_output=False)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = RegistryBackend().status(json_output=True)
    text = captured.getvalue().strip()
    body: Any = json.loads(text) if text else {}
    document = (
        {"backend": "registry", **body}
        if isinstance(body, dict)
        else {
            "backend": "registry",
            "leases": body,
        }
    )
    print(json.dumps(document, indent=2))
    return code


def cmd_status(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pitwall status")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    backend = select_backend()
    if backend == "registry":
        return _registry_status(args.json)
    service = _service_or_exit()
    leases = asyncio.run(service.status())
    if args.json:
        payload = {
            "backend": backend,
            "leases": [lease.model_dump(mode="json") for lease in leases],
        }
        print(json.dumps(payload, indent=2))
        return 0
    print(backend_status_line(backend))
    if not leases:
        print("nothing running")
        return 0
    for lease in leases:
        print(
            f"{lease.route:<20} {lease.state:<10} {lease.served_model_id:<28} "
            f"{lease.gpu_class:<28} ends {lease.deadline_at.isoformat()}"
        )
    from pitwall.personal.keys import ENDPOINT_KEY_ENV

    if not os.environ.get(ENDPOINT_KEY_ENV):
        print(
            f"note: {ENDPOINT_KEY_ENV} is not set in this shell; routes will not "
            "authenticate until it is"
        )
    return 0


def cmd_stop(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pitwall stop")
    parser.add_argument("route", nargs="?")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if select_backend() == "registry":
        if args.route is None:
            _err("pitwall stop <lease-id> on the registry backend")
            return 2
        return RegistryBackend().stop(args.route, json_output=args.json)
    if args.route is None and not args.all:
        _err("pitwall stop <route> or pitwall stop --all")
        return 2
    service = _service_or_exit()

    async def _run() -> list[dict[str, object]]:
        targets = (
            [args.route]
            if args.route
            else [
                lease.route
                for lease in await service.status()
                if lease.state in {"launching", "ready"}
            ]
        )
        return [(await service.stop(route)).model_dump(mode="json") for route in targets]

    try:
        stopped = asyncio.run(_run())
    except KeyError as exc:
        _err(f"refused: unknown_route {exc.args[0]}")
        return 1
    gateway_stopped = False
    if args.all:
        from pitwall.personal.gateway import GatewaySupervisor
        from pitwall.personal.state import StateStore

        try:
            GatewaySupervisor(StateStore(), env=os.environ).stop()
            gateway_stopped = True
        except (
            Exception
        ):  # reason: a gateway that will not stop is reported as not stopped, never raised
            gateway_stopped = False
    body: dict[str, object] = {"stopped": stopped}
    if args.all:
        body["gateway_stopped"] = gateway_stopped
    if args.json:
        print(json.dumps(body, indent=2))
    else:
        lines = [f"stopped {item['route']}" for item in stopped]
        if args.all and gateway_stopped:
            lines.append("stopped gateway")
        print("\n".join(lines) or "nothing to stop")
    return 0
