"""Release journeys J39-J41: routes and doctor, every shim, and the channel and workflows.

J39 drives the routes lifecycle through the real launcher in a temporary HOME against a
loopback OpenAI-compatible server. J40 runs each of the fourteen shims (thirteen harness
shims plus ``route-shim``) against a fake harness binary and pins the exact argv the
adapter hands it, the ``SHIM-DONE`` sentinel, the ``SHIM-RESULT`` receipt as read by
``pitwall.agents.result.parse_shim_receipt``, and a failing exit. J41 carries the scripted channel and
workflow exits, runs a Pi workflow against a fake ``pi``, refuses a workflow on a
non-workflow harness, and moves the mailbox with ``inbox``, ``answer``, ``steer``, and
``runs stop``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from pitwall.agents.channel import ChannelConfig, write_channel_config
from pitwall.agents.result import parse_shim_receipt
from pitwall.agents.run_store import (
    RunStore,
)
from tests.agents.http_test_support import (
    LoopbackServer,
)
from tests.agents.shim_test_support import (
    PITWALL,
    SHIMS,
    ShimSandbox,
)

# The scripted channel and workflow exits run as part of J41.
from tests.agents.test_channel_steer_integration import (
    SteerIntegrationTests,  # noqa: F401  # reason: re-exported so unittest discovers the classes
)
from tests.agents.test_channel_tier1_integration import (
    Tier1IntegrationTests,  # noqa: F401  # reason: re-exported so unittest discovers the classes
)
from tests.agents.test_workflow_channel_e2e import (
    WorkflowChannelEndToEndTests,  # noqa: F401  # reason: re-exported so unittest discovers the classes
)

ROOT = Path(__file__).resolve().parents[2]

PROMPT = "journey prompt\n"
TEXT = PROMPT.rstrip("\n")


def _launch(
    env: dict[str, str], *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PITWALL), "agents", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


class J39ProfilesJourney(unittest.TestCase):
    """Add, list, probe, doctor, replace, refuse, and remove one endpoint route."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        home = Path(self._temp.name)
        self.env = {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_STATE_HOME": str(home / "state"),
            "PATH": "/usr/bin:/bin",
            "NO_PROXY": "127.0.0.1,localhost",
            "JOURNEY_KEY": "journey-token",
        }

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _list(self) -> list[dict[str, object]]:
        listed = _launch(self.env, "profiles", "list", "--json")
        self.assertEqual(0, listed.returncode, listed.stderr)
        return json.loads(listed.stdout)

    def test_route_lifecycle(self) -> None:
        with LoopbackServer({"/v1/models": (200, {"data": [{"id": "journey-model"}]})}) as server:
            base = server.base_url + "/v1"
            add = ("profiles", "add", "jr", "--base-url", base, "--api-key-env", "JOURNEY_KEY")
            added = _launch(self.env, *add, "--model", "journey-model")
            self.assertEqual(0, added.returncode, added.stderr)
            self.assertIn("saved jr to", added.stdout)
            [route] = self._list()
            self.assertEqual(
                ("jr", "journey-model", "ok"), (route["name"], route["model"], route["status"])
            )
            self.assertEqual(base.split("//")[1].removesuffix("/v1"), route["endpointHost"])
            stored = (Path(self.env["XDG_CONFIG_HOME"]) / "pitwall" / "pitwall.toml").read_text()
            self.assertNotIn("journey-token", stored)  # agent profiles keep the variable name only

            probe = _launch(self.env, "profiles", "probe", "jr", "--json")
            self.assertEqual(0, probe.returncode, probe.stderr)
            probed = json.loads(probe.stdout)
            self.assertEqual(
                ("reachable", 200, ["journey-model"]),
                (probed["status"], probed["http_status"], probed["models"]),
            )

            doctor = _launch(self.env, "doctor", "--probe-routes", "--json")
            report = json.loads(doctor.stdout)
            checks = {check["id"]: check for check in report["checks"]}
            self.assertEqual("PASS", checks["routes.jr.probe"]["status"], checks["routes.jr.probe"])
            self.assertEqual(0, report["summary"]["fail"])

            replaced = _launch(self.env, *add, "--model", "journey-model-2")
            self.assertEqual(0, replaced.returncode, replaced.stderr)
            self.assertEqual(["journey-model-2"], [row["model"] for row in self._list()])

            refused = _launch(self.env, "profiles", "add", "jr", "--from-pitwall", "llm.journey")
            self.assertEqual(2, refused.returncode)
            self.assertIn("route 'jr' already exists", refused.stderr)
            self.assertEqual(["journey-model-2"], [row["model"] for row in self._list()])

        removed = _launch(self.env, "profiles", "remove", "jr")
        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertIn("removed jr", removed.stdout)
        self.assertEqual([], self._list())


