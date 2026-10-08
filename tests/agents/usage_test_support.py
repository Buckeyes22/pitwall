"""Shared fakes for the usage tests: a recording opener and credential builders."""

from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
EPOCH = NOW.timestamp()


class Response(io.BytesIO):
    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class FakeOpener:
    """Answers by URL substring and records every request it is handed."""

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = dict(answers or {})
        self.calls: list[request.Request] = []

    def __call__(self, outbound: request.Request, timeout: float = 0.0) -> Response:
        self.calls.append(outbound)
        for fragment, answer in self.answers.items():
            if fragment in outbound.full_url:
                if isinstance(answer, int):
                    raise error.HTTPError(
                        outbound.full_url,
                        answer,
                        "refused",
                        None,
                        io.BytesIO(b"body-that-must-not-leak"),
                    )  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
                if isinstance(answer, Exception):
                    raise answer
                if isinstance(answer, bytes):
                    return Response(answer)
                return Response(json.dumps(answer).encode("utf-8"))
        raise error.URLError(f"no fake answer for {outbound.full_url}")

    @property
    def urls(self) -> list[str]:
        return [call.full_url for call in self.calls]


def jwt(payload: dict[str, Any]) -> str:
    def part(value: dict[str, Any]) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode("ascii").rstrip("=")
        )

    return f"{part({'alg': 'none'})}.{part(payload)}.signature"


def claude_login(
    directory: Path,
    *,
    expires_at_ms: float,
    tier: str = "default_claude_max_20x",
    kind: str = "max",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "claude-access",
                    "refreshToken": "claude-refresh",
                    "expiresAt": expires_at_ms,
                    "subscriptionType": kind,
                    "rateLimitTier": tier,
                }
            }
        ),
        encoding="utf-8",
    )
    return directory


def codex_login(
    directory: Path, *, exp: float, account_id: str | None = "acct-1", plan: str = "pro"
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tokens: dict[str, Any] = {
        "access_token": jwt({"exp": exp}),
        "refresh_token": "codex-refresh",
        "id_token": jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct-from-id-token",
                    "chatgpt_plan_type": plan,
                }
            }
        ),
    }
    if account_id is not None:
        tokens["account_id"] = account_id
    (directory / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": tokens}), encoding="utf-8"
    )
    return directory


class ReaderCase(unittest.TestCase):
    """A throwaway home and state directory for one reader test."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {"HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")}

    def tearDown(self) -> None:
        self.tmp.cleanup()
