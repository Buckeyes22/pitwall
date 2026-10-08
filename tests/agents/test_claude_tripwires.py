"""Regression tests for Claude Code's Stop-hook enforcement boundaries."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests.agents.profiles_fixture import write_profiles
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "plugins" / "claude" / "hooks"
DAG_TRIPWIRE = HOOKS / "dag-tripwire.py"
LEDGER_TRIPWIRE = HOOKS / "ledger-tripwire.py"
LAUNCH_GUARD = HOOKS / "launch-guard.py"
DAG_COMMAND = "command-name>/pitwall:dag-routing run"


def user_entry(text: str) -> dict[str, object]:
    return {"type": "user", "message": {"content": text}}


def bash_entry(command: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]
        },
    }


def agent_entry(subagent_type: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Agent",
                    "input": {"subagent_type": subagent_type},
                }
            ]
        },
    }


def workflow_entry() -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Workflow", "input": {}}]},
    }


def managed_dispatch_entry() -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "mcp__pitwall-channel__dispatch_and_wait",
                    "input": {"prompt": "node"},
                }
            ]
        },
    }


_HOOK_HOME = tempfile.mkdtemp(prefix="tripwire-home-")
# A shim record with a non-ok outcome, so the ledger tripwire has something to nudge about.
# Tests that need a different ledger patch PITWALL_AGENTS_LEDGER themselves.
_DEFAULT_LEDGER = Path(_HOOK_HOME) / "observations.jsonl"
_DEFAULT_LEDGER.write_text(
    json.dumps(
        {
            "source": "shim",
            "event": "finished",
            "outcome": "error",
            "shim": "codex",
            "model": "m",
            "exit": 1,
            "ts": "2099-01-01T00:00:00",
        }
    )
    + "\n",
    encoding="utf-8",
)


def hook_env() -> dict[str, str]:
    """Hooks never read the developer's real ~/.claude state: isolate HOME and the ledger."""
    env = dict(os.environ)
    env["HOME"] = _HOOK_HOME
    env["XDG_STATE_HOME"] = str(Path(_HOOK_HOME) / "state")
    env["XDG_CONFIG_HOME"] = str(Path(_HOOK_HOME) / "config")
    env.pop("PITWALL_AGENTS_STATE_HOME", None)
    env.pop("PITWALL_AGENTS_PROFILES", None)
    env.setdefault("PITWALL_AGENTS_LEDGER", str(_DEFAULT_LEDGER))
    return env


def run_hook(
    hook: Path,
    entries: list[dict[str, object]],
    *,
    stop_hook_active: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, object] | None:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", encoding="utf-8") as transcript:
        for entry in entries:
            transcript.write(json.dumps(entry) + "\n")
        transcript.flush()
        result = subprocess.run(
            [sys.executable, str(hook)],
            env={**hook_env(), **(env or {})},
            input=json.dumps(
                {
                    "stop_hook_active": stop_hook_active,
                    "transcript_path": transcript.name,
                }
            ),
            text=True,
            capture_output=True,
            timeout=HANG_GUARD_SECS,
            check=False,
        )
    if result.returncode != 0:
        raise AssertionError(f"{hook.name} exited {result.returncode}: {result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def run_launch_guard(
    payload: dict[str, object], *, active: bool | None = True
) -> dict[str, object] | None:
    env = hook_env()
    marker_root = Path(_HOOK_HOME) / "routing-markers"
    env["PITWALL_AGENTS_ROUTING_MARKER_DIR"] = str(marker_root)
    if active is True:
        marker_root.mkdir(parents=True, exist_ok=True)
        session_id = str(payload.get("session_id") or "")
        (marker_root / f"{session_id}.json").write_text(
            json.dumps({"active": True, "session_id": session_id}), encoding="utf-8"
        )
    elif active is False:
        session_id = str(payload.get("session_id") or "")
        with contextlib.suppress(FileNotFoundError):
            (marker_root / f"{session_id}.json").unlink()
    result = subprocess.run(
        [sys.executable, str(LAUNCH_GUARD)],
        env=env,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=HANG_GUARD_SECS,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"{LAUNCH_GUARD.name} exited {result.returncode}: {result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else None


class ClaudeTripwireTests(unittest.TestCase):
    def run_dag_hook(self, command: str) -> dict[str, object] | None:
        return run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), bash_entry(command)])

    def test_run_and_resume_reject_non_claude_host_declarations(self) -> None:
        for command in (
            "pitwall agents workflow run workflow.json --host copilot",
            "pitwall agents workflow resume 123 --host=codex",
            "bash -lc 'pitwall agents workflow run workflow.json --host copilot'",
        ):
            with self.subTest(command=command):
                response = self.run_dag_hook(command)
                self.assertEqual("block", response["decision"] if response else None)
                self.assertIn("HOST BOUNDARY", str(response["reason"]))

    def test_run_and_resume_allow_the_claude_host_declaration(self) -> None:
        for command in (
            "pitwall agents workflow run workflow.json --host claude",
            "pitwall agents workflow resume 123 --host=claude",
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.run_dag_hook(command))

    def test_runner_text_that_is_not_executed_is_silent(self) -> None:
        self.assertIsNone(
            self.run_dag_hook(
                "grep 'pitwall agents workflow run workflow.json --host copilot' README.md"
            )
        )

    def test_workflow_tool_satisfies_dag_tripwire(self) -> None:
        response = run_hook(
            DAG_TRIPWIRE,
            [
                user_entry(DAG_COMMAND),
                bash_entry("scripts/codex-shim.sh prompt.md"),
                workflow_entry(),
            ],
        )
        self.assertIsNone(response)

    def test_managed_dispatch_tool_satisfies_dag_tripwire(self) -> None:
        response = run_hook(
            DAG_TRIPWIRE,
            [user_entry(DAG_COMMAND), managed_dispatch_entry()],
        )
        self.assertIsNone(response)

    def test_ledger_append_satisfies_ledger_tripwire(self) -> None:
        response = run_hook(
            LEDGER_TRIPWIRE,
            [
                user_entry("route this"),
                bash_entry("scripts/codex-shim.sh prompt.md"),
                bash_entry("echo note >> ~/.local/state/pitwall/agents/ledger/observations.jsonl"),
            ],
        )
        self.assertIsNone(response)

    def test_stop_hook_active_is_loop_safe_for_both_hooks(self) -> None:
        entries = [
            user_entry(DAG_COMMAND),
            bash_entry("scripts/codex-shim.sh prompt.md"),
        ]
        for hook in (DAG_TRIPWIRE, LEDGER_TRIPWIRE):
            with self.subTest(hook=hook.name):
                self.assertIsNone(run_hook(hook, entries, stop_hook_active=True))

    def test_garbage_input_is_fail_open_for_both_hooks(self) -> None:
        for hook in (DAG_TRIPWIRE, LEDGER_TRIPWIRE):
            with self.subTest(hook=hook.name):
                result = subprocess.run(
                    [sys.executable, str(hook)],
                    input="not-json",
                    text=True,
                    capture_output=True,
                    timeout=HANG_GUARD_SECS,
                    check=False,
                )
                self.assertEqual(0, result.returncode)
                self.assertEqual("", result.stdout)