# The exact argv each adapter hands its harness for `<shim> <prompt> -m journey-model`.
# "{prompt}" is the prompt file path; "{cwd}" is the dispatching directory; stdin-delivered
# prompts are checked separately.
ARGV = {
    "agy": [
        "--effort",
        "medium",
        "--add-dir",
        "{cwd}",
        "--dangerously-skip-permissions",
        "-m",
        "journey-model",
        "--print-timeout",
        "1200s",
        "--output-format",
        "text",
        "-p",
        TEXT,
    ],
    "claude": [
        "-p",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "-m",
        "journey-model",
        "--output-format",
        "text",
    ],
    "cline": ["-m", "journey-model", "--auto-approve", "true", TEXT],
    "codex": [
        "exec",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "-m",
        "journey-model",
    ],
    "dsh": ["--profile", "headless", "-m", "journey-model", TEXT],
    "goose": [
        "run",
        "--no-session",
        "-q",
        "--output-format",
        "text",
        "-m",
        "journey-model",
        "--instructions",
        "-",
    ],
    "grok": [
        "--no-auto-update",
        "--no-alt-screen",
        "--always-approve",
        "-m",
        "journey-model",
        "--output-format",
        "plain",
        "--prompt-file",
        "{delivery}",
    ],
    "hermes": ["-m", "journey-model", "--yolo", "-z", TEXT],
    "kimi": ["-m", "journey-model", "--output-format", "text", "--prompt", TEXT],
    "muse": ["exec", "--yolo", "-m", "journey-model", "--prompt-file", "{prompt}"],
    "opencode": ["run", "-m", "test-harness/journey-model"],
    "pi": ["-p", "--no-session", "--approve", "-m", "journey-model", "@{delivery}"],
    "qwen": ["-m", "journey-model", "--yolo", "--output-format", "text", "--prompt", TEXT],
    "zcode": [
        "--cwd",
        "{cwd}",
        "--mode",
        "yolo",
        "--no-color",
        "--output-format",
        "text",
        "--prompt",
        TEXT,
    ],
}
STDIN_PROMPT = {"claude", "codex", "goose", "opencode"}
# The effort flag passes through to every harness that takes one, in place of any default.
EFFORT_TAIL = {
    "agy": ["--effort", "high", "--print-timeout"],
    "claude": ["--effort", "high", "--output-format"],
}


def _shim_args(shim: str, prompt: Path, *extra: str) -> list[str]:
    if shim == "zcode":
        return [str(prompt), *extra]
    if shim == "opencode":
        return ["test-harness/journey-model", str(prompt), *extra]
    return [str(prompt), "-m", "journey-model", *extra]


