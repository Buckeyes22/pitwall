"""Interactive picker that writes one route profile: model -> harness -> details -> optional sync."""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO

from .catalog import load_catalog
from .errors import ProfileSyncError
from .profiles import (
    ROUTE_NAME,
    SEATS,
    ProfilesError,
    add_profile,
    effort_spec,
    find_vendor_harness,
    load_profiles,
    save_profiles,
)
from .profiles_sync import apply_plan, plan_sync, render_diff
from .registry import load_registry
from .setup import (
    HarnessSetupError,
    TerminalSignal,
    TtySession,
    environment_with_user_bins,
    resolve_harness_binary,
)


class Screen(Protocol):
    term: str

    def read_key(self) -> str: ...
    def write(self, text: str) -> None: ...
    def repaint(self, lines: Sequence[str]) -> None: ...
    def finish_repaint(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Choice:
    key: str
    label: str
    detail: str = ""


def render_menu(
    title: str, hint: str, choices: Sequence[Choice], cursor: int, *, use_color: bool
) -> tuple[str, ...]:
    lines = [title, "", hint, ""]
    for index, choice in enumerate(choices):
        line = f"{'>' if index == cursor else ' '} {choice.label:<34} {choice.detail}"
        if index == cursor and use_color:
            line = f"\x1b[7m{line}\x1b[0m"
        lines.append(line)
    return tuple(lines)


def pick_one(
    session: Screen, title: str, hint: str, choices: Sequence[Choice], *, use_color: bool
) -> Choice | None:
    cursor = 0
    while True:
        session.repaint(render_menu(title, hint, choices, cursor, use_color=use_color))
        key = session.read_key()
        if key in {"UP", "k", "K"}:
            cursor = (cursor - 1) % len(choices)
        elif key in {"DOWN", "j", "J"}:
            cursor = (cursor + 1) % len(choices)
        elif key in {"\r", "\n"}:
            session.finish_repaint()
            return choices[cursor]
        elif key in {"q", "Q", "ESC"}:
            session.finish_repaint()
            return None


def read_line(session: Screen, prompt: str, default: str = "") -> str | None:
    session.write(f"{prompt}" + (f" [{default}]" if default else "") + ": ")
    buffer: list[str] = []
    while True:
        key = session.read_key()
        if key in {"\r", "\n"}:
            session.write("\n")
            return "".join(buffer) or default
        if key == "ESC":
            session.write("\n")
            return None
        if key in {"\x7f", "\b"}:
            if buffer:
                buffer.pop()
                session.write("\b \b")
            continue
        if key.isprintable() and len(key) == 1:
            buffer.append(key)
            session.write(key)


def model_choices(registry: Mapping[str, Any], catalog: Mapping[str, Any]) -> list[Choice]:
    choices = [
        Choice("custom", "Custom endpoint…", "any OpenAI-compatible server; you type the model id")
    ]
    for catalog_id, entry in catalog["models"].items():
        choices.append(Choice(f"catalog:{catalog_id}", entry["displayName"], entry["modelId"]))
    for harness_id, harness in registry["harnesses"].items():
        for model_id, model in harness["models"].items():
            choices.append(
                Choice(
                    f"registry:{harness_id}:{model_id}",
                    model["displayName"],
                    f"{model_id} via {harness_id}",
                )
            )
    return choices


def harness_choices(
    registry: Mapping[str, Any],
    env: Mapping[str, str],
    home: Path,
    *,
    needs_endpoint: bool,
    vendor: str | None,
) -> list[Choice]:
    detection_env = environment_with_user_bins(env, home)
    choices: list[Choice] = []
    for harness_id, harness in registry["harnesses"].items():
        installed = resolve_harness_binary(harness_id, detection_env, home)
        if installed is None:
            continue
        if needs_endpoint:
            if harness["harnessKind"] != "model-agnostic" or harness["endpointDelivery"] == "none":
                continue
        elif harness["harnessKind"] == "model-bound" and vendor != harness_id:
            continue
        detail = f"installed at {installed}" + (
            " (needs sync)" if harness["endpointDelivery"] == "config-sync" else ""
        )
        choices.append(Choice(harness_id, harness["displayName"], detail))
    return choices


def suggest_name(model_id: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9._-]", "-", model_id.rsplit("/", 1)[-1]).strip("-.")
    if not candidate or not candidate[0].isalpha():
        candidate = "route-" + candidate
    return candidate[:64] if ROUTE_NAME.fullmatch(candidate[:64]) else "my-route"


def run_profiles_setup(
    env: Mapping[str, str],
    *,
    no_color: bool = False,
    tty_path: str = "/dev/tty",
    output: TextIO | None = None,
) -> int:
    destination = output or sys.stdout
    registry = load_registry()
    catalog = load_catalog(registry=registry)
    home = Path(env.get("HOME", "~")).expanduser()
    try:
        with TtySession(tty_path, term=env.get("TERM")) as session:
            use_color = not no_color and "NO_COLOR" not in env and session.term.lower() != "dumb"
            picked = pick_one(
                session,
                "Route setup — pick a model",
                "Up/Down to move, Enter to choose, q to cancel.",
                model_choices(registry, catalog),
                use_color=use_color,
            )
            if picked is None:
                session.write("Route setup skipped.\n")
                return 0
            vendor: str | None = None
            if picked.key == "custom":
                typed = read_line(session, "Model id as the server reports it")
                if not typed:
                    session.write("Route setup skipped.\n")
                    return 0
                model_id, needs_endpoint = typed, True
            elif picked.key.startswith("catalog:"):
                model_id = catalog["models"][picked.key.split(":", 1)[1]]["modelId"]
                vendor = find_vendor_harness(registry, model_id)
                served = pick_one(
                    session,
                    f"Where is {model_id} served?",
                    "Enter to choose.",
                    [
                        Choice(
                            "endpoint",
                            "At an OpenAI-compatible endpoint",
                            "self-hosted or third-party base URL",
                        ),
                        Choice(
                            "vendor",
                            "Through a vendor/subscription CLI",
                            "no endpoint; the harness's own config decides",
                        ),
                    ],
                    use_color=use_color,
                )
                if served is None:
                    session.write("Route setup skipped.\n")
                    return 0
                needs_endpoint = served.key == "endpoint"
            else:
                _, vendor, model_id = picked.key.split(":", 2)
                needs_endpoint = False
            harnesses = harness_choices(
                registry, env, home, needs_endpoint=needs_endpoint, vendor=vendor
            )
            if not harnesses:
                session.write(
                    "No installed harness can serve that choice; run `pitwall agents setup harnesses` first.\n"
                )
                return 1
            harness = pick_one(
                session,
                "Pick a harness",
                "Only installed, compatible harnesses are listed.",
                harnesses,
                use_color=use_color,
            )
            if harness is None:
                session.write("Route setup skipped.\n")
                return 0
            name = read_line(session, "Route name", default=suggest_name(model_id))
            if not name:
                session.write("Route setup skipped.\n")
                return 0
            base_url = key_env = None
            if needs_endpoint:
                base_url = read_line(session, "Base URL", default="http://localhost:8000/v1")
                if not base_url:
                    session.write("Route setup skipped.\n")
                    return 0
                key_env = (
                    read_line(session, "API key environment variable (blank for none)") or None
                )
            seat_choice = pick_one(
                session,
                "Seat (optional)",
                "Enter to choose.",
                [Choice("", "none", "")] + [Choice(seat, seat, "") for seat in SEATS],
                use_color=use_color,
            )
            seat = seat_choice.key or None if seat_choice else None
            effort: str | None = None
            kind, _key, values = effort_spec(registry, harness.key)
            if kind != "none":
                if values:
                    effort_choice = pick_one(
                        session,
                        "Effort (optional)",
                        "Enter to choose.",
                        [Choice("", "none", "")] + [Choice(value, value, "") for value in values],
                        use_color=use_color,
                    )
                    effort = effort_choice.key or None if effort_choice else None
                else:
                    effort = read_line(session, "Effort (optional, harness-defined)") or None
            try:
                updated = add_profile(
                    load_profiles(env, registry=registry),
                    name,
                    model=model_id,
                    harness=harness.key,
                    base_url=base_url,
                    api_key_env=key_env,
                    seat=seat,
                    effort=effort,
                )
                path = save_profiles(env, updated, registry=registry)
            except ProfilesError as exc:
                session.write(f"Cannot save route: {exc}\n")
                return 2
            session.write(f"Saved route {name!r} to {path}\n")
            if (
                needs_endpoint
                and registry["harnesses"][harness.key]["endpointDelivery"] == "config-sync"
            ):
                session.write(
                    f"{harness.key} reads endpoints from its own config. Run profiles sync now? [y/N] "
                )
                if session.read_key() in {"y", "Y"}:
                    session.write("y\n")
                    try:
                        for plan in plan_sync(
                            load_profiles(env, registry=registry),
                            registry=registry,
                            env=env,
                            home=home,
                            harness=harness.key,
                        ):
                            session.write(render_diff(plan) + "Apply? [y/N] ")
                            if session.read_key() in {"y", "Y"}:
                                backup = apply_plan(plan)
                                session.write(
                                    f"y\nwrote {plan.path}"
                                    + (f" (backup {backup})" if backup else "")
                                    + "\n"
                                )
                            else:
                                session.write("no\n")
                    except ProfileSyncError as exc:
                        session.write(f"sync refused: {exc}\n")
                else:
                    session.write(
                        f"no\nRun `pitwall agents profiles sync --harness {harness.key}` before dispatching {name!r}.\n"
                    )
            return 0
    except HarnessSetupError:
        print(
            "pitwall agents: setup profiles needs an interactive terminal; use "
            "`pitwall agents profiles add <name> --model ID [--harness H] [--base-url URL --api-key-env VAR]` instead",
            file=sys.stderr,
        )
        return 2
    except TerminalSignal as exc:
        return 128 + exc.signum
    finally:
        destination.flush()