class QwenTripwireCoverageTests(unittest.TestCase):
    """Qwen is a first-class harness (v0.7.0); both Stop hooks must see its dispatches."""

    QWEN_DISPATCHES = (
        ("bash shim path", bash_entry("~/.claude/scripts/qwen-shim.sh /tmp/prompt.md")),
        (
            "bash repo-relative",
            bash_entry("scripts/qwen-shim.sh prompt.md --model qwen3-coder"),
        ),
        (
            "bash control flow",
            bash_entry("bash -lc 'if true; then scripts/qwen-shim.sh -; fi'"),
        ),
        # An unbalanced quote defeats shlex, so this exercises the regex fallback path.
        (
            "bash unparseable",
            bash_entry("scripts/qwen-shim.sh prompt.md 'unterminated"),
        ),
        ("namespaced agent", agent_entry("pitwall:qwen-shim")),
        ("legacy bare agent", agent_entry("qwen-shim")),
    )

    def test_dag_tripwire_blocks_direct_qwen_dispatch_without_workflow(self) -> None:
        for label, dispatch in self.QWEN_DISPATCHES:
            with self.subTest(dispatch=label):
                response = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), dispatch])
                self.assertEqual("block", response["decision"] if response else None)
                self.assertIn("TRIPWIRE", str(response["reason"]))

    def test_ledger_tripwire_nudges_after_qwen_dispatch_without_note(self) -> None:
        for label, dispatch in self.QWEN_DISPATCHES:
            with self.subTest(dispatch=label):
                response = run_hook(LEDGER_TRIPWIRE, [user_entry("route this to qwen"), dispatch])
                self.assertEqual("block", response["decision"] if response else None)
                self.assertIn("LEDGER", str(response["reason"]))

    def test_qwen_pong_probe_stays_silent(self) -> None:
        for hook, lead in (
            (DAG_TRIPWIRE, DAG_COMMAND),
            (LEDGER_TRIPWIRE, "probe qwen"),
        ):
            with self.subTest(hook=hook.name):
                self.assertIsNone(
                    run_hook(
                        hook,
                        [
                            user_entry(lead),
                            bash_entry("scripts/qwen-shim.sh /tmp/pong.md"),
                        ],
                    )
                )


class ClineTripwireCoverageTests(unittest.TestCase):
    CLINE_DISPATCHES = (
        ("bash", bash_entry("~/.claude/scripts/cline-shim.sh /tmp/prompt.md")),
        ("namespaced agent", agent_entry("pitwall:cline-shim")),
    )

    def test_both_hooks_treat_cline_as_a_dispatch(self) -> None:
        for label, dispatch in self.CLINE_DISPATCHES:
            with self.subTest(dispatch=label):
                dag = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), dispatch])
                self.assertIn("TRIPWIRE", str(dag["reason"]) if dag else "")
                ledger = run_hook(LEDGER_TRIPWIRE, [user_entry("route this to Cline"), dispatch])
                self.assertIn("LEDGER", str(ledger["reason"]) if ledger else "")


class PiTripwireCoverageTests(unittest.TestCase):
    PI_DISPATCHES = (
        ("bash", bash_entry("~/.claude/scripts/pi-shim.sh /tmp/prompt.md")),
        ("namespaced agent", agent_entry("pitwall:pi-shim")),
    )

    def test_both_hooks_treat_pi_as_a_dispatch(self) -> None:
        for label, dispatch in self.PI_DISPATCHES:
            with self.subTest(dispatch=label):
                dag = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), dispatch])
                self.assertIn("TRIPWIRE", str(dag["reason"]) if dag else "")
                ledger = run_hook(LEDGER_TRIPWIRE, [user_entry("route this to Pi"), dispatch])
                self.assertIn("LEDGER", str(ledger["reason"]) if ledger else "")


