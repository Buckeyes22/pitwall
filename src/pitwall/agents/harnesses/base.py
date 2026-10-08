"""Typed harness-adapter contract."""

from __future__ import annotations

import math
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pitwall.agents.errors import UsageError

#: Largest prompt an adapter may place in argv. Linux caps one argument at
#: MAX_ARG_STRLEN (128 KiB); the margin leaves room for the flag around it.
ARGV_PROMPT_LIMIT_BYTES = 120 * 1024

_MODEL_FLAGS = ("-m", "--model")

_SECRET_VALUE = re.compile(
    r"""(?:
        (?:sk|pk|rk)-[A-Za-z0-9_-]{16,}
      | gh[pousr]_[A-Za-z0-9]{20,}
      | github_pat_[A-Za-z0-9_]{20,}
      | xox[abprs]-[A-Za-z0-9-]{10,}
      | xai-[A-Za-z0-9]{20,}
      | hf_[A-Za-z0-9]{20,}
      | AKIA[0-9A-Z]{16}
      | AIza[0-9A-Za-z_-]{30,}
      | eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}
    )""",
    re.VERBOSE,
)
_AUTH_SCHEME = re.compile(r"\b(?:Bearer|Basic)\s+\S{12,}", re.IGNORECASE)
_OPAQUE_TOKEN = re.compile(r"[A-Za-z0-9_+=-]{32,}")


def parse_duration_seconds(raw: str) -> float:
    """Seconds in ``raw`` (a number with an optional s/m/h/d suffix).

    Raises ValueError unless the result is finite and positive.
    """
    multipliers = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    suffix = raw[-1:].lower()
    seconds = float(raw[:-1]) * multipliers[suffix] if suffix in multipliers else float(raw)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"not a positive finite duration: {raw!r}")
    return seconds


def _looks_secret(value: str) -> bool:
    if _SECRET_VALUE.search(value) or _AUTH_SCHEME.search(value):
        return True
    return (
        _OPAQUE_TOKEN.fullmatch(value) is not None
        and any(character.isdigit() for character in value)
        and any(character.isalpha() for character in value)
    )


@dataclass(slots=True)
class ParsedRequest:
    source: str
    model: str
    extra_args: list[str]
    has_model_override: bool = False
    adapter_data: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class PreparedCommand:
    argv: list[str]
    env: dict[str, str]
    stdin: bytes | None
    sanitized_args: list[str]


