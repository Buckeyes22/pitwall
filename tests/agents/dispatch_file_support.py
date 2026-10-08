"""Run one dispatch through a file-delivery adapter variant of qwen (test helper)."""

from __future__ import annotations

from unittest import mock

from pitwall.agents import harnesses
from pitwall.agents.dispatch import dispatch_legacy
from pitwall.agents.harnesses.base import PreparedCommand
from pitwall.agents.harnesses.qwen import QwenAdapter


class FileQwen(QwenAdapter):
    prompt_delivery = "file"

    def prepare(self, request, binary, prompt, env, preflight_data):  # type: ignore[override]  # reason: test double overrides with a looser signature
        args = [
            *request.extra_args,
            "--output-format",
            "text",
            "--prompt-file",
            preflight_data["promptFile"],
        ]
        return PreparedCommand([binary, *args], dict(env), None, self.sanitize_args(args))


def run() -> int:
    with mock.patch.dict(harnesses._ADAPTERS, {"qwen": FileQwen}):
        return dispatch_legacy("qwen", ["-"])