class DshTripwireCoverageTests(unittest.TestCase):
    DSH_DISPATCHES = (
        ("bash", bash_entry("~/.claude/scripts/dsh-shim.sh /tmp/prompt.md")),
        ("namespaced agent", agent_entry("pitwall:dsh-shim")),
    )

    def test_both_hooks_treat_dsh_as_a_dispatch(self) -> None:
        for label, dispatch in self.DSH_DISPATCHES:
            with self.subTest(dispatch=label):
                dag = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), dispatch])
                self.assertIn("TRIPWIRE", str(dag["reason"]) if dag else "")
                ledger = run_hook(LEDGER_TRIPWIRE, [user_entry("route this to dsh"), dispatch])
                self.assertIn("LEDGER", str(ledger["reason"]) if ledger else "")


class HermesTripwireCoverageTests(unittest.TestCase):
    HERMES_DISPATCHES = (
        ("bash", bash_entry("~/.claude/scripts/hermes-shim.sh /tmp/prompt.md")),
        ("namespaced agent", agent_entry("pitwall:hermes-shim")),
    )

    def test_both_hooks_treat_hermes_as_a_dispatch(self) -> None:
        for label, dispatch in self.HERMES_DISPATCHES:
            with self.subTest(dispatch=label):
                dag = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), dispatch])
                self.assertIn("TRIPWIRE", str(dag["reason"]) if dag else "")
                ledger = run_hook(LEDGER_TRIPWIRE, [user_entry("route this to Hermes"), dispatch])
                self.assertIn("LEDGER", str(ledger["reason"]) if ledger else "")


class GooseTripwireCoverageTests(unittest.TestCase):
    GOOSE_DISPATCHES = (
        ("bash", bash_entry("~/.claude/scripts/goose-shim.sh /tmp/prompt.md")),
        ("namespaced agent", agent_entry("pitwall:goose-shim")),
    )

    def test_both_hooks_treat_goose_as_a_dispatch(self) -> None:
        for label, dispatch in self.GOOSE_DISPATCHES:
            with self.subTest(dispatch=label):
                dag = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), dispatch])
                self.assertIn("TRIPWIRE", str(dag["reason"]) if dag else "")
                ledger = run_hook(LEDGER_TRIPWIRE, [user_entry("route this to goose"), dispatch])
                self.assertIn("LEDGER", str(ledger["reason"]) if ledger else "")


class MuseTripwireCoverageTests(unittest.TestCase):
    MUSE_DISPATCHES = (
        ("bash", bash_entry("~/.claude/scripts/muse-shim.sh /tmp/prompt.md")),
        ("namespaced agent", agent_entry("pitwall:muse-shim")),
    )

    def test_both_hooks_treat_muse_as_a_dispatch(self) -> None:
        for label, dispatch in self.MUSE_DISPATCHES:
            with self.subTest(dispatch=label):
                dag = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), dispatch])
                self.assertIn("TRIPWIRE", str(dag["reason"]) if dag else "")
                ledger = run_hook(LEDGER_TRIPWIRE, [user_entry("route this to Muse"), dispatch])
                self.assertIn("LEDGER", str(ledger["reason"]) if ledger else "")


class AgyTripwireCoverageTests(unittest.TestCase):
    AGY_DISPATCHES = (
        ("bash", bash_entry("~/.claude/scripts/agy-shim.sh /tmp/prompt.md")),
        ("namespaced agent", agent_entry("pitwall:agy-shim")),
    )

    def test_both_hooks_treat_agy_as_a_dispatch(self) -> None:
        for label, dispatch in self.AGY_DISPATCHES:
            with self.subTest(dispatch=label):
                dag = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), dispatch])
                self.assertIn("TRIPWIRE", str(dag["reason"]) if dag else "")
                ledger = run_hook(LEDGER_TRIPWIRE, [user_entry("route this to Gemini"), dispatch])
                self.assertIn("LEDGER", str(ledger["reason"]) if ledger else "")

    def test_agy_claude_model_overrides_hit_the_host_boundary(self) -> None:
        for command in (
            "~/.claude/scripts/agy-shim.sh p.md --model claude-sonnet-4-6",
            "agy-shim.sh p.md --model=claude-opus-4-6-thinking",
        ):
            with self.subTest(command=command):
                response = run_hook(
                    DAG_TRIPWIRE,
                    [user_entry(DAG_COMMAND), bash_entry(command)],
                )
                self.assertIn("AGY BOUNDARY", str(response["reason"]) if response else "")


