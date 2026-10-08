from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import unquote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send


class FakeClock:
    def __init__(self, *, auto_advance: bool = False) -> None:
        self.now = 0.0
        self.auto_advance = auto_advance
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.auto_advance:
            self.advance(seconds)
            return
        future = asyncio.get_running_loop().create_future()
        self._waiters.append((self.now + seconds, future))
        await future

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        self.now += seconds
        pending: list[tuple[float, asyncio.Future[None]]] = []
        for deadline, future in self._waiters:
            if deadline <= self.now:
                if not future.done():
                    future.set_result(None)
            else:
                pending.append((deadline, future))
        self._waiters = pending

    async def checkpoint(self) -> None:
        await asyncio.sleep(0)


@dataclass(frozen=True, slots=True)
class FakeSwapperModel:
    id: str
    slot_group: str | None = None
    exclusive: bool = False
    tool_calling: bool = True
    load_delay_s: float = 0.0
    fail_start_after_s: float | None = None
    response_status: int | None = None
    response_body: object | None = None


class FakeSwapperApp:
    def __init__(
        self,
        *,
        catalogue: dict[str, FakeSwapperModel],
        api_key: str,
        clock: FakeClock | None = None,
        auto_advance: bool = False,
        concurrency_limit: int = 1,
        malformed_running_payload: object | None = None,
    ) -> None:
        self.catalogue = catalogue
        self.api_key = api_key
        self.clock = clock or FakeClock(auto_advance=auto_advance)
        self.concurrency_limit = concurrency_limit
        self.malformed_running_payload = malformed_running_payload
        self._states: dict[str, str] = {}
        self._active = 0
        self._app = Starlette(
            routes=[
                Route("/v1/models", self._models, methods=["GET"]),
                Route("/running", self._running, methods=["GET"]),
                Route(
                    "/v1/chat/completions",
                    self._chat_completions,
                    methods=["POST"],
                ),
                Route(
                    "/api/models/unload/{model_id:path}",
                    self._unload,
                    methods=["POST"],
                ),
            ]
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._app(scope, receive, send)

    def configure_edit(self, model: FakeSwapperModel) -> None:
        self.catalogue[model.id] = model
        self._states.pop(model.id, None)

    def _authorized(self, request: Request) -> bool:
        return request.headers.get("authorization") == f"Bearer {self.api_key}"

    async def _models(self, request: Request) -> Response:
        if not self._authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        data = [{"id": model_id, "object": "model"} for model_id in sorted(self._states)]
        return JSONResponse({"object": "list", "data": data})

    async def _running(self, request: Request) -> Response:
        if not self._authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if self.malformed_running_payload is not None:
            return JSONResponse(self.malformed_running_payload)
        return JSONResponse(
            [
                {"model": model_id, "state": self._states[model_id]}
                for model_id in sorted(self._states)
            ]
        )

    async def _chat_completions(self, request: Request) -> Response:
        if not self._authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        model_id = body.get("model")
        model = self.catalogue.get(model_id)
        if model is None:
            return JSONResponse({"error": "model not found"}, status_code=404)
        if model.response_status is not None:
            return JSONResponse(model.response_body, status_code=model.response_status)
        if body.get("tool_choice") == "auto" and not model.tool_calling:
            return JSONResponse(
                {
                    "error": (
                        '"auto" tool choice requires --enable-auto-tool-choice '
                        "and --tool-call-parser"
                    )
                },
                status_code=400,
            )
        if self._active >= self.concurrency_limit:
            return JSONResponse({"error": "concurrency limit"}, status_code=429)

        self._active += 1
        try:
            if self._states.get(model.id) != "ready":
                self._evict_for(model)
                self._states[model.id] = "starting"
                delay = (
                    model.fail_start_after_s
                    if model.fail_start_after_s is not None
                    else model.load_delay_s
                )
                await self.clock.sleep(delay)
                if model.fail_start_after_s is not None:
                    self._states.pop(model.id, None)
                    return JSONResponse({"error": "start failed"}, status_code=503)
                self._states[model.id] = "ready"
            return JSONResponse(
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "model": model.id,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ],
                }
            )
        finally:
            self._active -= 1

    def _evict_for(self, incoming: FakeSwapperModel) -> None:
        if incoming.exclusive:
            self._states.clear()
            return
        for resident_id in tuple(self._states):
            resident = self.catalogue[resident_id]
            if resident.exclusive:
                continue
            if incoming.slot_group is not None and resident.slot_group == incoming.slot_group:
                self._states.pop(resident_id, None)

    async def _unload(self, request: Request) -> Response:
        if not self._authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        self._states.pop(unquote(request.path_params["model_id"]), None)
        return Response(status_code=204)
