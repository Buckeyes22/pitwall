#!/usr/bin/env python3
"""Find locally hosted LLM servers and print how to attach them as routes.

Scans a host for OpenAI-compatible inference servers, identifies which product
is answering, reports whether it needs a key, lists the models it serves, and
prints the `pitwall agents profiles add` command that would attach it.

    python3 tools/agents/detect_local_endpoints.py
    python3 tools/agents/detect_local_endpoints.py --host 192.0.2.10 --api-key-env MY_KEY
    python3 tools/agents/detect_local_endpoints.py --json

Only the host you name is contacted, and only on the ports listed below (or the
ones you pass with --port). The default host is loopback.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

# Ports that local inference servers commonly listen on. Each entry is a hint
# only -- identification comes from the responses, never from the port.
KNOWN_PORTS: dict[int, str] = {
    1234: "LM Studio",
    3000: "generic",
    4891: "GPT4All",
    5000: "text-generation-webui",
    5001: "text-generation-webui",
    7860: "text-generation-webui",
    8000: "vLLM",
    8080: "llama.cpp / llama-swap",
    8081: "generic",
    9292: "llama-swap",
    11434: "Ollama",
    30000: "SGLang",
}

TIMEOUT = 3.0


@dataclass
class Endpoint:
    url: str
    port: int
    kind: str = "unknown OpenAI-compatible"
    auth: str = "unknown"  # "none" | "required" | "unknown"
    models: list[str] = field(default_factory=list)
    readiness: str | None = None  # richer readiness endpoint, if the server has one
    notes: list[str] = field(default_factory=list)


def _get(url: str, key: str | None = None) -> tuple[int, Any]:
    """GET a URL. Returns (status, parsed-json-or-text). Status 0 means no answer."""
    req = urllib.request.Request(url)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(65536).decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read(8192).decode("utf-8", "replace") if exc.fp else ""
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw
    except (
        Exception
    ):  # reason: endpoint probing is best-effort; any failure means nothing was detected
        return 0, None


def _json_probe(url: str, key: str | None, required_key: str) -> bool:
    """True only when the URL answers 200 with a JSON object carrying required_key.

    Status alone is not evidence: a web UI on the same host answers 200 with its
    single-page app for every path, which would otherwise be misread as a match.
    """
    status, body = _get(url, key)
    return status == 200 and isinstance(body, dict) and required_key in body


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def _listening_ports() -> set[int]:
    """Ports listening on this machine, as extra scan candidates."""
    found: set[int] = set()
    try:
        out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True, timeout=5).stdout
    except (
        Exception
    ):  # reason: endpoint probing is best-effort; any failure means nothing was detected
        return found
    for match in re.finditer(r":(\d+)\s", out):
        port = int(match.group(1))
        if 1024 <= port <= 65535:
            found.add(port)
    return found


def _identify(host: str, port: int, key: str | None) -> Endpoint | None:
    """Probe one port and identify what is answering."""
    base = f"http://{host}:{port}"
    ep = Endpoint(url=f"{base}/v1", port=port)

    status, body = _get(f"{base}/v1/models", key)

    # Ollama speaks its own API and only recent versions add /v1/models.
    if status in (0, 404):
        tags_status, tags = _get(f"{base}/api/tags", key)
        if tags_status == 200 and isinstance(tags, dict) and "models" in tags:
            ep.kind = "Ollama"
            ep.auth = "none"
            ep.models = [m.get("name", "?") for m in tags.get("models", [])][:25]
            ep.notes.append("Ollama serves an OpenAI-compatible API at /v1 on the same port.")
            return ep
        return None

    if status == 401 or status == 403:
        ep.auth = "required"
        ep.notes.append(
            "Rejected an unauthenticated request. Re-run with --api-key-env NAME "
            "to enumerate its models."
        )
    elif status == 200:
        if not (isinstance(body, dict) and isinstance(body.get("data"), list)):
            # 200 without a model list is some other web service on this port.
            return None
        ep.auth = "none" if not key else "satisfied by the supplied key"
        if isinstance(body, dict):
            data = body.get("data") or []
            ep.models = [m.get("id", "?") for m in data][:25]
            owners = {m.get("owned_by") for m in data if isinstance(m, dict)}
            if "vllm" in owners:
                ep.kind = "vLLM"
            elif "llama-swap" in owners:
                ep.kind = "llama-swap"
            elif "llamacpp" in owners or "llama.cpp" in owners:
                ep.kind = "llama.cpp"
    else:
        return None

    # Product-specific probes that survive an unauthenticated 401 on /v1/models.
    if _json_probe(f"{base}/running", key, "running"):
        ep.kind = "llama-swap"
        ep.readiness = "/running"
        ep.notes.append(
            "Swapper: it starts and stops backing engines on demand, so the first "
            "request to an unloaded model blocks for the whole load."
        )
    elif _json_probe(f"{base}/get_model_info", key, "model_path"):
        ep.kind = "SGLang"
    elif ep.kind.startswith("unknown") and _json_probe(
        f"{base}/props", key, "default_generation_settings"
    ):
        ep.kind = "llama.cpp"
    elif ep.kind.startswith("unknown") and _json_probe(f"{base}/info", key, "model_id"):
        ep.kind = "Text Generation Inference"

    if ep.kind.startswith("unknown") and port in KNOWN_PORTS:
        ep.notes.append(f"Port {port} is conventionally {KNOWN_PORTS[port]}.")

    return ep


def _local_hints() -> list[str]:
    """Corroborating evidence from this machine. Never required, only helpful."""
    hints: list[str] = []
    try:
        procs = subprocess.run(
            ["pgrep", "-af", "vllm|ollama|llama-server|llama-swap|sglang|lmstudio|text-generation"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        for line in procs.splitlines()[:6]:
            hints.append(f"process: {line[:110]}")
    except (
        Exception
    ):  # reason: endpoint probing is best-effort; any failure means nothing was detected
        pass
    try:
        containers = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}} {{.Image}} {{.Ports}}"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        for line in containers.splitlines():
            if re.search(r"vllm|ollama|llama|sglang|tgi|text-gen", line, re.I):
                hints.append(f"container: {line[:110]}")
    except (
        Exception
    ):  # reason: endpoint probing is best-effort; any failure means nothing was detected
        pass
    return hints


def _suggest(ep: Endpoint, authenticated: bool) -> list[str]:
    """The commands that attach this endpoint."""
    model = ep.models[0] if ep.models else "<model-id>"
    env = "YOUR_KEY_ENV" if authenticated or ep.auth == "required" else None
    key_flag = f" --api-key-env {env}" if env else ""
    lines = [
        f"pitwall agents profiles discover --base-url {ep.url}{key_flag} --timeout 15",
        f"pitwall agents profiles add local-llm --model {model} --harness goose"
        f" --base-url {ep.url}{key_flag} --seat local",
        "pitwall agents profiles probe local-llm --timeout 120",
    ]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find locally hosted LLM servers and print how to attach them."
    )
    parser.add_argument("--host", default="127.0.0.1", help="host to scan (default: loopback)")
    parser.add_argument("--port", type=int, action="append", help="extra port (repeatable)")
    parser.add_argument("--api-key-env", help="env var holding a bearer token to try")
    parser.add_argument(
        "--all-listening",
        action="store_true",
        help="also probe every listening port on this machine",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    key = os.environ.get(args.api_key_env) if args.api_key_env else None
    if args.api_key_env and not key:
        print(
            "warning: the requested API-key environment variable is not set",
            file=sys.stderr,
        )

    ports = set(KNOWN_PORTS) | set(args.port or [])
    if args.all_listening and args.host in ("127.0.0.1", "localhost", "::1"):
        ports |= _listening_ports()

    found: list[Endpoint] = []
    for port in sorted(ports):
        if not _port_open(args.host, port):
            continue
        ep = _identify(args.host, port, key)
        if ep:
            found.append(ep)

    if args.as_json:
        print(json.dumps({"host": args.host, "endpoints": [asdict(e) for e in found]}, indent=2))
        return 0 if found else 1

    if not found:
        print(f"No OpenAI-compatible endpoint answered on {args.host}.")
        print("\nIf you know the server is running, check the port it binds to:")
        print("  ss -ltnp | grep -iE 'vllm|ollama|llama|sglang'")
        print("and re-run with --port <port>. A server bound to a specific")
        print("interface will not answer on loopback.")
        for hint in _local_hints():
            print(f"  {hint}")
        return 1

    for ep in found:
        print(f"\n{ep.url}")
        print(f"  server     {ep.kind}")
        print(f"  auth       {ep.auth}")
        if ep.readiness:
            print(
                f"  readiness  {ep.readiness} (per-model state; /v1/models is NOT a readiness signal)"
            )
        if ep.models:
            shown = ", ".join(ep.models[:8])
            more = f" (+{len(ep.models) - 8} more)" if len(ep.models) > 8 else ""
            print(f"  models     {shown}{more}")
        elif ep.auth == "required":
            print("  models     unknown -- needs a key")
        for note in ep.notes:
            print(f"  note       {note}")
        print("\n  attach it with:")
        for line in _suggest(ep, args.api_key_env is not None):
            print(f"    {line}")

    print("\nBefore relying on it, read docs/attach-local-endpoint.md -- cold starts,")
    print("tool-calling flags, and per-harness timeouts all bite in practice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