class RouteShimTripwireTests(unittest.TestCase):
    ROUTE_DISPATCHES = (
        ("bash", bash_entry("~/.claude/scripts/route-shim.sh glimmer /tmp/prompt.md")),
        (
            "bash override",
            bash_entry("scripts/route-shim.sh glimmer@opencode prompt.md"),
        ),
        ("namespaced agent", agent_entry("pitwall:route-shim")),
    )

    def test_both_hooks_treat_route_shim_as_a_dispatch(self) -> None:
        for label, dispatch in self.ROUTE_DISPATCHES:
            with self.subTest(dispatch=label):
                dag = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), dispatch])
                self.assertIn("TRIPWIRE", str(dag["reason"]) if dag else "")
                ledger = run_hook(LEDGER_TRIPWIRE, [user_entry("route this"), dispatch])
                self.assertIn("LEDGER", str(ledger["reason"]) if ledger else "")
        self.assertIsNone(
            run_hook(
                DAG_TRIPWIRE,
                [
                    user_entry(DAG_COMMAND),
                    bash_entry("scripts/route-shim.sh glimmer /tmp/pong.md"),
                ],
            )
        )

    def test_claude_model_specs_through_route_shim_hit_the_route_boundary(self) -> None:
        agent = agent_entry("pitwall:route-shim")
        agent["message"]["content"][0]["input"]["prompt"] = (
            "Run verbatim: ~/.claude/scripts/route-shim.sh fable /tmp/x.md"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        )
        for label, dispatch in (
            (
                "bash sonnet",
                bash_entry("~/.claude/scripts/route-shim.sh sonnet /tmp/p.md"),
            ),
            ("bash opus override", bash_entry("route-shim.sh opus@claude p.md")),
            (
                "bash claude id",
                bash_entry("scripts/route-shim.sh claude-sonnet-5 p.md"),
            ),
            ("agent prompt", agent),
        ):
            with self.subTest(dispatch=label):
                response = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), dispatch])
                self.assertIn("ROUTE BOUNDARY", str(response["reason"]) if response else "")
        plain = run_hook(
            DAG_TRIPWIRE,
            [user_entry(DAG_COMMAND), bash_entry("scripts/route-shim.sh glimmer p.md")],
        )
        self.assertNotIn("ROUTE BOUNDARY", str(plain["reason"]) if plain else "")

    def test_a_saved_claude_route_with_its_own_login_directory_passes_the_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            routes_file = Path(directory) / "profiles.toml"
            write_profiles(
                routes_file,
                {
                    "models": {
                        "claude-home": {
                            "model": "sonnet",
                            "harness": "claude",
                            "env": {"CLAUDE_CONFIG_DIR": "/srv/logins/claude-home"},
                        },
                        "claude-plain": {"model": "sonnet", "harness": "claude", "env": {}},
                        "claude-blank": {
                            "model": "sonnet",
                            "harness": "claude",
                            "env": {"CLAUDE_CONFIG_DIR": "  "},
                        },
                    },
                },
            )
            env = {"PITWALL_AGENTS_PROFILES": str(routes_file)}
            for spec, blocked in (
                ("claude-home", False),
                ("claude-home@claude", False),
                ("claude-plain", True),
                ("claude-blank", True),
                ("claude-missing", True),
                ("sonnet", True),
            ):
                with self.subTest(spec=spec):
                    response = run_hook(
                        DAG_TRIPWIRE,
                        [user_entry(DAG_COMMAND), bash_entry(f"scripts/route-shim.sh {spec} p.md")],
                        env=env,
                    )
                    reason = str(response["reason"]) if response else ""
                    self.assertEqual(blocked, "ROUTE BOUNDARY" in reason, reason)
            agent = agent_entry("pitwall:route-shim")
            agent["message"]["content"][0]["input"]["prompt"] = (  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
                "Run verbatim: ~/.claude/scripts/route-shim.sh claude-home /tmp/x.md"
            )
            response = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), agent], env=env)
            self.assertNotIn("ROUTE BOUNDARY", str(response["reason"]) if response else "")

    def test_an_unreadable_routes_file_leaves_the_boundary_in_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            routes_file = Path(directory) / "profiles.toml"
            for content in (
                "",
                "[agents.profiles",
                "models = []",
                "[agents.profiles]\nmodels = []",
                '[agents.profiles.models.claude-home]\nenv = "text"',
            ):
                with self.subTest(content=content):
                    routes_file.write_text(content, encoding="utf-8")
                    response = run_hook(
                        DAG_TRIPWIRE,
                        [
                            user_entry(DAG_COMMAND),
                            bash_entry("scripts/route-shim.sh claude-home p.md"),
                        ],
                        env={"PITWALL_AGENTS_PROFILES": str(routes_file)},
                    )
                    self.assertIn("ROUTE BOUNDARY", str(response["reason"]) if response else "")