class J40ShimsJourney(unittest.TestCase):
    """Every shim: argv shape, sentinel, parsed receipt, and a failing exit."""

    def setUp(self) -> None:
        self.sandboxes: list[ShimSandbox] = []

    def tearDown(self) -> None:
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def _sandbox(self, harness: str) -> ShimSandbox:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        sandbox.install_harness(harness)
        return sandbox

    def _receipt(self, stdout: bytes) -> dict[str, object]:
        return parse_shim_receipt(stdout.decode("utf-8"))

    def test_every_harness_shim(self) -> None:
        self.assertEqual(sorted(ARGV), sorted(SHIMS))
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self._sandbox(shim)
                prompt = sandbox.prompt(PROMPT)
                result = sandbox.run(
                    shim,
                    _shim_args(shim, prompt),
                    env=sandbox.environment(SHIM_RESULT="1", PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("SHIM-DONE exit=0", result.stdout.decode().splitlines()[-1])
                expected = [
                    part.replace("{prompt}", str(prompt)).replace("{cwd}", os.getcwd())
                    for part in ARGV[shim]
                ]
                captured = sandbox.captured_args()
                if shim in {"pi", "grok"}:
                    # The prompt travels as a private file in the run directory, never in argv.
                    self.assertRegex(captured[-1], r"^@?.*/runs/[^/]+/prompt\.deliver\.md$")
                    expected = [
                        captured[-1] if part in {"@{delivery}", "{delivery}"} else part
                        for part in expected
                    ]
                self.assertEqual(expected, captured)
                if shim in STDIN_PROMPT:
                    self.assertEqual(PROMPT.encode(), sandbox.captured_stdin())
                receipt = self._receipt(result.stdout)
                self.assertEqual(
                    (0, "ok", "finished"), (receipt["exit"], receipt["outcome"], receipt["event"])
                )
                self.assertEqual(sandbox.ledger_records()[-1], receipt)

                effort = self._sandbox(shim)
                prompt = effort.prompt(PROMPT)
                effort_result = effort.run(
                    shim,
                    _shim_args(shim, prompt, "--effort", "high"),
                    env=effort.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                if shim == "zcode":
                    self.assertEqual(64, effort_result.returncode)
                    self.assertFalse(effort.args_file.exists())
                else:
                    self.assertEqual(0, effort_result.returncode)
                    args = effort.captured_args()
                    tail = EFFORT_TAIL.get(shim, ["--effort", "high"])
                    self.assertTrue(
                        any(args[i : i + len(tail)] == tail for i in range(len(args))), args
                    )
                    self.assertEqual(1, args.count("--effort"), args)

                failing = self._sandbox(shim)
                prompt = failing.prompt(PROMPT)
                env = failing.environment(
                    SHIM_RESULT="1", FAKE_EXIT="3", PITWALL_AGENTS_UNRESTRICTED="1"
                )
                result = failing.run(shim, _shim_args(shim, prompt), env=env)
                self.assertEqual(3, result.returncode)
                self.assertEqual("SHIM-DONE exit=3", result.stdout.decode().splitlines()[-1])
                self.assertEqual(
                    (3, "error"),
                    (self._receipt(result.stdout)["exit"], self._receipt(result.stdout)["outcome"]),
                )

    def test_route_shim(self) -> None:
        routes = {
            "schemaVersion": 1,
            "models": {
                "jr": {
                    "model": "journey-model",
                    "harness": "qwen",
                    "endpoint": {"baseUrl": "http://127.0.0.1:9/v1", "apiKeyEnv": "JOURNEY_KEY"},
                }
            },
        }
        for exit_code in (0, 3):
            with self.subTest(exit=exit_code):
                sandbox = self._sandbox("qwen")
                sandbox.write_routes(routes)
                prompt = sandbox.prompt(PROMPT)
                env = sandbox.environment(
                    JOURNEY_KEY="journey-token",
                    SHIM_RESULT="1",
                    FAKE_EXIT=str(exit_code),
                    PITWALL_AGENTS_UNRESTRICTED="1",
                )
                result = sandbox.run_route(["jr", str(prompt)], env=env)
                self.assertEqual(exit_code, result.returncode, result.stderr)
                self.assertEqual(
                    f"SHIM-DONE exit={exit_code}", result.stdout.decode().splitlines()[-1]
                )
                self.assertIn("route-shim: jr -> qwen journey-model", result.stderr.decode())
                self.assertEqual(
                    ["-m", "journey-model", "--yolo", "--output-format", "text", "--prompt", TEXT],
                    sandbox.captured_args(),
                )
                receipt = self._receipt(result.stdout)
                self.assertEqual(("jr", exit_code), (receipt["route"], receipt["exit"]))


class J41ChannelAndWorkflowsJourney(unittest.TestCase):
    """A Pi workflow runs, a non-workflow harness is refused, and the mailbox verbs act."""

    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.sandbox.install_harness("pi")
        self.repo = self.sandbox.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.env = self.sandbox.environment(
            PYTHONPATH=str(ROOT / "src"), PITWALL_AGENTS_UNRESTRICTED="1"
        )

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _workflow(self, harness: str) -> str:
        name = f"{harness}.json"
        (self.repo / name).write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "name": f"j41-{harness}",
                    "tasks": {
                        "one": {
                            "route": {"provider": harness, "model": "journey-model"},
                            "mode": "read",
                            "prompt": {"text": "journey task"},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        return name

    def test_pi_workflow_runs_against_a_fake_pi(self) -> None:
        result = _launch(
            self.env, "workflow", "run", self._workflow("pi"), "--host", "copilot", cwd=self.repo
        )
        self.assertEqual(0, result.returncode, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(
            ("succeeded", "succeeded"), (state["status"], state["tasks"]["one"]["state"])
        )
        captured = self.sandbox.captured_args()
        self.assertRegex(captured[-1], r"^@.*/runs/[^/]+/prompt\.deliver\.md$")
        self.assertEqual(
            ["-p", "--no-session", "--approve", "--model", "journey-model", captured[-1]], captured
        )
        listed = json.loads(_launch(self.env, "workflow", "list", "--json").stdout)
        self.assertEqual(
            [(state["workflowId"], "succeeded")],
            [(row["workflowId"], row["status"]) for row in listed],
        )

    def test_model_bound_harness_rejects_unbound_model_before_any_run(self) -> None:
        result = _launch(
            self.env, "workflow", "run", self._workflow("muse"), "--host", "copilot", cwd=self.repo
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("is not a model of model-bound harness 'muse'", result.stderr)
        self.assertEqual([], self.sandbox.captured_args())
        self.assertEqual([], json.loads(_launch(self.env, "workflow", "list", "--json").stdout))

    def test_inbox_answer_steer_and_stop_move_the_mailbox(self) -> None:
        dispatch_id = "00000000-0000-4000-8000-0000000000d1"
        store = RunStore(Path(self.env["XDG_STATE_HOME"]) / "pitwall" / "agents", dispatch_id)
        store.path.mkdir(parents=True)
        store.write_json(
            "run.json",
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "state": "running",
                "provider": "pi",
                "model": "journey-model",
                "attempt": 1,
            },
        )
        write_channel_config(
            store,
            ChannelConfig(dispatch_id, tier="1", attempt_started_epoch=time.time()),
        )
        store.mailbox().write_ask(
            blocked_on="choice",
            question="Which first?",
            options=[{"id": "a", "text": "0031"}, {"id": "b", "text": "0032"}],
            default="a",
            deadline_s=600,
        )

        inbox = _launch(self.env, "inbox")
        self.assertEqual(0, inbox.returncode, inbox.stderr)
        self.assertIn(dispatch_id, inbox.stdout)
        self.assertRegex(inbox.stdout, r"ASK\t0001\t\d+m")

        answered = _launch(self.env, "answer", dispatch_id, "0001", "b")
        self.assertEqual(0, answered.returncode, answered.stderr)
        answer = store.mailbox().get_answer("0001")
        assert answer is not None
        self.assertEqual(("b", "operator"), (answer["choice"], answer["answered_by"]))
        self.assertNotRegex(_launch(self.env, "inbox").stdout, r"ASK\t0001")

        steered = _launch(
            self.env, "steer", dispatch_id, "--kind", "scope", "--message", "SQLite only", "--json"
        )
        self.assertEqual(0, steered.returncode, steered.stderr)
        self.assertEqual("0001", json.loads(steered.stdout)["steer_id"])
        self.assertIn("SQLite only", _launch(self.env, "inbox").stdout)

        stopped = _launch(self.env, "runs", "stop", dispatch_id, "--grace", "5")
        self.assertEqual(0, stopped.returncode, stopped.stderr)
        self.assertIn("stop requested: steer 0002", stopped.stdout)
        steers = store.mailbox().steers()
        self.assertEqual(
            [("scope", "SQLite only"), ("stop", "stop: wrap up the current work and report")],
            [(steer["kind"], steer["message"]) for steer in steers],
        )
        self.assertEqual(5, steers[1]["deadline_s"])


if __name__ == "__main__":
    unittest.main()
