"""Tests for the local, non-discovery doctor report."""

from __future__ import annotations

import contextlib
import gc
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from pitwall.agents import (
    cli,
    doctor,
)
from pitwall.agents.harnesses import adapter_ids
from pitwall.agents.installation import (
    shim_names,
    shim_script,
)
from tests.agents.component_tree import copy_component
from tests.agents.http_test_support import (
    LoopbackServer,
)
from tests.agents.profiles_fixture import write_profiles
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]


class DoctorTests(unittest.TestCase):
    def environment(
        self, root: Path, *, harnesses: bool = True, exclude: tuple[str, ...] = ()
    ) -> dict[str, str]:
        home = root / "home"
        binary_dir = root / "bin"
        home.mkdir(parents=True, exist_ok=True)
        binary_dir.mkdir(parents=True, exist_ok=True)
        if harnesses:
            # Every adapter id gets a fake binary so a harness CLI installed on
            # the host machine can never leak into these checks via the real PATH.
            for name in sorted(adapter_ids()):
                if name in exclude:
                    continue
                path = binary_dir / name
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                path.chmod(0o755)
        return {
            "HOME": str(home),
            "PATH": f"{binary_dir}:{os.environ.get('PATH', '')}",
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CONFIG_HOME": str(root / "config"),
        }

    @staticmethod
    def completed(
        argv: list[str], *, help_complete: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        if argv[-1] == "--version":
            output = f"{Path(argv[0]).name} 1.0\n".encode()
        elif "--help" in argv:
            # Every flag any harness's contract requires, so the derived fake
            # help satisfies PROVIDER_HELP for all adapter ids.
            flags = " ".join(
                sorted(
                    {
                        token
                        for _args, required in doctor.HARNESS_HELP.values()
                        for token in required
                    }
                )
            )
            output = (flags if help_complete else "minimal help").encode()
        else:
            output = b"authenticated\n"
        return subprocess.CompletedProcess(argv, 0, output, b"")

    def routes_report(
        self,
        root: Path,
        data: dict[str, object] | None,
        *,
        harness: str | None = None,
        extra_env: dict[str, str] | None = None,
        exclude: tuple[str, ...] = (),
        probe_routes: bool = False,
    ) -> dict[str, object]:
        env = self.environment(root, exclude=exclude)
        env.update(extra_env or {})
        if data is not None:
            target = write_profiles(Path(env["XDG_CONFIG_HOME"]) / "pitwall" / "pitwall.toml", data)
            target.chmod(0o600)
        with mock.patch.object(
            doctor.subprocess, "run", side_effect=lambda argv, **_: self.completed(argv)
        ):
            return doctor.run_doctor(ROOT, env, harness=harness, probe_routes=probe_routes)

    def write_pitwall_sync(self, root: Path, data: dict[str, object]) -> None:
        target = root / "state" / "pitwall" / "agents" / "pitwall-sync.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
        target.chmod(0o600)

    @staticmethod
    def check(report: dict[str, object], check_id: str) -> dict[str, object]:
        return next(check for check in report["checks"] if check["id"] == check_id)  # type: ignore[index,union-attr]  # reason: the test navigates a loosely typed JSON document

    def test_routes_checks_skip_without_config_and_report_each_route(self) -> None:
        data = {
            "schemaVersion": 1,
            "models": {
                "glimmer": {
                    "model": "meta-models/Muse-Glimmer-30B",
                    "endpoint": {"baseUrl": "http://gpu-1:8000/v1", "apiKeyEnv": "GLIMMER_API_KEY"},
                },
                "glm": {
                    "model": "zai-coding-plan/glm-5.3",
                    "endpoint": {"baseUrl": "http://gpu-2:8000/v1"},
                    "harness": "opencode",
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                "SKIP",
                self.check(self.routes_report(root, None), "runtime.routes_config")["status"],
            )
            report = self.routes_report(root, data)
            self.assertEqual("PASS", self.check(report, "runtime.routes_config")["status"])
            self.assertEqual(
                "PASS", self.check(report, "routes.glimmer.harness_installed")["status"]
            )
            self.assertEqual(
                "qwen", self.check(report, "routes.glimmer.harness_installed")["provider"]
            )
            self.assertEqual("WARN", self.check(report, "routes.glimmer.api_key_env")["status"])
            sync = self.check(report, "routes.glm.sync_status")
            self.assertEqual("WARN", sync["status"])
            self.assertIn("profiles sync --harness opencode", sync["remediation"])
            self.assertEqual("PASS", self.check(report, "security.routes_file_mode")["status"])
            keyed = self.routes_report(root, data, extra_env={"GLIMMER_API_KEY": "k"})
            self.assertEqual("PASS", self.check(keyed, "routes.glimmer.api_key_env")["status"])
            filtered = self.routes_report(root, data, harness="qwen")
            ids = [check["id"] for check in filtered["checks"]]  # type: ignore[index,union-attr]  # reason: the test navigates a loosely typed JSON document
            self.assertIn("routes.glimmer.harness_installed", ids)
            self.assertNotIn("routes.glm.sync_status", ids)

    def test_route_probes_run_only_with_explicit_flag(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            LoopbackServer(
                {
                    "/v1/models": (200, {"data": [{"id": "m"}]}),
                    "/running": (200, {"running": [{"model": "m", "state": "starting"}]}),
                }
            ) as server,
        ):
            root = Path(directory)
            data = {
                "schemaVersion": 1,
                "models": {
                    "local": {"model": "m", "endpoint": {"baseUrl": server.base_url + "/v1"}},
                    "missing": {"model": "other", "endpoint": {"baseUrl": server.base_url + "/v1"}},
                    "native": {"model": "gpt-5.6-sol"},
                },
            }
            offline = self.routes_report(root, data)
            self.assertEqual([], server.requests)
            self.assertNotIn("routes.local.probe", [check["id"] for check in offline["checks"]])  # type: ignore[index,union-attr]  # reason: the test navigates a loosely typed JSON document

            probed = self.routes_report(root, data, probe_routes=True)
            check = self.check(probed, "routes.local.probe")
            self.assertEqual("PASS", check["status"])
            self.assertIn("warming", check["summary"])
            self.assertEqual(
                "the model is loading; retry shortly — cold starts on swapper endpoints commonly take 1–2 minutes",
                check["remediation"],
            )
            missing = self.check(probed, "routes.missing.probe")
            self.assertEqual("WARN", missing["status"])
            self.assertEqual(
                "serve the route's model id on the endpoint or change the route's model",
                missing["remediation"],
            )
            self.assertNotIn("routes.native.probe", [item["id"] for item in probed["checks"]])  # type: ignore[index,union-attr]  # reason: the test navigates a loosely typed JSON document

    def test_doctor_cli_accepts_probe_routes_flag(self) -> None:
        report = {
            "checks": [],
            "status": "pass",
            "summary": {"pass": 0, "warn": 0, "fail": 0, "skip": 0},
        }
        with (
            mock.patch.object(cli, "run_doctor", return_value=report) as run,
            mock.patch("sys.stdout"),
        ):
            self.assertEqual(0, cli.main(["doctor", "--probe-routes", "--json"]))
        self.assertTrue(run.call_args.kwargs["probe_routes"])

    def test_harness_summary_lists_installed_and_warns_on_routes_pinning_missing_harnesses(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # A hermetic PATH (fake bin dir only) so the machine's real installs
            # cannot resurrect the deliberately excluded harness.
            hermetic = {"PATH": str(root / "bin")}
            report = self.routes_report(
                root,
                {"schemaVersion": 1, "models": {"sol": {"model": "gpt-5.6-sol"}}},
                exclude=("pi",),
                extra_env=hermetic,
            )
            check = self.check(report, "harness.summary")
            self.assertEqual("PASS", check["status"])
            self.assertIn("codex", check["details"]["installed"])
            self.assertIn("pi", check["details"]["missing"])
            checks = report["checks"]
            assert isinstance(checks, list)
            summary_index = checks.index(check)
            later_check_indexes = [
                index
                for index, candidate in enumerate(checks)
                if candidate["id"].startswith("routes.")
                or (candidate["id"].startswith("harness.") and candidate["id"] != "harness.summary")
            ]
            self.assertTrue(all(summary_index < index for index in later_check_indexes))
            pinned = self.routes_report(
                root,
                {"schemaVersion": 1, "models": {"p": {"model": "x/y", "harness": "pi"}}},
                exclude=("pi",),
                extra_env=hermetic,
            )
            warned = self.check(pinned, "harness.summary")
            self.assertEqual("WARN", warned["status"])
            self.assertEqual({"p": "pi"}, warned["details"]["routesOnMissing"])
            self.assertIn("setup harnesses", warned["remediation"])
            filtered = self.routes_report(root, None, harness="codex")
            self.assertNotIn("harness.summary", [c["id"] for c in filtered["checks"]])  # type: ignore[index,union-attr]  # reason: the test navigates a loosely typed JSON document

    def test_route_expiry_check_warns_when_expiring_or_expired(self) -> None:
        base = {
            "schemaVersion": 1,
            "models": {
                "g": {
                    "model": "m",
                    "endpoint": {"baseUrl": "http://h/v1"},
                    "expiresAt": "2000-01-01T00:00:00Z",
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.routes_report(root, base)
            check = self.check(report, "routes.g.expiry")
            self.assertEqual("WARN", check["status"])
            self.assertIn("expired", check["summary"])
            base["models"]["g"]["expiresAt"] = "2999-01-01T00:00:00Z"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
            self.assertEqual(
                "PASS", self.check(self.routes_report(root, base), "routes.g.expiry")["status"]
            )
            del base["models"]["g"]["expiresAt"]  # type: ignore[union-attr]  # reason: the test navigates a loosely typed JSON document
            ids = [c["id"] for c in self.routes_report(root, base)["checks"]]  # type: ignore[index,union-attr]  # reason: the test navigates a loosely typed JSON document
            self.assertNotIn("routes.g.expiry", ids)

    def test_pitwall_sync_warns_for_soon_expiring_route_with_stale_sidecar(self) -> None:
        now = datetime.now(UTC)
        data = {
            "schemaVersion": 1,
            "models": {
                "g": {
                    "model": "m",
                    "endpoint": {"baseUrl": "http://h/v1"},
                    "expiresAt": (now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "origin": {
                        "kind": "pitwall",
                        "capability": "llm.g",
                        "leaseId": "lease-1",
                        "url": "http://pitwall",
                        "state": "active",
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_pitwall_sync(
                root,
                {
                    "routes": {
                        "g": {
                            "lastUpdatedAt": (now - timedelta(minutes=31)).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "source": "refresh",
                        }
                    }
                },
            )
            stale = self.check(self.routes_report(root, data), "routes.g.pitwall_sync")
            self.assertEqual("WARN", stale["status"])
            self.assertIn("30 minutes", stale["summary"])
            self.assertIn("broker watch", stale["remediation"])

            self.write_pitwall_sync(
                root,
                {
                    "routes": {
                        "g": {
                            "lastUpdatedAt": (now - timedelta(minutes=5)).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "source": "refresh",
                        }
                    }
                },
            )
            fresh = self.check(self.routes_report(root, data), "routes.g.pitwall_sync")
            self.assertEqual("PASS", fresh["status"])

    def test_pitwall_receiver_probes_loopback_only_after_receiver_sidecar_update(self) -> None:
        data = {
            "schemaVersion": 1,
            "models": {
                "g": {
                    "model": "m",
                    "endpoint": {"baseUrl": "http://h/v1"},
                    "origin": {
                        "kind": "pitwall",
                        "capability": "llm.g",
                        "leaseId": "lease-1",
                        "url": "http://pitwall",
                        "state": "active",
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch("urllib.request.urlopen") as urlopen:
                report = self.routes_report(root, data)
                skipped = self.check(report, "pitwall.receiver")
            self.assertEqual("SKIP", skipped["status"])
            self.assertEqual("PASS", self.check(report, "routes.g.pitwall_sync")["status"])
            urlopen.assert_not_called()

            self.write_pitwall_sync(
                root,
                {
                    "routes": {
                        "g": {"lastUpdatedAt": "2026-08-28T12:00:00Z", "source": "receiver"}
                    },
                    "receiver": {"port": 18765},
                },
            )
            response = mock.MagicMock()
            response.status = 200
            response.__enter__.return_value = response
            with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
                live = self.check(self.routes_report(root, data), "pitwall.receiver")
            self.assertEqual("PASS", live["status"])
            self.assertEqual("http://127.0.0.1:18765/health", urlopen.call_args.args[0].full_url)

            with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
                down = self.check(self.routes_report(root, data), "pitwall.receiver")
            self.assertEqual("SKIP", down["status"])

    def test_routes_config_fails_on_inline_secrets_and_warns_on_loose_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.routes_report(
                root,
                {
                    "schemaVersion": 1,
                    "models": {
                        "x": {
                            "model": "a/b",
                            "endpoint": {
                                "baseUrl": "http://h/v1",
                                "apiKey": "sk",  # pragma: allowlist secret
                            },
                        }
                    },
                },
            )
            self.assertEqual("FAIL", self.check(report, "runtime.routes_config")["status"])
            target = Path(self.environment(root)["XDG_CONFIG_HOME"]) / "pitwall" / "pitwall.toml"
            write_profiles(target, {"models": {}})
            target.chmod(0o644)
            loose = self.routes_report(root, None)
            self.assertEqual("WARN", self.check(loose, "security.routes_file_mode")["status"])

    def test_install_links_require_every_public_shim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            scripts = Path(env["HOME"]) / ".claude" / "scripts"
            scripts.mkdir(parents=True)
            present = (
                "codex-shim.sh",
                "claude-shim.sh",
                "grok-shim.sh",
                "kimi-shim.sh",
                "opencode-shim.sh",
            )
            for name in present:
                (scripts / name).write_text(shim_script(name), encoding="utf-8")
                (scripts / name).chmod(0o755)
            check = doctor._check_install_links(ROOT, env)
            self.assertEqual("WARN", check["status"] if isinstance(check, dict) else check.status)
            missing = {Path(path).name for path in (check.details or {})["missing"]}
            self.assertEqual(set(shim_names()) - set(present), missing)

    def test_agy_help_contract_and_auth_skip_remediation(self) -> None:
        self.assertEqual(
            (("--help",), ("--print", "--output-format")),
            doctor.HARNESS_HELP["agy"],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            with mock.patch.object(
                doctor.subprocess,
                "run",
                side_effect=lambda argv, **_: self.completed(argv),
            ):
                report = doctor.run_doctor(ROOT, env, harness="agy")
        check = self.check(report, "harness.agy.auth_probe")
        self.assertEqual("SKIP", check["status"])
        self.assertEqual(
            "agy has no status-only probe; consult its harness setup documentation separately, "
            "then pass `--live-auth` only when you explicitly authorize one bounded inference request",
            check["remediation"],
        )
        self.assertEqual("unknown", check["details"]["readiness"])
        self.assertEqual("not requested", check["details"]["inference"])

    def test_grok_hidden_flag_proves_parser_acceptance_without_auth_or_model_calls(self) -> None:
        self.assertEqual(
            (("--no-auto-update", "--help"), ("--output-format", "--prompt-file")),
            doctor.HARNESS_HELP["grok"],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            binary = str(root / "bin" / "grok")
            visible_help = (
                "Usage: grok [OPTIONS]\n  --output-format <FORMAT>  Output format\n"
                "  --prompt-file <PATH>  Single-turn prompt from a file\n"
            )
            cases = {
                "hidden-flag-accepted": (0, visible_help, "PASS"),
                "parser-rejects-hidden-flag": (
                    2,
                    "error: unexpected argument '--no-auto-update'\n",
                    "WARN",
                ),
                "missing-visible-flag": (0, "Usage: grok [OPTIONS]\n", "WARN"),
            }
            for label, (hidden_exit, help_text, expected) in cases.items():
                with self.subTest(label=label):
                    commands: list[list[str]] = []

                    def runner(
                        argv: list[str],
                        commands: list[list[str]] = commands,
                        hidden_exit: int = hidden_exit,
                        help_text: str = help_text,
                        **_: object,
                    ) -> subprocess.CompletedProcess[bytes]:
                        commands.append(argv)
                        if argv[1] == "--version":
                            return subprocess.CompletedProcess(argv, 0, b"grok 1.0.13\n", b"")
                        self.assertEqual([binary, "--no-auto-update", "--help"], argv)
                        return subprocess.CompletedProcess(
                            argv, hidden_exit, b"", help_text.encode()
                        )

                    with mock.patch.object(doctor.subprocess, "run", side_effect=runner):
                        check = doctor._check_harness_cli_contract(ROOT, env, harness_id="grok")
                    self.assertEqual(expected, check.status)
                    self.assertEqual(
                        [[binary, "--version"], [binary, "--no-auto-update", "--help"]],
                        commands,
                    )
                    self.assertFalse(
                        any({"auth", "login", "models"} & set(argv) for argv in commands)
                    )
                    if label == "hidden-flag-accepted":
                        self.assertNotIn("--no-auto-update", visible_help)
                    elif label == "parser-rejects-hidden-flag":
                        self.assertEqual(2, check.details["exitCode"])
                    else:
                        self.assertEqual(
                            ["--output-format", "--prompt-file"], check.details["missing"]
                        )

    def test_agy_status_only_never_submits_the_inference_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            commands: list[list[str]] = []

            def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
                commands.append(argv)
                return self.completed(argv)

            with mock.patch.object(doctor.subprocess, "run", side_effect=runner):
                report = doctor.run_doctor(ROOT, env, harness="agy")
            check = self.check(report, "harness.agy.auth_probe")
            self.assertEqual("SKIP", check["status"])
            self.assertEqual("unknown", check["details"]["readiness"])
            self.assertFalse(
                any("-p" in argv or "Reply with exactly: pong" in argv for argv in commands)
            )

    def test_agy_live_auth_is_explicit_verified_request_and_redacts_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            commands: list[list[str]] = []

            def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
                commands.append(argv)
                if "-p" in argv:
                    return subprocess.CompletedProcess(argv, 0, b"pong\n", b"")
                return self.completed(argv)

            with mock.patch.object(doctor.subprocess, "run", side_effect=runner):
                report = doctor.run_doctor(ROOT, env, harness="agy", live_auth=True)
            check = self.check(report, "harness.agy.auth_probe")
            self.assertEqual("PASS", check["status"])
            self.assertEqual("verified-request", check["details"]["readiness"])
            self.assertEqual("requested", check["details"]["inference"])
            self.assertTrue(any("-p" in argv for argv in commands))
            self.assertNotIn("Reply with exactly: pong", json.dumps(check))

    def test_default_report_is_serializable_non_mutating_and_avoids_live_probes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            commands: list[list[str]] = []

            def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
                commands.append(argv)
                return self.completed(argv)

            with mock.patch.object(doctor.subprocess, "run", side_effect=runner):
                report = doctor.run_doctor(ROOT, env)
            json.dumps(report)
            self.assertEqual(1, report["schemaVersion"])
            self.assertEqual({"pass", "warn", "fail", "skip"}, set(report["summary"]))
            self.assertTrue(
                all(check["status"] in doctor.VALID_STATUSES for check in report["checks"])
            )
            self.assertTrue(commands)
            self.assertTrue(
                all(
                    "models" not in argv and "auth" not in argv and "login" not in argv
                    for argv in commands
                )
            )
            self.assertTrue(any(argv[-2:] == ["doctor", "config"] for argv in commands))
            self.assertFalse((root / "state").exists())
            self.assertFalse((root / "config").exists())
            security = next(
                check for check in report["checks"] if check["id"] == "security.unrestricted_mode"
            )
            self.assertEqual("PASS", security["status"])
            self.assertIn("(restricted)", security["summary"])
            self.assertEqual({"value": None}, security["details"])

    def test_explicit_unrestricted_mode_warns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(Path(directory))
            env["PITWALL_AGENTS_UNRESTRICTED"] = "1"
            with mock.patch.object(
                doctor.subprocess,
                "run",
                side_effect=lambda argv, **_: self.completed(argv),
            ):
                report = doctor.run_doctor(ROOT, env)
            security = next(
                check for check in report["checks"] if check["id"] == "security.unrestricted_mode"
            )
            self.assertEqual("WARN", security["status"])
            self.assertIn("bypasses harness sandboxes", security["summary"])

    def test_explicit_restricted_mode_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(Path(directory))
            env["PITWALL_AGENTS_UNRESTRICTED"] = "0"
            with mock.patch.object(
                doctor.subprocess,
                "run",
                side_effect=lambda argv, **_: self.completed(argv),
            ):
                report = doctor.run_doctor(ROOT, env)
            security = next(
                check for check in report["checks"] if check["id"] == "security.unrestricted_mode"
            )
            self.assertEqual("PASS", security["status"])
            self.assertIn("=0", security["summary"])

    def test_harness_filter_keeps_only_requested_harness_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(Path(directory))
            with mock.patch.object(
                doctor.subprocess, "run", side_effect=lambda argv, **_: self.completed(argv)
            ):
                report = doctor.run_doctor(ROOT, env, harness="codex")
            harnesses = {
                check.get("provider")
                for check in report["checks"]
                if check["category"] == "provider"
            }
            self.assertEqual({"codex"}, harnesses)
            self.assertIn("runtime", {check["category"] for check in report["checks"]})

    def test_invalid_registry_is_machine_distinguishable_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "src/pitwall/agents/resources/config"
            config.mkdir(parents=True)
            (config / "harness-registry.json").write_text("{not-json", encoding="utf-8")
            report = doctor.run_doctor(root, self.environment(root, harnesses=False))
            self.assertEqual("fail", report["status"])
            self.assertEqual("runtime.registry_valid", report["checks"][0]["id"])
            self.assertEqual("FAIL", report["checks"][0]["status"])

    def test_artifact_mode_skips_clone_integrity_and_loads_packaged_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            with mock.patch.object(
                doctor.subprocess,
                "run",
                side_effect=lambda argv, **_: self.completed(argv),
            ):
                report = doctor.run_doctor(None, env)
        by_id = {check["id"]: check for check in report["checks"]}
        self.assertFalse(report["modes"]["sourceMode"])
        for check_id in (
            "runtime.source_registry_layout",
            "runtime.generated_routes",
            "runtime.install_links",
            "plugin.claude.marketplace_present",
            "plugin.codex.marketplace_present",
            "plugin.copilot.marketplace_present",
            "plugin.runtime_reference_shared",
            "plugin.version_alignment",
        ):
            self.assertEqual("SKIP", by_id[check_id]["status"], check_id)
        self.assertFalse(any(check["status"] == "FAIL" for check in report["checks"]))
        self.assertNotIn("ImportError", json.dumps(report))

    def test_generated_routes_check_without_sys_path(self) -> None:
        from pitwall.agents.registry import load_registry

        registry = load_registry()
        runtime = str(ROOT / "src")
        stripped = [entry for entry in sys.path if Path(entry or ".").resolve() != ROOT]
        stripped.insert(0, runtime)
        hidden = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "tools" or name.startswith("tools.")
        }
        try:
            with mock.patch.object(sys, "path", stripped):
                before = list(sys.path)
                check = doctor._check_generated_routes(ROOT, {}, registry)
                self.assertEqual(before, sys.path)
            self.assertEqual("PASS", check.status, check.details)
            self.assertFalse(
                [name for name in sys.modules if name == "tools" or name.startswith("tools.")]
            )
        finally:
            sys.modules.update(hidden)

    def test_source_mode_detects_a_missing_prompt_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "agent-routing"
            copy_component(source)
            missing = source / "docs/prompting/openai-codex-gpt-prompting-reference.md"
            missing.unlink()
            report = doctor.run_doctor(
                source,
                self.environment(Path(directory) / "doctor-home", harnesses=False),
                installation_only=True,
            )
        self.assertEqual("fail", report["status"])
        failure = report["checks"][0]
        self.assertEqual("runtime.registry_valid", failure["id"])
        self.assertIn("does not exist", failure["summary"])

    def test_source_mode_detects_every_clone_integrity_class(self) -> None:
        mutations = {
            "capability-card": lambda source: (
                source / "plugins/claude/skills/subagent-model-routing/ledger/codex.md"
            ).unlink(),
            "runtime-anchor": lambda source: self._remove_runtime_anchor(source),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "agent-routing"
                copy_component(source)
                mutate(source)
                report = doctor.run_doctor(
                    source,
                    self.environment(root / "doctor-home", harnesses=False),
                    installation_only=True,
                )
                self.assertEqual("fail", report["status"])
                self.assertEqual("runtime.registry_valid", report["checks"][0]["id"])

    @staticmethod
    def _remove_runtime_anchor(source: Path) -> None:
        reference = (
            source / "plugins/claude/skills/subagent-model-routing/references/model-prompting.md"
        )
        text = reference.read_text(encoding="utf-8")
        reference.write_text(
            text.replace("(#openai-gpt-6-through-codex)", "(#removed-openai-gpt-6-anchor)"),
            encoding="utf-8",
        )

    def test_source_mode_detects_generated_asset_and_install_link_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "agent-routing"
            copy_component(source)
            generated = source / "src/pitwall/agents/resources/generated/routes.generated.md"
            generated.unlink()
            report = doctor.run_doctor(
                source,
                self.environment(root / "doctor-generated", harnesses=False),
                installation_only=True,
            )
            check = next(
                item for item in report["checks"] if item["id"] == "runtime.generated_routes"
            )
            self.assertEqual("WARN", check["status"])
            self.assertIn("missing", json.dumps(check["details"]))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root / "doctor-links", harnesses=False)
            scripts = Path(env["HOME"]) / ".claude" / "scripts"
            scripts.mkdir(parents=True)
            for name in shim_names():
                if name == "qwen-shim.sh":
                    continue
                (scripts / name).write_text(shim_script(name), encoding="utf-8")
                (scripts / name).chmod(0o755)
            report = doctor.run_doctor(ROOT, env, installation_only=True)
            check = next(item for item in report["checks"] if item["id"] == "runtime.install_links")
            self.assertEqual("WARN", check["status"])
            self.assertEqual([str(scripts / "qwen-shim.sh")], check["details"]["missing"])

    def test_missing_binary_and_help_drift_are_warnings_not_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            env["CODEX_BIN"] = str(root / "missing-codex")
            with mock.patch.object(
                doctor.subprocess,
                "run",
                side_effect=lambda argv, **_: self.completed(argv, help_complete=False),
            ):
                report = doctor.run_doctor(ROOT, env)
            by_id = {check["id"]: check for check in report["checks"]}
            self.assertEqual("WARN", by_id["harness.codex.binary_resolved"]["status"])
            self.assertEqual("WARN", by_id["harness.claude.cli_contract"]["status"])

    def test_installation_only_has_runtime_checks_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(Path(directory), harnesses=False)
            with mock.patch.object(
                doctor.subprocess, "run", side_effect=lambda argv, **_: self.completed(argv)
            ):
                report = doctor.run_doctor(ROOT, env, installation_only=True)
            self.assertEqual({"runtime"}, {check["category"] for check in report["checks"]})

    def test_live_auth_is_the_only_mode_that_runs_documented_auth_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(Path(directory))
            commands: list[list[str]] = []

            def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
                commands.append(argv)
                if argv[-2:] == ["auth", "status"]:
                    return subprocess.CompletedProcess(
                        argv, 0, b"signed in secret-account@example.test\n", b""
                    )
                return self.completed(argv)

            with mock.patch.object(doctor.subprocess, "run", side_effect=runner):
                default = doctor.run_doctor(ROOT, env, harness="claude")
                self.assertFalse(any(argv[-2:] == ["auth", "status"] for argv in commands))
                live = doctor.run_doctor(ROOT, env, harness="claude", live_auth=True)
            self.assertTrue(any(argv[-2:] == ["auth", "status"] for argv in commands))
            self.assertFalse(any("models" in argv or "discover" in argv for argv in commands))
            self.assertEqual(
                "signed-in", self.check(live, "harness.claude.auth_probe")["details"]["readiness"]
            )
            self.assertNotIn("secret-account", json.dumps(default))
            self.assertNotIn("secret-account", json.dumps(live))

    def test_readiness_uses_safe_local_metadata_without_claiming_auth_validity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            auth_path = Path(env["HOME"]) / ".local/share/opencode/auth.json"
            auth_path.parent.mkdir(parents=True)
            auth_path.write_text('{"harness":"test","token":"do-not-print"}', encoding="utf-8")
            report = doctor.run_doctor(ROOT, env, harness="opencode")
            check = self.check(report, "harness.opencode.auth_probe")
            self.assertEqual("SKIP", check["status"])
            self.assertEqual("unknown", check["details"]["readiness"])
            self.assertTrue(check["details"]["metadataPresent"])
            self.assertNotIn("do-not-print", json.dumps(report))

    def test_empty_local_auth_store_does_not_claim_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            auth_path = Path(env["HOME"]) / ".pi/agent/auth.json"
            auth_path.parent.mkdir(parents=True)
            auth_path.write_text("{}", encoding="utf-8")
            report = doctor.run_doctor(ROOT, env, harness="pi")
            check = self.check(report, "harness.pi.auth_probe")
            self.assertEqual("unknown", check["details"]["readiness"])
            self.assertTrue(check["details"]["metadataPresent"])

    def test_kimi_config_probe_is_read_only_bounded_to_status_and_does_not_retain_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(Path(directory))

            def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
                if argv[-2:] == ["doctor", "config"]:
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        b"OK config with sk-secret-that-must-not-be-retained\n",
                        b"private diagnostic detail\n",
                    )
                return self.completed(argv)

            with mock.patch.object(doctor.subprocess, "run", side_effect=runner):
                report = doctor.run_doctor(ROOT, env, harness="kimi")
            check = next(
                item for item in report["checks"] if item["id"] == "harness.kimi.config_probe"
            )
            self.assertEqual("PASS", check["status"])
            serialized = json.dumps(check)
            self.assertNotIn("sk-secret", serialized)
            self.assertNotIn("private diagnostic", serialized)
            self.assertEqual(["doctor", "config"], check["details"]["argv"][-2:])
            self.assertEqual("configured", check["details"]["readiness"])

    def test_muse_auth_recipe_matches_supported_stdin_command(self) -> None:
        setup_docs = (ROOT / "docs/agents/harness-cli-setup.md").read_text(encoding="utf-8")
        readme = (ROOT / "docs/agents/routing-readme.md").read_text(encoding="utf-8")
        command = "muse auth set --provider meta --api-key-stdin"
        self.assertIn(command, setup_docs)
        self.assertIn(command, readme)
        self.assertNotIn("muse auth login", setup_docs)
        self.assertNotIn("muse auth login", readme)

    def test_unknown_harness_is_an_invocation_error(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ValueError, "unknown harness"),
        ):
            doctor.run_doctor(ROOT, self.environment(Path(directory)), harness="unknown")

    def test_discovery_runs_only_when_explicit_and_is_harness_filterable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(Path(directory))
            discovered = [
                {
                    "id": "harness.codex.models_discovery",
                    "category": "provider",
                    "provider": "codex",
                    "status": "PASS",
                    "summary": "models discovered",
                    "details": {
                        "source": "local-cache",
                        "command": None,
                        "models": ["gpt-test"],
                        "configuredModels": [],
                    },
                }
            ]
            with (
                mock.patch.object(
                    doctor.subprocess, "run", side_effect=lambda argv, **_: self.completed(argv)
                ),
                mock.patch.object(
                    doctor, "run_model_discovery", return_value=discovered
                ) as discovery,
            ):
                default = doctor.run_doctor(ROOT, env, harness="codex")
                discovery.assert_not_called()
                report = doctor.run_doctor(ROOT, env, harness="codex", discover_models=True)
            self.assertFalse(default["modes"]["discoverModels"])
            self.assertTrue(report["modes"]["discoverModels"])
            discovery.assert_called_once_with(ROOT, env, mock.ANY, harness="codex")
            self.assertEqual(
                "PASS",
                next(
                    check for check in report["checks"] if check["id"].endswith("models_discovery")
                )["status"],
            )

    def test_installation_only_rejects_discovery(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ValueError, "cannot be combined"),
        ):
            doctor.run_doctor(
                ROOT,
                self.environment(Path(directory)),
                installation_only=True,
                discover_models=True,
            )


class ChannelDoctorTests(unittest.TestCase):
    def _claude_home(self, home: Path, *, enabled: bool, guard: bool) -> None:
        install = home / ".claude/plugins/cache/pitwall/pitwall/0.0.0"
        (install / "hooks").mkdir(parents=True)
        pre = (
            [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/launch-guard.py"',
                        }
                    ],
                }
            ]
            if guard
            else []
        )
        (install / "hooks/hooks.json").write_text(
            json.dumps({"hooks": {"PreToolUse": pre}}), encoding="utf-8"
        )
        (home / ".claude/plugins/installed_plugins.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "plugins": {
                        "pitwall@pitwall": [{"scope": "user", "installPath": str(install)}]
                    },
                }
            ),
            encoding="utf-8",
        )
        (home / ".claude/settings.json").write_text(
            json.dumps({"enabledPlugins": {"pitwall@pitwall": enabled}}), encoding="utf-8"
        )

    def test_launch_enforcement_states(self) -> None:
        for enabled, guard, status in (
            (True, True, "PASS"),
            (False, True, "WARN"),
            (True, False, "WARN"),
        ):
            with (
                self.subTest(enabled=enabled, guard=guard),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory)
                self._claude_home(home, enabled=enabled, guard=guard)
                env = {
                    "HOME": str(home),
                    "PATH": "/usr/bin:/bin",
                    "XDG_STATE_HOME": str(home / "state"),
                }
                report = doctor.run_doctor(None, env)
                check = next(c for c in report["checks"] if c["id"] == "channel.launch_enforcement")
                self.assertEqual(status, check["status"])
                self.assertEqual(status == "PASS", check["details"]["enforcementAvailable"])
                self.assertFalse(check["details"]["enforcementVerified"])
                if status == "WARN":
                    self.assertIn("unavailable", check["summary"])

    def test_server_handshake_and_registration_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            binary_dir.mkdir()
            for name in ("codex", "kimi"):
                target = binary_dir / name
                target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                target.chmod(0o755)
            launcher = binary_dir / "pitwall"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(0o755)
            (home / ".codex").mkdir()
            (home / ".codex/config.toml").write_text(
                f'[mcp_servers.pitwall-channel]\ncommand = "{launcher}"\nargs = ["mcp"]\n',
                encoding="utf-8",
            )
            env = {
                "HOME": str(home),
                "PATH": f"{binary_dir}:/usr/bin:/bin",
                "XDG_STATE_HOME": str(home / "state"),
            }
            # The probe returns as soon as the server replies; its own short reply budget is
            # replaced by the hang guard so a slow server start under load is not a WARN.
            with mock.patch.object(doctor, "CHANNEL_HANDSHAKE_TIMEOUT", HANG_GUARD_SECS):
                report = doctor.run_doctor(None, env)
        checks = {
            (c["id"], c.get("provider")): c for c in report["checks"] if c["category"] == "channel"
        }
        self.assertEqual("PASS", checks[("channel.mcp_server", None)]["status"])
        self.assertEqual("SKIP", checks[("channel.interactive_delivery", None)]["status"])
        self.assertTrue(
            checks[("channel.interactive_delivery", None)]["details"]["registrationIsNotDelivery"]
        )
        self.assertEqual("SKIP", checks[("channel.launch_enforcement", None)]["status"])
        self.assertFalse(
            checks[("channel.launch_enforcement", None)]["details"]["enforcementVerified"]
        )
        self.assertEqual("PASS", checks[("channel.registration", "codex")]["status"])
        kimi = checks[("channel.registration", "kimi")]
        self.assertEqual("WARN", kimi["status"])
        self.assertEqual("pitwall agents setup mcp --harness kimi", kimi["remediation"])
        self.assertNotIn(("channel.registration", "opencode"), checks)

    def test_unparseable_yaml_channel_config_is_reported_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            binary_dir.mkdir()
            hermes = binary_dir / "hermes"
            hermes.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            hermes.chmod(0o755)
            config = home / ".hermes" / "config.yaml"
            config.parent.mkdir()
            config.write_text("mcp_servers:\n  a: [unclosed\n", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PATH": f"{binary_dir}:/usr/bin:/bin",
                "XDG_STATE_HOME": str(home / "state"),
            }
            report = doctor.run_doctor(None, env)
        check = next(
            c
            for c in report["checks"]
            if c["id"] == "channel.registration" and c.get("provider") == "hermes"
        )
        self.assertEqual("WARN", check["status"])
        self.assertIn("unreadable", check["summary"])
        self.assertIn(str(config), check["summary"])
        self.assertIn("invalid YAML (ParserError)", check["summary"])
        self.assertNotIn("not registered", check["summary"])

    def test_unreadable_yaml_channel_config_never_echoes_its_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            binary_dir.mkdir()
            hermes = binary_dir / "hermes"
            hermes.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            hermes.chmod(0o755)
            config = home / ".hermes" / "config.yaml"
            config.parent.mkdir()
            config.write_text(
                "model: x\nmcp_servers:\n  docs:\n    TOKEN: hunter2\n"
                "    API_KEY: sk-SECRET123: x\n",
                encoding="utf-8",
            )
            env = {
                "HOME": str(home),
                "PATH": f"{binary_dir}:/usr/bin:/bin",
                "XDG_STATE_HOME": str(home / "state"),
            }
            outputs = {}
            for argv in (["doctor"], ["doctor", "--json"]):
                stdout = io.StringIO()
                with (
                    mock.patch.dict(os.environ, env, clear=True),
                    contextlib.redirect_stdout(stdout),
                ):
                    cli.main(argv)
                outputs[" ".join(argv)] = stdout.getvalue()
        for name, output in outputs.items():
            with self.subTest(output=name):
                self.assertIn("unreadable", output)
                self.assertIn("line 5, column 26", output)
                self.assertNotIn("sk-SECRET123", output)
                self.assertNotIn("hunter2", output)

    def test_channel_probe_closes_stdio_handles_after_handshake(self) -> None:
        """Repeated doctor probes must not leak subprocess pipe descriptors."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            binary_dir.mkdir()
            launcher = binary_dir / "pitwall"
            launcher.write_text(
                '#!/bin/sh\nexec "$PYTHON" -m pitwall.agents mcp\n', encoding="utf-8"
            )
            launcher.chmod(0o755)
            # The subprocess inherits the package runtime through this path.
            env = {
                "HOME": str(home),
                "PATH": f"{binary_dir}:/usr/bin:/bin",
                "PYTHON": sys.executable,
                "PYTHONPATH": str(ROOT / "src"),
            }
            # Finalize garbage left by earlier tests first, so only descriptors the
            # probe itself leaks can raise a ResourceWarning inside the block.
            gc.collect()
            with (
                warnings.catch_warnings(record=True) as captured,
                mock.patch.object(doctor, "CHANNEL_HANDSHAKE_TIMEOUT", HANG_GUARD_SECS),
            ):
                warnings.simplefilter("always", ResourceWarning)
                for _ in range(3):
                    self.assertIsNotNone(doctor._probe_channel_server(env, None))
                gc.collect()
            self.assertEqual(
                [], [warning for warning in captured if warning.category is ResourceWarning]
            )

    def test_channel_probe_bounds_a_silent_server(self) -> None:
        """A server that never writes must not hold the doctor indefinitely."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            real_popen = doctor.subprocess.Popen

            def silent_popen(_argv: object, **kwargs: object) -> object:
                return real_popen(
                    [sys.executable, "-c", f"import time; time.sleep({2 * HANG_GUARD_SECS!r})"],
                    **kwargs,
                )

            env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
            started = time.monotonic()
            with (
                mock.patch.object(doctor.subprocess, "Popen", side_effect=silent_popen),
                mock.patch.object(doctor, "CHANNEL_HANDSHAKE_TIMEOUT", 0.2),
            ):
                self.assertIsNone(doctor._probe_channel_server(env, None))
            # The probe never waits for a silent server; the hang guard is the only bound.
            self.assertLess(time.monotonic() - started, HANG_GUARD_SECS)


if __name__ == "__main__":
    unittest.main()