class LaunchGuardTests(unittest.TestCase):
    def _payload(self, tool_name: str, tool_input: dict[str, object]) -> dict[str, object]:
        return {
            "hook_event_name": "PreToolUse",
            "session_id": "9f35b460-6c1b-4075-8f9e-f3bb5a5c2e24",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }

    def test_blocks_foreground_and_background_shims_before_execution(self) -> None:
        for command in (
            "~/.claude/scripts/codex-shim.sh prompt.md",
            "~/.claude/scripts/opencode-shim.sh prompt.md &",
            "bash -lc 'exec ~/.claude/scripts/kimi-shim.sh prompt.md'",
        ):
            with self.subTest(command=command):
                response = run_launch_guard(self._payload("Bash", {"command": command}))
                self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])
                self.assertIn(
                    "dispatch_and_wait", response["hookSpecificOutput"]["permissionDecisionReason"]
                )

    def test_blocks_recognizable_direct_harness_and_dispatcher_launches(self) -> None:
        for command in (
            "claude --print 'do work'",
            "env FOO=bar codex --help",
            "pitwall agents dispatch codex prompt.md",
            "pitwall agents workflow run graph.json --host claude",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(run_launch_guard(self._payload("Bash", {"command": command})))

    def test_blocks_namespaced_and_prompted_shim_agents(self) -> None:
        namespaced = self._payload(
            "Agent",
            {"subagent_type": "pitwall:codex-shim", "run_in_background": True},
        )
        prompted = self._payload(
            "Agent",
            {
                "subagent_type": "general-purpose",
                "run_in_background": True,
                "prompt": "Run ~/.claude/scripts/grok-shim.sh prompt.md",
            },
        )
        for payload in (namespaced, prompted):
            with self.subTest(payload=payload):
                response = run_launch_guard(payload)
                self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])

    def test_allows_managed_channel_and_native_tools(self) -> None:
        cases = (
            ("mcp__pitwall-channel__dispatch_and_wait", {"route": "codex"}),
            ("mcp__pitwall-channel__answer_and_wait", {"dispatch_id": "x"}),
            ("Bash", {"command": "printf ordinary"}),
            ("Bash", {"command": "grep claude README.md"}),
        )
        for tool_name, tool_input in cases:
            with self.subTest(tool_name=tool_name, tool_input=tool_input):
                self.assertIsNone(run_launch_guard(self._payload(tool_name, tool_input)))

    def test_allows_native_research_agents_while_routing_is_active(self) -> None:
        for tool_input in (
            {
                "subagent_type": "Explore",
                "prompt": "find the doctor checks",
                "run_in_background": False,
            },
            {
                "subagent_type": "general-purpose",
                "prompt": "summarise README.md",
                "run_in_background": True,
            },
        ):
            with self.subTest(tool_input=tool_input):
                self.assertIsNone(run_launch_guard(self._payload("Agent", tool_input)))

    def test_native_agent_shim_bash_call_is_denied(self) -> None:
        payload = self._payload("Bash", {"command": "~/.claude/scripts/codex-shim.sh prompt.md"})
        payload["agent_id"] = "a1b2c3"
        response = run_launch_guard(payload)
        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])

    def test_session_end_clears_the_routing_marker(self) -> None:
        session_end = {
            "hook_event_name": "SessionEnd",
            "session_id": "9f35b460-6c1b-4075-8f9e-f3bb5a5c2e24",
        }
        self.assertIsNone(run_launch_guard(session_end))
        direct = self._payload("Bash", {"command": "codex-shim.sh prompt.md"})
        self.assertIsNone(run_launch_guard(direct, active=False))

    def test_dag_command_expansion_activates_before_first_child_launch(self) -> None:
        expansion = {
            "hook_event_name": "UserPromptExpansion",
            "session_id": self._payload("Bash", {}).get("session_id"),
            "expansion_type": "slash_command",
            "command_name": "pitwall:dag-routing",
            "command_args": "run a graph",
            "command_source": "plugin",
            "prompt": "/pitwall:dag-routing run a graph",
        }
        self.assertIsNone(run_launch_guard(expansion, active=False))
        first_launch = self._payload("Bash", {"command": "codex-shim.sh prompt.md"})
        response = run_launch_guard(first_launch, active=None)
        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])

    def test_unrelated_prompt_expansion_does_not_activate_guard(self) -> None:
        expansion = {
            "hook_event_name": "UserPromptExpansion",
            "session_id": self._payload("Bash", {}).get("session_id"),
            "expansion_type": "slash_command",
            "command_name": "some-other-command",
            "command_source": "plugin",
            "prompt": "/some-other-command",
        }
        self.assertIsNone(run_launch_guard(expansion, active=False))
        self.assertIsNone(
            run_launch_guard(
                self._payload("Bash", {"command": "~/.claude/scripts/codex-shim.sh prompt.md"}),
                active=None,
            )
        )

    def test_stop_clears_idle_routing_marker(self) -> None:
        expansion = {
            "hook_event_name": "UserPromptExpansion",
            "session_id": self._payload("Bash", {}).get("session_id"),
            "expansion_type": "slash_command",
            "command_name": "pitwall:dag-routing",
            "command_source": "plugin",
        }
        self.assertIsNone(run_launch_guard(expansion, active=False))
        stop = {
            "hook_event_name": "Stop",
            "session_id": expansion["session_id"],
        }
        self.assertIsNone(run_launch_guard(stop, active=None))
        self.assertIsNone(
            run_launch_guard(
                self._payload("Bash", {"command": "~/.claude/scripts/codex-shim.sh prompt.md"}),
                active=None,
            )
        )

    def test_routing_skill_tool_activates_before_first_child_launch(self) -> None:
        skill = self._payload("Skill", {"skill": "subagent-model-routing"})
        self.assertIsNone(run_launch_guard(skill, active=False))
        response = run_launch_guard(
            self._payload("Bash", {"command": "~/.claude/scripts/codex-shim.sh prompt.md"}),
            active=None,
        )
        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])

    def test_workflow_denied_only_when_it_launches_external_models(self) -> None:
        denied = (
            {"script": "await agent('x', {agentType: 'pitwall:codex-shim'})"},
            {"script": "await codex('review the diff')"},
            {"scriptPath": "/nonexistent/workflow.js"},
            {"name": "saved-workflow"},
        )
        for tool_input in denied:
            with self.subTest(tool_input=tool_input):
                response = run_launch_guard(self._payload("Workflow", tool_input))
                self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])
        self.assertIsNone(
            run_launch_guard(
                self._payload("Workflow", {"script": "await agent('summarise README')"})
            )
        )

    def test_dispatch_terminal_releases_one_lease_and_preserves_sibling(self) -> None:
        first = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "codex"})
        first["tool_use_id"] = "toolu_first"
        second = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "gemini"})
        second["tool_use_id"] = "toolu_second"
        self.assertIsNone(run_launch_guard(first, active=False))
        self.assertIsNone(run_launch_guard(second, active=None))

        terminal_first = {
            "hook_event_name": "PostToolUse",
            "session_id": first["session_id"],
            "tool_name": first["tool_name"],
            "tool_use_id": first["tool_use_id"],
            "tool_response": {"event": "terminal", "dispatch_id": "dispatch-first"},
        }
        self.assertIsNone(run_launch_guard(terminal_first, active=None))
        marker = Path(_HOOK_HOME) / "routing-markers" / f"{first['session_id']}.json"
        record = json.loads(marker.read_text(encoding="utf-8"))
        sibling = record["pending_dispatches"]
        self.assertEqual(1, len(sibling))
        self.assertEqual("toolu_second", sibling[0]["tool_use_id"])
        self.assertIsInstance(sibling[0].get("created_at"), (int, float))
        self.assertEqual(
            [{"tool_use_id": "toolu_second"}],
            [
                {k: v for k, v in lease.items() if k != "created_at"}
                for lease in record["pending_dispatches"]
            ],
        )
        blocked = run_launch_guard(
            self._payload("Bash", {"command": "~/.claude/scripts/codex-shim.sh prompt.md"}),
            active=None,
        )
        self.assertEqual("deny", blocked["hookSpecificOutput"]["permissionDecision"])

        terminal_second = dict(terminal_first)
        terminal_second["tool_use_id"] = second["tool_use_id"]
        terminal_second["tool_response"] = {"event": "terminal", "dispatch_id": "dispatch-second"}
        self.assertIsNone(run_launch_guard(terminal_second, active=None))
        self.assertFalse(marker.exists())

    def test_string_tool_response_releases_the_lease(self) -> None:
        start = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "q38-cline"})
        start["tool_use_id"] = "toolu_string"
        self.assertIsNone(run_launch_guard(start, active=False))
        done = {
            "hook_event_name": "PostToolUse",
            "session_id": start["session_id"],
            "tool_name": start["tool_name"],
            "tool_use_id": "toolu_string",
            "tool_response": json.dumps({"event": "terminal", "dispatch_id": "d-string"}),
        }
        self.assertIsNone(run_launch_guard(done, active=None))
        marker = Path(_HOOK_HOME) / "routing-markers" / f"{start['session_id']}.json"
        self.assertFalse(marker.exists())

    def test_stop_drops_leases_whose_dispatch_finished_without_a_post_hook(self) -> None:
        start = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "q38-cline"})
        start["tool_use_id"] = "toolu_backgrounded"
        self.assertIsNone(run_launch_guard(start, active=False))
        marker = Path(_HOOK_HOME) / "routing-markers" / f"{start['session_id']}.json"
        record = json.loads(marker.read_text(encoding="utf-8"))
        record["pending_dispatches"][0]["created_at"] -= 600  # older than the preflight window
        marker.write_text(json.dumps(record), encoding="utf-8")
        stop = {"hook_event_name": "Stop", "session_id": start["session_id"]}
        self.assertIsNone(run_launch_guard(stop, active=None))
        self.assertFalse(marker.exists())

    def test_stop_keeps_a_lease_while_its_dispatch_is_running(self) -> None:
        dispatch_id = str(uuid.uuid4())
        state = Path(hook_env()["XDG_STATE_HOME"]) / "pitwall" / "agents"
        (state / "launches" / dispatch_id).mkdir(parents=True)
        (state / "launches" / dispatch_id / "launcher.json").write_text(
            json.dumps({"pid": os.getpid()}), encoding="utf-8"
        )
        try:
            start = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "q38-cline"})
            start["tool_use_id"] = "toolu_live"
            self.assertIsNone(run_launch_guard(start, active=False))
            still = {
                "hook_event_name": "PostToolUse",
                "session_id": start["session_id"],
                "tool_name": start["tool_name"],
                "tool_use_id": "toolu_live",
                "tool_response": json.dumps({"event": "still_running", "dispatch_id": dispatch_id}),
            }
            self.assertIsNone(run_launch_guard(still, active=None))
            self.assertIsNone(
                run_launch_guard(
                    {"hook_event_name": "Stop", "session_id": start["session_id"]},
                    active=None,
                )
            )
            marker = Path(_HOOK_HOME) / "routing-markers" / f"{start['session_id']}.json"
            self.assertTrue(marker.exists())
            (state / "runs" / dispatch_id).mkdir(parents=True)
            (state / "runs" / dispatch_id / "result.json").write_text("{}", encoding="utf-8")
            self.assertIsNone(
                run_launch_guard(
                    {"hook_event_name": "Stop", "session_id": start["session_id"]},
                    active=None,
                )
            )
            self.assertFalse(marker.exists())
        finally:
            shutil.rmtree(state / "launches" / dispatch_id)

    def test_ask_then_answer_maps_terminal_to_the_original_dispatch(self) -> None:
        dispatch = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "codex"})
        dispatch["tool_use_id"] = "toolu_dispatch"
        self.assertIsNone(run_launch_guard(dispatch, active=False))

        ask = {
            "hook_event_name": "PostToolUse",
            "session_id": dispatch["session_id"],
            "tool_name": dispatch["tool_name"],
            "tool_use_id": dispatch["tool_use_id"],
            "tool_response": {"event": "ask", "dispatch_id": "dispatch-ask"},
        }
        self.assertIsNone(run_launch_guard(ask, active=None))
        marker = Path(_HOOK_HOME) / "routing-markers" / f"{dispatch['session_id']}.json"
        record = json.loads(marker.read_text(encoding="utf-8"))
        leases = record["pending_dispatches"]
        self.assertEqual(1, len(leases))
        self.assertEqual("toolu_dispatch", leases[0]["tool_use_id"])
        self.assertEqual("dispatch-ask", leases[0]["dispatch_id"])
        self.assertIsInstance(leases[0].get("created_at"), (int, float))
        self.assertEqual(
            [{"tool_use_id": "toolu_dispatch", "dispatch_id": "dispatch-ask"}],
            [
                {k: v for k, v in lease.items() if k != "created_at"}
                for lease in record["pending_dispatches"]
            ],
        )

        answer = {
            "hook_event_name": "PostToolUse",
            "session_id": dispatch["session_id"],
            "tool_name": "mcp__pitwall-channel__answer_and_wait",
            "tool_use_id": "toolu_answer",
            "tool_input": {"dispatch_id": "dispatch-ask", "ask_id": "0001", "choice": "yes"},
            "tool_response": {"event": "terminal", "dispatch_id": "dispatch-ask"},
        }
        self.assertIsNone(run_launch_guard(answer, active=None))
        self.assertFalse(marker.exists())

    def test_out_of_order_answer_terminals_preserve_the_other_sibling(self) -> None:
        first = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "codex"})
        first["tool_use_id"] = "toolu_first"
        second = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "gemini"})
        second["tool_use_id"] = "toolu_second"
        self.assertIsNone(run_launch_guard(first, active=False))
        self.assertIsNone(run_launch_guard(second, active=None))

        for dispatch, dispatch_id in ((first, "dispatch-first"), (second, "dispatch-second")):
            ask = {
                "hook_event_name": "PostToolUse",
                "session_id": dispatch["session_id"],
                "tool_name": dispatch["tool_name"],
                "tool_use_id": dispatch["tool_use_id"],
                "tool_response": {"event": "ask", "dispatch_id": dispatch_id},
            }
            self.assertIsNone(run_launch_guard(ask, active=None))

        marker = Path(_HOOK_HOME) / "routing-markers" / f"{first['session_id']}.json"
        second_answer = {
            "hook_event_name": "PostToolUse",
            "session_id": first["session_id"],
            "tool_name": "mcp__pitwall-channel__answer_and_wait",
            "tool_use_id": "toolu_answer_second",
            "tool_input": {"dispatch_id": "dispatch-second", "ask_id": "0001", "choice": "yes"},
            "tool_response": {"event": "terminal", "dispatch_id": "dispatch-second"},
        }
        self.assertIsNone(run_launch_guard(second_answer, active=None))
        record = json.loads(marker.read_text(encoding="utf-8"))
        remaining = record["pending_dispatches"]
        self.assertEqual(1, len(remaining))
        self.assertEqual("toolu_first", remaining[0]["tool_use_id"])
        self.assertEqual("dispatch-first", remaining[0]["dispatch_id"])
        self.assertIsInstance(remaining[0].get("created_at"), (int, float))
        self.assertEqual(
            [{"tool_use_id": "toolu_first", "dispatch_id": "dispatch-first"}],
            [
                {k: v for k, v in lease.items() if k != "created_at"}
                for lease in record["pending_dispatches"]
            ],
        )

        first_answer = dict(second_answer)
        first_answer["tool_use_id"] = "toolu_answer_first"
        first_answer["tool_input"] = {
            "dispatch_id": "dispatch-first",
            "ask_id": "0001",
            "choice": "yes",
        }
        first_answer["tool_response"] = {"event": "terminal", "dispatch_id": "dispatch-first"}
        self.assertIsNone(run_launch_guard(first_answer, active=None))
        self.assertFalse(marker.exists())

    def test_failed_dispatch_releases_its_lease(self) -> None:
        dispatch = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "codex"})
        dispatch["tool_use_id"] = "toolu_failed"
        self.assertIsNone(run_launch_guard(dispatch, active=False))
        failed = {
            "hook_event_name": "PostToolUseFailure",
            "session_id": dispatch["session_id"],
            "tool_name": dispatch["tool_name"],
            "tool_use_id": dispatch["tool_use_id"],
            "error": "harness unavailable",
        }
        self.assertIsNone(run_launch_guard(failed, active=None))
        stop = {"hook_event_name": "Stop", "session_id": dispatch["session_id"]}
        self.assertIsNone(run_launch_guard(stop, active=None))
        marker = Path(_HOOK_HOME) / "routing-markers" / f"{dispatch['session_id']}.json"
        self.assertFalse(marker.exists())

    def test_post_tool_use_is_error_releases_its_dispatch_lease(self) -> None:
        first = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "codex"})
        first["tool_use_id"] = "toolu_error"
        second = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "gemini"})
        second["tool_use_id"] = "toolu_live"
        self.assertIsNone(run_launch_guard(first, active=False))
        self.assertIsNone(run_launch_guard(second, active=None))

        failed = {
            "hook_event_name": "PostToolUse",
            "session_id": first["session_id"],
            "tool_name": first["tool_name"],
            "tool_use_id": first["tool_use_id"],
            "tool_response": {
                "is_error": True,
                "content": [{"type": "text", "text": "harness unavailable"}],
            },
        }
        self.assertIsNone(run_launch_guard(failed, active=None))
        marker = Path(_HOOK_HOME) / "routing-markers" / f"{first['session_id']}.json"
        record = json.loads(marker.read_text(encoding="utf-8"))
        live_lease = record["pending_dispatches"]
        self.assertEqual(1, len(live_lease))
        self.assertEqual("toolu_live", live_lease[0]["tool_use_id"])
        self.assertIsInstance(live_lease[0].get("created_at"), (int, float))
        self.assertEqual(
            [{"tool_use_id": "toolu_live"}],
            [
                {k: v for k, v in lease.items() if k != "created_at"}
                for lease in record["pending_dispatches"]
            ],
        )

        blocked = run_launch_guard(
            self._payload("Bash", {"command": "~/.claude/scripts/codex-shim.sh prompt.md"}),
            active=None,
        )
        self.assertEqual("deny", blocked["hookSpecificOutput"]["permissionDecision"])

        failed_second = dict(failed)
        failed_second["tool_use_id"] = second["tool_use_id"]
        failed_second["tool_response"] = {"isError": True, "content": []}
        self.assertIsNone(run_launch_guard(failed_second, active=None))
        self.assertFalse(marker.exists())

    def test_failed_call_without_a_matching_identity_keeps_all_siblings(self) -> None:
        first = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "codex"})
        first["tool_use_id"] = "toolu_first"
        second = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "gemini"})
        second["tool_use_id"] = "toolu_second"
        self.assertIsNone(run_launch_guard(first, active=False))
        self.assertIsNone(run_launch_guard(second, active=None))
        failed = {
            "hook_event_name": "PostToolUseFailure",
            "session_id": first["session_id"],
            "tool_name": first["tool_name"],
            "tool_use_id": "toolu_unknown",
            "tool_input": {"dispatch_id": "dispatch-first"},
            "error": "harness unavailable",
        }
        self.assertIsNone(run_launch_guard(failed, active=None))
        marker = Path(_HOOK_HOME) / "routing-markers" / f"{first['session_id']}.json"
        record = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(
            ["toolu_first", "toolu_second"],
            [lease["tool_use_id"] for lease in record["pending_dispatches"]],
        )
        for lease in record["pending_dispatches"]:
            self.assertIsInstance(lease.get("created_at"), (int, float))
        self.assertEqual(
            [{"tool_use_id": "toolu_first"}, {"tool_use_id": "toolu_second"}],
            [
                {k: v for k, v in lease.items() if k != "created_at"}
                for lease in record["pending_dispatches"]
            ],
        )

        terminal_unknown = {
            "hook_event_name": "PostToolUse",
            "session_id": first["session_id"],
            "tool_name": first["tool_name"],
            "tool_use_id": "toolu_unknown",
            "tool_response": {"event": "terminal", "dispatch_id": "dispatch_unknown"},
        }
        self.assertIsNone(run_launch_guard(terminal_unknown, active=None))
        record = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(
            ["toolu_first", "toolu_second"],
            [lease["tool_use_id"] for lease in record["pending_dispatches"]],
        )
        for lease in record["pending_dispatches"]:
            self.assertIsInstance(lease.get("created_at"), (int, float))
        self.assertEqual(
            [{"tool_use_id": "toolu_first"}, {"tool_use_id": "toolu_second"}],
            [
                {k: v for k, v in lease.items() if k != "created_at"}
                for lease in record["pending_dispatches"]
            ],
        )

    def test_managed_dispatch_activates_the_session_marker(self) -> None:
        dispatch = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "codex"})
        self.assertIsNone(run_launch_guard(dispatch, active=False))
        blocked = run_launch_guard(
            self._payload("Bash", {"command": "~/.claude/scripts/codex-shim.sh prompt.md"})
        )
        self.assertEqual("deny", blocked["hookSpecificOutput"]["permissionDecision"])

    def test_opaque_wrappers_are_outside_detection_boundary(self) -> None:
        self.assertIsNone(
            run_launch_guard(self._payload("Bash", {"command": "./my-harness-wrapper prompt.md"}))
        )

    def test_inactive_session_preserves_unrelated_launches(self) -> None:
        self.assertIsNone(
            run_launch_guard(
                self._payload("Bash", {"command": "~/.claude/scripts/codex-shim.sh prompt.md"}),
                active=False,
            )
        )

    def test_malformed_or_non_pretool_input_fails_open(self) -> None:
        self.assertIsNone(
            run_launch_guard(
                {"tool_name": "Bash", "tool_input": {"command": "codex"}}, active=False
            )
        )