class HarnessAdapter:
    """Translate dispatches for harnesses using stdin, argv, or file prompt delivery."""

    harness_id = ""
    prompt_delivery = "stdin"
    #: True for a harness that always prints its answer: exit 0 with nothing on stdout
    #: (or only whitespace) means it did no work, and dispatch records exit 77 instead.
    empty_stdout_is_failure = False
    binary_override_env: str | None = None
    preflight_binary = True
    missing_binary_ledger = "none"
    start_ledger_before_prompt = False
    # True when the harness takes the model as a leading positional argument
    # (``<model> <prompt-source>``); ``runs resume` must re-emit it. False for
    # harnesses whose model arrives through a replayed flag or their own config.
    model_positional = False
    # How a custom OpenAI-compatible endpoint reaches this harness:
    # "none" (model-bound vendor CLI), "env" (per-process variables), or
    # "config-sync" (the harness reads its own config file; see profiles sync).
    endpoint_delivery = "none"
    # Effort values a workflow task may request (a literal, so the release
    # inventory can read it). None: the harness has no effort control, so
    # workflow validation rejects any effort. Empty: the control exists but its
    # values are not enumerable here.
    effort_values: tuple[str, ...] | None = None
    # True for vendor CLIs that serve only their registered models: a workflow
    # task naming another model is rejected.
    model_bound = False
    # File-delivery adapters normally read the caller's own prompt file. True
    # copies the prompt into the dispatch's private run directory (mode 0600)
    # for every dispatch and removes it when the run ends.
    private_prompt_file = False

    def detect_soft_denial(self, exit_code: int, stderr_tail: bytes) -> str | None:
        """A reason when the harness exited 0 without doing the work; None otherwise.

        Dispatch calls this after a zero-exit run with the tail of the child's
        stderr; a non-None reason converts the run to exit 77 (EX_NOPERM).
        """
        del exit_code, stderr_tail
        return None

    @property
    def supported_efforts(self) -> frozenset[str] | None:
        """Effort values a workflow task may request; None when there is no control."""
        return None if self.effort_values is None else frozenset(self.effort_values)

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        """Shim arguments (after the harness name) for one workflow task."""
        raise NotImplementedError(f"{self.harness_id} does not implement workflow_args")

    def endpoint_argv(self, name: str, entry: Mapping[str, Any]) -> list[str]:
        """Extra harness arguments for an endpoint route (e.g. a harness selector)."""
        del name, entry
        return []

    def endpoint_environment_extras(
        self, entry: Mapping[str, Any], env_updates: Mapping[str, str]
    ) -> dict[str, str]:
        """Variables derived from the registry-mapped endpoint variables."""
        del entry, env_updates
        return {}

    def endpoint_sync_status(
        self,
        name: str,
        entry: Mapping[str, Any],
        env: Mapping[str, str],
        home: Path,
    ) -> str:
        """Whether a route's endpoint is materialized in this harness's own config.

        Returns "not-required" for env/none delivery; config-sync adapters return
        "synced", "missing", or "stale".
        """
        del name, entry, env, home
        return "not-required"

    def endpoint_sync_commands(
        self,
        entries: Mapping[str, Mapping[str, Any]],
        env: Mapping[str, str],
        home: Path,
    ) -> list[list[str]]:
        """argv commands that materialize entries; ``{env:VAR}`` is applied later."""
        del entries, env, home
        return []

    def plan_endpoint_sync(
        self,
        entries: Mapping[str, Mapping[str, Any]],
        env: Mapping[str, str],
        home: Path,
    ) -> tuple[Path, str, str] | list[tuple[Path, str, str]]:
        """Return (config path, current text, text after materializing ``entries``)."""
        raise NotImplementedError(f"{self.harness_id} does not support profiles sync")

    def usage(self) -> str:
        raise NotImplementedError

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        raise NotImplementedError

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        raise NotImplementedError

    def missing_binary_message(self) -> str:
        raise NotImplementedError

    def preflight(
        self,
        request: ParsedRequest,
        binary: str,
        env: Mapping[str, str],
    ) -> dict[str, str]:
        return {}

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        raise NotImplementedError

    @staticmethod
    def parse_model_flag(arguments: list[str], default: str) -> tuple[str, bool]:
        """Return the model named by ``-m X``, ``--model X``, ``--model=X`` or ``-m=X``.

        The last occurrence wins. The flag reports whether the caller overrode
        ``default``.
        """
        model, overridden = default, False
        previous = ""
        for argument in arguments:
            if previous in _MODEL_FLAGS:
                model, overridden = argument, True
            else:
                for flag in _MODEL_FLAGS:
                    if argument.startswith(f"{flag}="):
                        model, overridden = argument.removeprefix(f"{flag}="), True
            previous = argument
        return model, overridden

    def check_prompt_size(self, prompt: bytes) -> None:
        """Refuse a prompt an argv-delivering harness cannot receive in one argument."""
        if self.prompt_delivery == "argv" and len(prompt) > ARGV_PROMPT_LIMIT_BYTES:
            raise UsageError(
                f"{self.harness_id}-shim: prompt is {len(prompt)} bytes; {self.harness_id} takes "
                f"its prompt as a command-line argument, which is limited to "
                f"{ARGV_PROMPT_LIMIT_BYTES} bytes"
            )

    def argv_prompt_text(self, prompt: bytes) -> str:
        """Prompt text for argv delivery, after the size check."""
        self.check_prompt_size(prompt)
        return self.prompt_text(prompt)

    @staticmethod
    def require_args(argv: list[str], count: int, usage: str) -> None:
        if len(argv) < count:
            raise UsageError(usage)

    @staticmethod
    def which(command: str, env: Mapping[str, str]) -> str | None:
        return shutil.which(command, path=env.get("PATH"))

    @staticmethod
    def unrestricted(env: Mapping[str, str]) -> bool:
        """Bypass the child CLI's sandbox and approvals only on an explicit opt-in."""
        return env.get("PITWALL_AGENTS_UNRESTRICTED") == "1"

    def policy_profile(
        self,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str] | None = None,
    ) -> str:
        """Which sandbox/approval policy the child CLI actually runs under.

        `unrestricted` means the shim suppressed the CLI's own prompting;
        `cli-policy` means the CLI keeps enforcing whatever it enforces. An
        adapter whose bypass depends on preflight discovery overrides this.
        """
        return "unrestricted" if self.unrestricted(env) else "cli-policy"

    @staticmethod
    def prompt_text(prompt: bytes) -> str:
        # Bash command substitution strips every trailing newline. Decode with
        # replacement so malformed prompt bytes cannot crash lifecycle cleanup.
        return prompt.rstrip(b"\n").decode("utf-8", errors="replace")

    @staticmethod
    def sanitize_args(arguments: list[str]) -> list[str]:
        sensitive_names = ("api-key", "apikey", "token", "secret", "password", "authorization")
        sanitized: list[str] = []
        redact_next = False
        for argument in arguments:
            lowered = argument.lower()
            if redact_next:
                sanitized.append("<redacted>")
                redact_next = False
                continue
            if argument.startswith("-") and any(name in lowered for name in sensitive_names):
                if "=" in argument:
                    sanitized.append(argument.split("=", 1)[0] + "=<redacted>")
                else:
                    sanitized.append(argument)
                    redact_next = True
                continue
            if argument.startswith("--") and "=" in argument:
                flag, value = argument.split("=", 1)
                sanitized.append(f"{flag}=<redacted>" if _looks_secret(value) else argument)
                continue
            sanitized.append("<redacted>" if _looks_secret(argument) else argument)
        return sanitized
