"""Facts the audit derives from Pitwall code and behaviour instead of asserting them.

Both the checks and ``RuntimeAuditConfig`` read these, so a runtime input such as
"terminate treats 404 as success" is measured, never typed in as ``True``.
"""

from __future__ import annotations

import ast
import threading
from collections.abc import Coroutine
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pitwall.audit._introspect import (
    CodeFacts,
    facts_from_tree,
    facts_of,
    facts_of_module_file,
    route_handlers,
)
from pitwall.runpod_client import pods, templates

WEBHOOK_MODULE = "pitwall.webhook_receiver"
WEBHOOK_POST_PATHS = {"/webhooks/runpod", "/runpod"}
#: Calls a fast-200 webhook handler may await: read the body, enqueue, record.
FAST_200_ALLOWED_AWAITS = frozenset({"body", "_enqueue_terminal_status_job", "insert_or_skip"})


class ProbeError(RuntimeError):
    """A probe could not run to completion."""


def drive(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine that never suspends, safe inside a running event loop."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    coroutine.close()
    raise ProbeError("audit probe suspended; it must complete without I/O")


# --------------------------------------------------------------------------- #
# Webhook receiver dedupe path                                                #
# --------------------------------------------------------------------------- #


def webhook_handlers() -> list[ast.AsyncFunctionDef]:
    """RunPod POST handlers of the receiver, read from its file (never imported).

    The receiver validates its runtime environment at import time, so it is analysed
    from source instead of being imported.
    """
    _facts, tree = facts_of_module_file(WEBHOOK_MODULE)
    return route_handlers(tree, "post", WEBHOOK_POST_PATHS)


def webhook_handler_facts() -> CodeFacts | None:
    handlers = webhook_handlers()
    if not handlers:
        return None
    return facts_from_tree(ast.Module(body=list(handlers), type_ignores=[]))


def webhook_dedupes_through_repository() -> bool:
    """The receiver handler records deliveries via ``insert_or_skip`` and reports duplicates."""
    facts = webhook_handler_facts()
    return (
        facts is not None
        and facts.references("insert_or_skip")
        and facts.references("WebhookDeliveryRepository")
        and facts.has_string_containing("duplicate")
    )


def webhook_is_fast_200() -> bool:
    """The receiver handler awaits only the body read, the enqueue, and the dedupe insert."""
    facts = webhook_handler_facts()
    return facts is not None and facts.awaited <= FAST_200_ALLOWED_AWAITS


class _DedupePool:
    """asyncpg-shaped pool whose insert honours the UNIQUE (job, attempt) dedupe gate."""

    def __init__(self) -> None:
        self.seen: set[tuple[str, int]] = set()

    def acquire(self) -> _DedupeConnection:
        return _DedupeConnection(self.seen)


class _DedupeConnection:
    def __init__(self, seen: set[tuple[str, int]]) -> None:
        self._seen = seen

    async def __aenter__(self) -> _DedupeConnection:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def fetchrow(self, _sql: str, job_id: str, attempt: int, _payload: object) -> Any:
        key = (job_id, attempt)
        if key in self._seen:
            return None  # ON CONFLICT DO NOTHING returns no row
        self._seen.add(key)
        return {"id": len(self._seen)}


def webhook_repository_skips_duplicates() -> bool:
    """A repeated (job, attempt) delivery is reported as not new by the repository."""
    from pitwall.db.repository import (  # noqa: PLC0415  # reason: audit imports lazily
        _INSERT_WEBHOOK_DELIVERY_SQL,
        WebhookDeliveryRepository,
    )

    if "ON CONFLICT" not in _INSERT_WEBHOOK_DELIVERY_SQL.upper():
        return False
    repository = WebhookDeliveryRepository(_DedupePool())
    first = drive(repository.insert_or_skip("job-audit", 1, {}))
    second = drive(repository.insert_or_skip("job-audit", 1, {}))
    return bool(first.is_new) and not second.is_new


# --------------------------------------------------------------------------- #
# Terminate is idempotent on 404                                              #
# --------------------------------------------------------------------------- #


class _NotFoundHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return None


def terminate_treats_404_as_success() -> bool:
    """Terminate a pod against a loopback server that answers 404, through injected URL and key."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NotFoundHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        pods._terminate_pod_sync(
            "pod-already-gone",
            api_key="audit-probe-key",  # pragma: allowlist secret
            rest_api_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
    except pods.RunPodError:
        return False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return True


# --------------------------------------------------------------------------- #
# Code-shape facts                                                            #
# --------------------------------------------------------------------------- #


def pod_readiness_probe_order() -> tuple[str, ...]:
    return tuple(pods.POD_READINESS_PROBE_ORDER)


def cost_gate_runs_before_readiness_wait() -> bool:
    return any(
        facts_of(create).before("_gate_pod_cost_before_readiness", "wait_for_pod_runtime_sync")
        for create in (pods.create_pod_with_fallback_sync, pods._create_pod_with_fallback_sync)
    )


def readiness_reads_runtime_field() -> bool:
    return facts_of(pods._pod_has_runtime_signal).has_string_containing("runtime")


def template_cache_shape() -> dict[str, bool]:
    ensure = facts_of(templates.ensure_template)
    return {
        "cache_enabled": ensure.references("_lookup_cached") and ensure.references("_insert_cache"),
        "create_on_cache_miss": ensure.references("create_template_rest"),
        "reuse_on_cache_hit": ensure.before("_lookup_cached", "create_template_rest"),
    }


def kill_switch_latches_before_teardown() -> bool:
    from pitwall.api.admin import emergency  # noqa: PLC0415  # reason: audit imports lazily

    return facts_of(emergency.run_kill).before("persist_kill_report", "activate")


__all__ = [
    "FAST_200_ALLOWED_AWAITS",
    "ProbeError",
    "cost_gate_runs_before_readiness_wait",
    "drive",
    "kill_switch_latches_before_teardown",
    "pod_readiness_probe_order",
    "readiness_reads_runtime_field",
    "template_cache_shape",
    "terminate_treats_404_as_success",
    "webhook_dedupes_through_repository",
    "webhook_handler_facts",
    "webhook_is_fast_200",
    "webhook_repository_skips_duplicates",
]