if __name__ == "__main__":
    unittest.main()


class LedgerOutcomePolicyTests(unittest.TestCase):
    """Ledger tripwire: heredoc prose is not a dispatch; nudge only on non-ok shim outcomes."""

    def _with_ledger(self, records):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as tmp:
            for r in records:
                tmp.write(json.dumps(r) + "\n")
        self.addCleanup(os.unlink, tmp.name)
        return mock.patch.dict(os.environ, {"PITWALL_AGENTS_LEDGER": tmp.name})

    def test_heredoc_prose_mentioning_shim_does_not_trip(self) -> None:
        cmd = "cat > notes.md <<'EOF'\nUse `codex-shim.sh --ping` before assuming an outage.\nscripts/*-shim.sh writes the ledger.\nEOF\nwc -l notes.md"
        bad = {
            "source": "shim",
            "event": "finished",
            "outcome": "error",
            "shim": "codex",
            "model": "m",
            "exit": 1,
            "ts": "2099-01-01T00:00:00",
        }
        with self._with_ledger([bad]):
            self.assertIsNone(
                run_hook(LEDGER_TRIPWIRE, [user_entry("write notes"), bash_entry(cmd)])
            )

    def test_real_dispatch_with_error_outcome_blocks(self) -> None:
        bad = {
            "source": "shim",
            "event": "finished",
            "outcome": "error",
            "shim": "codex",
            "model": "m",
            "exit": 1,
            "ts": "2099-01-01T00:00:00",
        }
        with self._with_ledger([bad]):
            out = run_hook(
                LEDGER_TRIPWIRE,
                [
                    user_entry("route this"),
                    bash_entry("bash ~/.claude/scripts/codex-shim.sh prompt.md"),
                ],
            )
        self.assertIsNotNone(out)
        self.assertIn("non-ok outcome", str(out["reason"]))

    def test_real_dispatch_with_ok_outcome_is_silent(self) -> None:
        ok = {
            "source": "shim",
            "event": "finished",
            "outcome": "ok",
            "shim": "codex",
            "model": "m",
            "exit": 0,
            "ts": "2099-01-01T00:00:00",
        }
        with self._with_ledger([ok]):
            self.assertIsNone(
                run_hook(
                    LEDGER_TRIPWIRE,
                    [
                        user_entry("route this"),
                        bash_entry("bash ~/.claude/scripts/codex-shim.sh prompt.md"),
                    ],
                )
            )
