"""``pitwall workbench``: launch Pi under a workbench profile, and the workbench tools around it.

Port of ``cli.ts``, ``cli-args.ts``, ``cli-path.ts`` and the TypeScript acceptance scripts. Every
command that starts Pi checks the pinned toolchain first, before it creates a worktree, an agent
directory, or any other state.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pitwall.workbench.accounting import usage_report
from pitwall.workbench.comparison.reevaluate import reevaluate_child_only, reevaluate_comparison
from pitwall.workbench.comparison.runner import (
    TINTIN,
    ComparisonUsageError,
    parse_comparison_options,
    run_comparison,
)
from pitwall.workbench.doctor import (
    ToolchainError,
    doctor,
    read_opencode_metadata,
    require_pinned_toolchain,
    toolchain_summary,
)
from pitwall.workbench.hosted.acceptance import run_hosted_acceptance
from pitwall.workbench.hosted.baseline import run_baseline
from pitwall.workbench.hosted.fixture import create_fixture
from pitwall.workbench.hosted.native_acceptance import run_hosted_native_acceptance
from pitwall.workbench.hosted_profiles import credential_value
from pitwall.workbench.launcher import PiLaunchOptions, extension_path, launch_pi
from pitwall.workbench.native_profile import configure_native_profile
from pitwall.workbench.profile import (
    compile_profile,
    configure_provider_profile,
    profile_from_config,
)
from pitwall.workbench.runtime_settings import enforce_runtime_settings

USAGE = (
    "usage: pitwall workbench <command>\n"
    "  launch <profile.json> [profile-name] [cwd] [--continue] [--native-child] [--planning] [--restricted]\n"
    "  doctor\n"
    "  usage <accounting.jsonl>\n"
    "  compare <profile.json> <output-dir> [repetitions] [flags] [candidate...]\n"
    "  reevaluate <original-report> <output-report>\n"
    "  reevaluate-child <original-report> <output-report>\n"
    "  hosted-acceptance <profile.json> <auth.json> <output-dir> [profile-name...]\n"
    "  hosted-native-acceptance <profile.json> <auth.json> <output-dir> [profile-name]\n"
    "  baseline <profile.json> <profile-name> <disposable-fixture> <report.json>\n"
    "  fixture <new-directory>"
)
LAUNCH_USAGE = (
    "usage: pitwall workbench launch <profile.json> [profile-name] [cwd] "
    "[--continue] [--native-child] [--planning] [--restricted]"
)
USAGE_COMMAND_USAGE = "usage: pitwall workbench usage <accounting.jsonl>"
_COMMAND_USAGE = {
    "launch": LAUNCH_USAGE,
    "doctor": "usage: pitwall workbench doctor",
    "usage": USAGE_COMMAND_USAGE,
    "compare": (
        "usage: pitwall workbench compare <profile.json> <output-dir> "
        "[repetitions] [flags] [candidate...]"
    ),
    "reevaluate": "usage: pitwall workbench reevaluate <original-report> <output-report>",
    "reevaluate-child": "usage: pitwall workbench reevaluate-child <original-report> <output-report>",
    "hosted-acceptance": (
        "usage: pitwall workbench hosted-acceptance <profile.json> <auth.json> "
        "<output-dir> [profile-name...]"
    ),
    "hosted-native-acceptance": (
        "usage: pitwall workbench hosted-native-acceptance <profile.json> <auth.json> "
        "<output-dir> [profile-name]"
    ),
    "baseline": (
        "usage: pitwall workbench baseline <profile.json> <profile-name> "
        "<disposable-fixture> <report.json>"
    ),
    "fixture": "usage: pitwall workbench fixture <new-directory>",
}
_LAUNCH_FLAGS = ("--continue", "--native-child", "--planning", "--plan", "--restricted")


@dataclass(frozen=True)
class LaunchArgs:
    continue_session: bool
    planning: bool
    native_child: bool
    restricted: bool
    positional: list[str]


def parse_launch_args(raw: Sequence[str]) -> LaunchArgs:
    """Split the interactive launch flags from the profile, profile-name, and cwd positionals."""
    planning = "--planning" in raw or "--plan" in raw
    return LaunchArgs(
        continue_session="--continue" in raw,
        planning=planning,
        native_child="--native-child" in raw or planning,
        restricted="--restricted" in raw,
        positional=[arg for arg in raw if arg not in _LAUNCH_FLAGS],
    )


def default_state_root() -> Path:
    return Path.home() / ".local" / "state" / "pitwall" / "pi-workbench"


def default_agent_dir(
    profile_name: str,
    cwd: Path | str,
    native_child: bool,
    state_root: Path | str | None = None,
    planning: bool = False,
) -> Path:
    """Private Pi state kept out of the project, isolated by mode, canonical cwd, and profile."""
    root = Path(state_root) if state_root is not None else default_state_root()
    canonical = Path(os.path.realpath(os.path.abspath(cwd)))
    key = hashlib.sha256(str(canonical).encode()).hexdigest()[:16]
    mode = "native-planning" if planning else "native" if native_child else "agent"
    return root / mode / key / profile_name


class CliError(Exception):
    """A concise, user-facing failure: the message is printed as is."""


def _read_json_file(path: Path, message: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CliError(f"file not found: {path}") from None
    try:
        return json.loads(text)
    except ValueError:
        raise CliError(message) from None


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2))


def _needs_subagents_for_compare(flags: Sequence[str]) -> bool:
    requested = [flag for flag in flags if not flag.startswith("--")]
    return not requested or TINTIN in requested


def _check_toolchain(*, subagents: bool) -> Any:
    toolchain = require_pinned_toolchain(subagents=subagents)
    print(toolchain_summary(toolchain, subagents=subagents), file=sys.stderr)
    return toolchain


def _cmd_doctor(args: Sequence[str]) -> int:
    home = Path.home()
    try:
        metadata = read_opencode_metadata(
            home / ".config/opencode/opencode.json", home / ".local/share/opencode/auth.json"
        )
    except ValueError:
        raise CliError("invalid local OpenCode metadata") from None
    _print_json(doctor(os.environ, metadata))
    return 0


def _cmd_usage(args: Sequence[str]) -> int:
    if len(args) != 1:
        print(USAGE_COMMAND_USAGE, file=sys.stderr)
        return 2
    report = usage_report(Path(args[0]).resolve())
    _print_json(
        {
            "requests": report.requests,
            "settled": report.settled,
            "unavailable": report.unavailable,
            "queueWaitMs": report.queue_wait_ms,
            "accounts": report.accounts,
            "usage": report.usage,
            "source": report.source,
            "accountingScope": report.accounting_scope,
        }
    )
    return 0


def _require_linux() -> None:
    """The Pi extension serialises admission with ``flock(1)``, which only Linux provides."""
    if sys.platform != "linux":
        raise CliError(
            "pitwall workbench launch needs Linux: its admission extension uses flock, which "
            f"{sys.platform} does not provide. Run the workbench on a Linux host."
        )


def _cmd_launch(raw: Sequence[str]) -> int:
    _require_linux()
    parsed = parse_launch_args(raw)
    args = parsed.positional
    if not args or len(args) > 3 or any(arg.startswith("--") for arg in args):
        print(LAUNCH_USAGE, file=sys.stderr)
        return 2
    config_path = args[0]
    profile_name = args[1] if len(args) > 1 else "local-coder"
    cwd = Path(args[2] if len(args) > 2 else os.getcwd()).resolve()
    toolchain = _check_toolchain(subagents=parsed.native_child)
    config = _read_json_file(Path(config_path).resolve(), "invalid profile configuration")
    profile = profile_from_config(config, profile_name)
    # Keep generated Pi state out of the selected project. Callers may opt into an explicit
    # private directory with PITWALL_WORKBENCH_AGENT_DIR.
    configured_dir = os.environ.get("PITWALL_WORKBENCH_AGENT_DIR")
    agent_dir = (
        Path(configured_dir).resolve()
        if configured_dir
        else default_agent_dir(profile_name, cwd, parsed.native_child, None, parsed.planning)
    )
    compiled = compile_profile(profile_name, profile, agent_dir)
    enforce_runtime_settings(compiled.agent_dir, cwd, compiled.profile)
    env: dict[str, str] = {}
    extensions: list[Path]
    if parsed.native_child:
        assert toolchain.subagents_entry is not None  # reason: the toolchain check proved it
        native = configure_native_profile(compiled, cwd, planning=parsed.planning)
        env.update(native.env)
        extensions = [toolchain.subagents_entry, extension_path("native-extension")]
    else:
        env.update(configure_provider_profile(compiled).env)
        extensions = [extension_path("extension")]
    if profile.get("accountRef") and profile.get("apiKeyEnv"):
        auth = _read_json_file(
            Path.home() / ".local/share/opencode/auth.json", "invalid hosted credential metadata"
        )
        try:
            env[profile["apiKeyEnv"]] = credential_value(auth, profile["accountRef"])
        except ValueError:
            raise CliError("invalid hosted credential metadata") from None
    child = launch_pi(
        PiLaunchOptions(
            cwd=cwd,
            profile=compiled,
            extensions=extensions,
            env=env,
            continue_session=parsed.continue_session,
            restricted=parsed.restricted,
        ),
        rpc=False,
    )
    return int(child.wait())


def _cmd_compare(args: Sequence[str]) -> int:
    if len(args) < 2:
        print(_COMMAND_USAGE["compare"], file=sys.stderr)
        return 2
    _check_toolchain(subagents=_needs_subagents_for_compare(args[3:]))
    try:
        options = parse_comparison_options(*args)
    except ComparisonUsageError as error:
        raise CliError(str(error)) from None
    _print_json(run_comparison(options))
    return 0


def _reevaluate(
    name: str, runner: Callable[[Path | str, Path | str], object], args: Sequence[str]
) -> int:
    if len(args) != 2:
        print(_COMMAND_USAGE[name], file=sys.stderr)
        return 2
    _print_json(runner(args[0], args[1]))
    return 0


def _cmd_reevaluate(args: Sequence[str]) -> int:
    return _reevaluate("reevaluate", reevaluate_comparison, args)


def _cmd_reevaluate_child(args: Sequence[str]) -> int:
    return _reevaluate("reevaluate-child", reevaluate_child_only, args)


def _cmd_hosted_acceptance(args: Sequence[str]) -> int:
    if len(args) < 3:
        print(_COMMAND_USAGE["hosted-acceptance"], file=sys.stderr)
        return 2
    _check_toolchain(subagents=False)
    return int(run_hosted_acceptance(args[0], args[1], args[2], list(args[3:])))


def _cmd_hosted_native_acceptance(args: Sequence[str]) -> int:
    if not 3 <= len(args) <= 4:
        print(_COMMAND_USAGE["hosted-native-acceptance"], file=sys.stderr)
        return 2
    _check_toolchain(subagents=True)
    return int(run_hosted_native_acceptance(*args))


def _cmd_baseline(args: Sequence[str]) -> int:
    if len(args) != 4:
        print(_COMMAND_USAGE["baseline"], file=sys.stderr)
        return 2
    _check_toolchain(subagents=False)
    return int(run_baseline(args[0], args[1], args[2], args[3]))


def _cmd_fixture(args: Sequence[str]) -> int:
    if len(args) != 1:
        print(_COMMAND_USAGE["fixture"], file=sys.stderr)
        return 2
    try:
        print(create_fixture(args[0]))
    except FileExistsError:
        raise CliError(f"refusing to replace an existing path: {args[0]}") from None
    return 0


_COMMANDS: dict[str, Callable[[Sequence[str]], int]] = {
    "launch": _cmd_launch,
    "doctor": _cmd_doctor,
    "usage": _cmd_usage,
    "compare": _cmd_compare,
    "reevaluate": _cmd_reevaluate,
    "reevaluate-child": _cmd_reevaluate_child,
    "hosted-acceptance": _cmd_hosted_acceptance,
    "hosted-native-acceptance": _cmd_hosted_native_acceptance,
    "baseline": _cmd_baseline,
    "fixture": _cmd_fixture,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run one ``pitwall workbench`` command and return its exit status."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    if len(args) > 1 and args[0] in _COMMANDS and args[1] in ("-h", "--help"):
        print(_COMMAND_USAGE[args[0]])
        return 0
    if not args or args[0] not in _COMMANDS:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        return _COMMANDS[args[0]](args[1:])
    except (CliError, ToolchainError) as error:
        print(str(error), file=sys.stderr)
    except Exception as error:  # reason: every failure is one concise line on stderr, never a traceback that could echo a credential
        print(str(error) or "command failed", file=sys.stderr)
    return 1
