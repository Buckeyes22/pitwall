"""Tests for optional harness CLI setup and its checkbox terminal."""

from __future__ import annotations

import hashlib
import io
import json
import os
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import termios
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from pitwall.agents import cli, setup
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
RESOURCE_ROOT = ROOT / "src/pitwall/agents/resources"


def executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def comparable_tty_attributes(fd: int) -> list[object]:
    """Return terminal attributes without Darwin's transient pending-input state."""

    attributes: list[object] = termios.tcgetattr(fd)
    attributes[3] = int(attributes[3]) & ~getattr(termios, "PENDIN", 0)
    return attributes


class FakeResponse:
    def __init__(self, content: bytes, url: str, *, status: int = 200) -> None:
        self.content = content
        self.url = url
        self.status = status
        self.offset = 0

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def read(self, size: int) -> bytes:
        chunk = self.content[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class FakeSession:
    def __init__(self, keys: list[str], *, term: str = "xterm") -> None:
        self.keys = iter(keys)
        self.term = term
        self.output = ""
        self.paints: list[tuple[str, ...]] = []
        self.descriptor = 9
        self.input_mode_restored = False

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read_key(self) -> str:
        return next(self.keys)

    def write(self, text: str) -> None:
        self.output += text

    def repaint(self, lines: tuple[str, ...]) -> None:
        self.paints.append(lines)

    def finish_repaint(self) -> None:
        self.output += "\n"

    def restore_input_mode(self) -> None:
        self.input_mode_restored = True


class ManifestTests(unittest.TestCase):
    def manifest(self) -> dict[str, object]:
        return json.loads(
            (RESOURCE_ROOT / "config/harness-installers.json").read_text(encoding="utf-8")
        )

    def load_with(self, value: dict[str, object]) -> tuple[setup.HarnessInstallSpec, ...]:
        with mock.patch.object(setup, "_load_json", return_value=value):
            return setup.load_install_specs(RESOURCE_ROOT, system_name="Linux")

    def manifest_with_recipe(
        self,
        harness_id: str,
        recipe: dict[str, object],
        *,
        hosts: list[str],
    ) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root))
        config = root / "config"
        config.mkdir()
        manifest = self.manifest()
        harnesses = manifest.get("harnesses")
        if not isinstance(harnesses, dict):
            raise AssertionError("test manifest harnesses must be an object")
        harness = harnesses.get(harness_id)
        if not isinstance(harness, dict):
            raise AssertionError(f"test harness {harness_id} must be an object")
        platforms = harness.get("platforms")
        if not isinstance(platforms, dict):
            raise AssertionError(f"test harness {harness_id} platforms must be an object")
        platforms["darwin"] = recipe
        platforms["linux"] = recipe
        harness["allowedRedirectHosts"] = hosts
        (config / "harness-installers.json").write_text(json.dumps(manifest), encoding="utf-8")
        (config / "harness-registry.json").write_text(
            (RESOURCE_ROOT / "config/harness-registry.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return root

    def load(self, manifest_root: Path, system_name: str) -> tuple[setup.HarnessInstallSpec, ...]:
        registry = setup.load_registry()
        with mock.patch.object(setup, "load_registry", return_value=registry):
            return setup.load_install_specs(manifest_root, system_name=system_name)

    def test_npm_recipe_validates_and_rejects_bad_names(self) -> None:
        good = self.manifest_with_recipe(
            "qwen",
            {"kind": "npm", "package": "@qwen-code/qwen-code", "version": "0.21.14"},
            hosts=["registry.npmjs.org"],
        )
        spec = next(s for s in self.load(good, system_name="Linux") if s.harness_id == "qwen")
        self.assertEqual(
            ("npm", "@qwen-code/qwen-code", "0.21.14", ""),
            (
                spec.recipe.kind,
                spec.recipe.package,
                spec.recipe.version,
                spec.recipe.installer_url,
            ),
        )
        for bad in (
            {"kind": "npm", "package": "../evil", "version": "1.0.0"},
            {"kind": "npm", "package": "ok", "version": "latest"},
            {"kind": "npm", "package": "ok"},
        ):
            with self.subTest(recipe=bad), self.assertRaises(setup.HarnessSetupError):
                self.load(
                    self.manifest_with_recipe("qwen", bad, hosts=["registry.npmjs.org"]),
                    system_name="Linux",
                )

    def test_script_recipe_env_is_carried(self) -> None:
        manifest = self.manifest_with_recipe(
            "qwen",
            {
                "installerUrl": "https://example.invalid/i.sh",
                "interpreter": ["bash"],
                "sha256": "0" * 64,
                "env": {"CONFIGURE": "false"},
            },
            hosts=["example.invalid"],
        )
        spec = next(s for s in self.load(manifest, system_name="Linux") if s.harness_id == "qwen")
        self.assertEqual((("CONFIGURE", "false"),), spec.recipe.env)
        with self.assertRaises(setup.HarnessSetupError):
            self.load(
                self.manifest_with_recipe(
                    "qwen",
                    {
                        "installerUrl": "https://example.invalid/i.sh",
                        "interpreter": ["bash"],
                        "sha256": "0" * 64,
                        "env": {"lower": "x"},
                    },
                    hosts=["example.invalid"],
                ),
                system_name="Linux",
            )

    def test_manifest_covers_registry_with_first_party_https_recipes(self) -> None:
        specs = setup.load_install_specs(system_name="Linux")
        self.assertEqual(
            (
                "codex",
                "claude",
                "grok",
                "kimi",
                "opencode",
                "goose",
                "qwen",
                "hermes",
                "pi",
                "muse",
                "cline",
                "dsh",
                "agy",
                "zcode",
            ),
            tuple(spec.harness_id for spec in specs),
        )
        for spec in specs:
            with self.subTest(harness=spec.harness_id):
                if spec.recipe.kind == "manual":
                    self.assertEqual("zcode", spec.harness_id)
                    self.assertEqual("", spec.recipe.installer_url)
                    self.assertEqual((), spec.recipe.interpreter)
                    self.assertEqual(frozenset(), spec.allowed_redirect_hosts)
                elif spec.recipe.kind == "npm":
                    self.assertTrue(spec.recipe.package)
                    self.assertTrue(spec.recipe.version)
                else:
                    self.assertTrue(spec.recipe.installer_url.startswith("https://"))
                    self.assertIn(spec.recipe.interpreter[0], {"bash", "sh"})
                    self.assertIn(
                        spec.recipe.installer_url.split("/", 3)[2], spec.allowed_redirect_hosts
                    )
                if spec.harness_id == "agy":
                    self.assertEqual((), spec.auth_args)
                else:
                    self.assertTrue(spec.auth_args)
                self.assertTrue(spec.verify_args)
        script_specs = [spec for spec in specs if spec.recipe.kind == "script"]
        self.assertEqual(10, len(script_specs))
        for spec in script_specs:
            with self.subTest(harness=spec.harness_id):
                self.assertRegex(spec.recipe.sha256 or "", r"^[0-9a-f]{64}$")

    def test_every_platform_recipe_in_the_manifest_carries_a_pinned_sha256(self) -> None:
        recipes = [
            (harness_id, platform_id, recipe)
            for harness_id, harness in self.manifest()["harnesses"].items()  # type: ignore[attr-defined]  # reason: the test reads an attribute on a loosely typed test double
            for platform_id, recipe in harness["platforms"].items()
            if recipe.get("kind", "script") == "script"
        ]
        self.assertEqual(20, len(recipes))
        for harness_id, platform_id, recipe in recipes:
            with self.subTest(harness=harness_id, platform=platform_id):
                self.assertRegex(str(recipe.get("sha256")), r"^[0-9a-f]{64}$")

    def test_null_sha256_is_rejected_when_the_manifest_loads(self) -> None:
        manifest = self.manifest()
        manifest["harnesses"]["kimi"]["platforms"]["linux"]["sha256"] = None  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(setup.HarnessSetupError, "sha256"):
            self.load_with(manifest)

    def test_agy_recipe_uses_first_party_installer_and_redirect_chain(self) -> None:
        spec = next(
            item
            for item in setup.load_install_specs(system_name="Linux")
            if item.harness_id == "agy"
        )
        self.assertEqual("https://antigravity.google/cli/install.sh", spec.recipe.installer_url)
        self.assertEqual(("bash",), spec.recipe.interpreter)
        self.assertRegex(spec.recipe.sha256 or "", r"^[0-9a-f]{64}$")
        self.assertEqual(("--version",), spec.verify_args)
        self.assertEqual((), spec.auth_args)
        self.assertEqual(
            frozenset(
                {
                    "antigravity.google",
                    "antigravity-cli-auto-updater-974169037036.us-central1.run.app",
                    "storage.googleapis.com",
                }
            ),
            spec.allowed_redirect_hosts,
        )

    def test_kimi_recipe_allows_the_cdn_host_its_installer_redirects_to(self) -> None:
        spec = next(
            item
            for item in setup.load_install_specs(system_name="Linux")
            if item.harness_id == "kimi"
        )
        self.assertEqual(frozenset({"code.kimi.com", "cdn.kimi.com"}), spec.allowed_redirect_hosts)

    def test_missing_harness_is_rejected(self) -> None:
        manifest = self.manifest()
        del manifest["harnesses"]["kimi"]  # type: ignore[attr-defined,index]  # reason: the test reads an attribute on a loosely typed test double; the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(setup.HarnessSetupError, "exactly match"):
            self.load_with(manifest)

    def test_extra_harness_is_rejected(self) -> None:
        manifest = self.manifest()
        manifest["harnesses"]["unexpected"] = manifest["harnesses"]["codex"]  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(setup.HarnessSetupError, "exactly match"):
            self.load_with(manifest)

    def test_registry_drift_is_rejected(self) -> None:
        registry = json.loads(
            (RESOURCE_ROOT / "config/harness-registry.json").read_text(encoding="utf-8")
        )
        del registry["harnesses"]["kimi"]
        with (
            mock.patch.object(setup, "load_registry", return_value=registry),
            self.assertRaisesRegex(setup.HarnessSetupError, "exactly match"),
        ):
            setup.load_install_specs(system_name="Linux")

    def test_unsafe_scheme_is_rejected(self) -> None:
        manifest = self.manifest()
        manifest["harnesses"]["codex"]["platforms"]["linux"]["installerUrl"] = (
            "http://example.com/x"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        )
        with self.assertRaisesRegex(setup.HarnessSetupError, "must use https"):
            self.load_with(manifest)

    def test_unapproved_interpreter_is_rejected(self) -> None:
        manifest = self.manifest()
        manifest["harnesses"]["codex"]["platforms"]["linux"]["interpreter"] = ["python"]  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(setup.HarnessSetupError, "unsupported interpreter"):
            self.load_with(manifest)

    def test_initial_installer_host_must_be_allowlisted(self) -> None:
        manifest = self.manifest()
        manifest["harnesses"]["codex"]["allowedRedirectHosts"] = ["example.com"]  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(setup.HarnessSetupError, "installer host"):
            self.load_with(manifest)

    def test_invalid_redirect_hostname_is_rejected(self) -> None:
        manifest = self.manifest()
        manifest["harnesses"]["codex"]["allowedRedirectHosts"] = ["chatgpt.com", "bad..host"]  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(setup.HarnessSetupError, "invalid host"):
            self.load_with(manifest)

    def test_registry_display_name_and_override_are_reused(self) -> None:
        specs = setup.load_install_specs(system_name="Darwin")
        codex = specs[0]
        self.assertEqual("OpenAI Codex", codex.display_name)
        self.assertEqual("CODEX_BIN", codex.binary_override_env)

    def test_unsupported_native_windows_is_explicit(self) -> None:
        with self.assertRaisesRegex(setup.HarnessSetupError, "Windows through WSL.*https://"):
            setup.load_install_specs(system_name="Windows")


class DetectionAndSelectionTests(unittest.TestCase):
    def specs(self) -> tuple[setup.HarnessInstallSpec, ...]:
        return setup.load_install_specs(system_name="Linux")

    def test_detection_respects_path_fallback_and_binary_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            binary_dir = root / "bin"
            home.mkdir()
            codex = executable(binary_dir / "codex")
            custom_kimi = executable(root / "custom-kimi")
            env = {
                "HOME": str(home),
                "PATH": str(binary_dir),
                "KIMI_BIN": str(custom_kimi),
            }
            rows = {row.harness_id: row for row in setup.detect_harness_rows(self.specs(), env)}
            self.assertEqual(str(codex.resolve()), rows["codex"].installed_path)
            self.assertEqual(str(custom_kimi.resolve()), rows["kimi"].installed_path)
            self.assertFalse(rows["claude"].installed)

    def test_detection_refreshes_existing_common_user_bin_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            opencode = executable(home / ".opencode/bin/opencode")
            rows = setup.detect_harness_rows(
                self.specs(), {"HOME": str(home), "PATH": "/nonexistent"}
            )
            by_id = {row.harness_id: row for row in rows}
            self.assertEqual(str(opencode.resolve()), by_id["opencode"].installed_path)

    def test_selection_skips_installed_rows_and_toggles_only_missing(self) -> None:
        rows = (
            setup.HarnessRow("codex", "Codex", "/bin/codex"),
            setup.HarnessRow("claude", "Claude", None),
            setup.HarnessRow("grok", "Grok", "/bin/grok"),
            setup.HarnessRow("kimi", "Kimi", None),
        )
        state = setup.initial_selection(rows)
        self.assertEqual(1, state.cursor)
        state = setup.toggle_selection(state)
        self.assertEqual(("claude",), setup.selected_harness_ids(state))
        state = setup.move_selection(state, 1)
        self.assertEqual(3, state.cursor)
        state = setup.toggle_selection(state)
        self.assertEqual(("claude", "kimi"), setup.selected_harness_ids(state))
        state = setup.move_selection(state, 1)
        self.assertEqual(1, state.cursor)

    def test_checkbox_controls_and_cancel(self) -> None:
        rows = (
            setup.HarnessRow("codex", "Codex", None),
            setup.HarnessRow("claude", "Claude", None),
        )
        session = FakeSession([" ", "DOWN", " ", "\r"])
        selected = setup.choose_harnesses(rows, session, no_color=True)  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertEqual(("codex", "claude"), selected)
        self.assertIn("[x] Codex", "\n".join(session.paints[-1]))

        cancelled = setup.choose_harnesses(
            rows,
            FakeSession(["ESC"]),
            no_color=True,  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        )
        self.assertIsNone(cancelled)

    def test_enter_with_no_selection_is_clean_skip(self) -> None:
        rows = (setup.HarnessRow("codex", "Codex", None),)
        selected = setup.choose_harnesses(
            rows,
            FakeSession(["\n"]),
            no_color=True,  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        )
        self.assertEqual((), selected)

    def test_confirmation_defaults_to_no_and_shows_sources(self) -> None:
        spec = self.specs()[0]
        rejected = FakeSession(["\n"])
        self.assertFalse(
            setup.confirm_selection([spec], rejected, dry_run=False)  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        )
        self.assertIn("chatgpt.com", rejected.output)
        self.assertIn("SHA-256", rejected.output)
        self.assertIn("No login", rejected.output)

        kimi = next(spec for spec in self.specs() if spec.harness_id == "kimi")
        kimi_rejected = FakeSession(["\n"])
        self.assertFalse(
            setup.confirm_selection([kimi], kimi_rejected, dry_run=False)  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        )
        self.assertIn("code.kimi.com", kimi_rejected.output)
        self.assertIn("SHA-256 270a86f2d230", kimi_rejected.output)
        self.assertNotIn("WARNING", kimi_rejected.output)

    def test_tty_selector_reads_arrows_space_enter_and_restores_attributes(self) -> None:
        master, slave = pty.openpty()
        stop_drain = threading.Event()

        def drain_master() -> None:
            while not stop_drain.is_set():
                ready, _, _ = select.select([master], [], [], 0.05)
                if ready:
                    try:
                        os.read(master, 4096)
                    except OSError:
                        return

        drain_thread = threading.Thread(target=drain_master, daemon=True)
        drain_thread.start()
        try:
            tty_path = os.ttyname(slave)
            before = comparable_tty_attributes(slave)
            with setup.TtySession(tty_path, term="xterm") as session:
                os.write(master, b" \x1b[B \r")
                selected = setup.choose_harnesses(
                    (
                        setup.HarnessRow("codex", "Codex", None),
                        setup.HarnessRow("claude", "Claude", None),
                    ),
                    session,
                    no_color=True,
                )
                self.assertEqual(("codex", "claude"), selected)
                session.restore_input_mode()
                self.assertEqual(before, comparable_tty_attributes(slave))
            self.assertEqual(before, comparable_tty_attributes(slave))
        finally:
            stop_drain.set()
            drain_thread.join(timeout=HANG_GUARD_SECS)
            os.close(master)
            os.close(slave)

    def test_tty_ctrl_c_restores_attributes(self) -> None:
        master, slave = pty.openpty()
        try:
            tty_path = os.ttyname(slave)
            before = comparable_tty_attributes(slave)
            with (
                self.assertRaises(setup.TerminalSignal),
                setup.TtySession(tty_path, term="xterm"),
            ):
                os.kill(os.getpid(), signal.SIGINT)
            self.assertEqual(before, comparable_tty_attributes(slave))
        finally:
            os.close(master)
            os.close(slave)


class DownloadAndInstallTests(unittest.TestCase):
    def specs(self) -> tuple[setup.HarnessInstallSpec, ...]:
        return setup.load_install_specs(system_name="Linux")

    def npm_spec(self, harness_id: str, package: str, version: str) -> setup.HarnessInstallSpec:
        spec = next(item for item in self.specs() if item.harness_id == harness_id)
        return replace(
            spec,
            recipe=setup.PlatformRecipe("", (), None, kind="npm", package=package, version=version),
            allowed_redirect_hosts=frozenset({"registry.npmjs.org"}),
        )

    def script_spec_with_env(
        self, harness_id: str, recipe_env: dict[str, str]
    ) -> setup.HarnessInstallSpec:
        spec = next(item for item in self.specs() if item.harness_id == harness_id)
        return replace(
            spec,
            recipe=replace(spec.recipe, env=tuple(sorted(recipe_env.items()))),
        )

    def test_npm_install_runs_ignore_scripts_or_fails_without_npm(self) -> None:
        spec = self.npm_spec("qwen", "@qwen-code/qwen-code", "0.21.14")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            bin_dir = Path(directory) / "bin"
            executable(bin_dir / "npm")
            seen: list[list[str]] = []

            def runner(argv: list[str], child_env: dict[str, str], _fd: int) -> int:
                seen.append(list(argv))
                executable(Path(child_env["HOME"]) / ".local/bin/qwen")
                return 0

            results = setup.install_selected(
                [spec],
                {"HOME": str(home), "PATH": str(bin_dir)},
                1,
                installer_runner=runner,  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
                verifier=lambda *_: (True, "verified"),
            )
            self.assertEqual("installed", results[0].status)
            self.assertEqual(
                ["install", "-g", "--ignore-scripts", "@qwen-code/qwen-code@0.21.14"],
                seen[0][1:],
            )
            self.assertIn("no reviewed checksum", results[0].message)
            (home / ".local/bin/qwen").unlink()
            missing = setup.install_selected(
                [spec],
                {"HOME": str(home), "PATH": "/nonexistent"},
                1,
                installer_runner=runner,  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
                verifier=lambda *_: (True, "ok"),
            )
            self.assertEqual("failed", missing[0].status)
            self.assertIn("npm", missing[0].message)
            dry = setup.install_selected(
                [spec],
                {"HOME": str(home), "PATH": str(bin_dir)},
                1,
                dry_run=True,
            )
            self.assertIn(
                "npm install -g --ignore-scripts @qwen-code/qwen-code@0.21.14",
                dry[0].message,
            )

    def test_script_recipe_env_reaches_the_installer(self) -> None:
        spec = self.script_spec_with_env("qwen", {"CONFIGURE": "false"})
        captured: dict[str, str] = {}

        def runner(argv: list[str], child_env: dict[str, str], _fd: int) -> int:
            captured.update(child_env)
            executable(Path(child_env["HOME"]) / ".local/bin/qwen")
            return 0

        with tempfile.TemporaryDirectory() as directory:
            setup.install_selected(
                [spec],
                {"HOME": directory, "PATH": "/nonexistent"},
                1,
                downloader=lambda _s: b"#!/bin/sh\n",
                installer_runner=runner,  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
                verifier=lambda *_: (True, "ok"),
            )
        self.assertEqual("false", captured.get("CONFIGURE"))

    def test_download_accepts_only_bounded_shebang_from_allowlisted_redirect(self) -> None:
        spec = self.specs()[0]
        content = b"#!/bin/sh\necho ok\n"
        spec = replace(
            spec,
            recipe=replace(spec.recipe, sha256=hashlib.sha256(content).hexdigest()),
        )
        response = FakeResponse(
            content,
            "https://release-assets.githubusercontent.com/file?signature=secret#fragment",
        )
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(setup, "build_opener", return_value=opener) as build:
            downloaded = setup.download_installer(spec)
        self.assertEqual(content, downloaded.content)
        self.assertEqual(
            "https://release-assets.githubusercontent.com/file", downloaded.resolved_url
        )
        self.assertNotIn("secret", downloaded.resolved_url)
        self.assertEqual(hashlib.sha256(content).hexdigest(), downloaded.sha256)
        request = opener.open.call_args.args[0]
        self.assertEqual(spec.recipe.installer_url, request.full_url)
        handler = build.call_args.args[0]
        self.assertEqual(spec.allowed_redirect_hosts, handler.allowed_hosts)

    def test_each_redirect_hop_is_rejected_before_request(self) -> None:
        spec = self.specs()[0]
        handler = setup.ApprovedRedirectHandler(spec.allowed_redirect_hosts)
        with self.assertRaisesRegex(setup.InstallerDownloadError, "unapproved host"):
            handler.redirect_request(
                setup.Request(spec.recipe.installer_url),
                None,
                302,
                "Found",
                {},
                "https://evil.example/install.sh",
            )

    def test_unpinned_recipe_refused(self) -> None:
        spec = self.specs()[0]
        spec = replace(spec, recipe=replace(spec.recipe, sha256=None))
        opener = mock.Mock()
        opener.open.return_value = FakeResponse(b"#!/bin/sh\necho ok\n", spec.recipe.installer_url)
        with (
            mock.patch.object(setup, "build_opener", return_value=opener),
            self.assertRaisesRegex(setup.InstallerDownloadError, "no pinned SHA-256"),
        ):
            setup.download_installer(spec)
        opener.open.assert_not_called()

    def test_download_rejects_changed_content_when_checksum_is_pinned(self) -> None:
        spec = self.specs()[0]
        response = FakeResponse(b"#!/bin/sh\necho changed\n", spec.recipe.installer_url)
        opener = mock.Mock()
        opener.open.return_value = response
        with (
            mock.patch.object(setup, "build_opener", return_value=opener),
            self.assertRaisesRegex(setup.InstallerDownloadError, "SHA-256"),
        ):
            setup.download_installer(spec)

    def test_download_rejects_unapproved_redirect_empty_and_non_script_content(self) -> None:
        spec = self.specs()[0]
        cases = (
            (FakeResponse(b"#!/bin/sh\n", "https://evil.example/install"), "unapproved host"),
            (FakeResponse(b"", spec.recipe.installer_url), "empty"),
            (FakeResponse(b"not a script", spec.recipe.installer_url), "shebang"),
        )
        for response, message in cases:
            opener = mock.Mock()
            opener.open.return_value = response
            with (
                self.subTest(message=message),
                mock.patch.object(setup, "build_opener", return_value=opener),
                self.assertRaisesRegex(setup.InstallerDownloadError, message),
            ):
                setup.download_installer(spec)

    def test_download_failure_result_links_first_party_documentation(self) -> None:
        spec = self.specs()[0]
        with tempfile.TemporaryDirectory() as directory:
            results = setup.install_selected(
                [spec],
                {"HOME": directory, "PATH": "/nonexistent"},
                1,
                downloader=mock.Mock(
                    side_effect=setup.InstallerDownloadError("source unavailable")
                ),
            )
        self.assertEqual("failed", results[0].status)
        self.assertIn(spec.documentation_url, results[0].message)

    def test_download_rejects_oversized_content(self) -> None:
        spec = self.specs()[0]
        content = b"#!" + b"x" * setup.MAX_INSTALLER_BYTES
        response = FakeResponse(content, spec.recipe.installer_url)
        opener = mock.Mock()
        opener.open.return_value = response
        with (
            mock.patch.object(setup, "build_opener", return_value=opener),
            self.assertRaisesRegex(setup.InstallerDownloadError, "size limit"),
        ):
            setup.download_installer(spec)

    def test_install_continues_after_failure_verifies_success_and_cleans_tempfiles(self) -> None:
        specs = self.specs()
        selected = (specs[0], specs[3])  # codex fails, kimi succeeds
        seen_paths: list[Path] = []
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            home.mkdir()
            # A hermetic PATH: a real harness CLI on this machine (for example
            # a root-owned /usr/bin/codex) must not turn "codex fails to
            # install" into "codex already installed".
            env = {"HOME": str(home), "PATH": "/nonexistent"}

            def runner(argv: list[str], child_env: dict[str, str], _fd: int) -> int:
                path = Path(argv[-1])
                seen_paths.append(path)
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
                if "codex" in path.name:
                    return 7
                executable(Path(child_env["HOME"]) / ".local/bin/kimi")
                return 0

            results = setup.install_selected(
                selected,
                env,
                1,
                downloader=lambda _spec: b"#!/bin/sh\n",
                installer_runner=runner,  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
                verifier=lambda *_args: (True, "verified"),
            )
        self.assertEqual(("failed", "installed"), tuple(result.status for result in results))
        self.assertIn("exited 7", results[0].message)
        self.assertTrue(all(not path.exists() for path in seen_paths))

    def test_existing_binary_is_never_downloaded_or_reinstalled(self) -> None:
        spec = self.specs()[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary_dir = root / "bin"
            executable(binary_dir / "codex")
            downloader = mock.Mock(side_effect=AssertionError("must not download"))
            results = setup.install_selected(
                [spec],
                {"HOME": str(root / "home"), "PATH": str(binary_dir)},
                1,
                downloader=downloader,
            )
        self.assertEqual("already-installed", results[0].status)
        downloader.assert_not_called()

    def test_dry_run_downloads_and_verifies_but_never_executes(self) -> None:
        spec = self.specs()[0]
        digest = "a" * 64
        downloader = mock.Mock(
            return_value=setup.InstallerDownload(
                b"#!/bin/sh\n", "https://example.test/i.sh", digest
            )
        )
        runner = mock.Mock(side_effect=AssertionError("must not run"))
        with tempfile.TemporaryDirectory() as directory:
            results = setup.install_selected(
                [spec],
                {"HOME": directory, "PATH": "/nonexistent"},
                1,
                dry_run=True,
                downloader=downloader,
                installer_runner=runner,
            )
        self.assertEqual("dry-run", results[0].status)
        self.assertIn(spec.recipe.installer_url, results[0].message)
        self.assertIn("verified", results[0].message)
        self.assertIn(digest, results[0].message)
        downloader.assert_called_once_with(spec)
        runner.assert_not_called()

    def test_dry_run_reports_a_hash_mismatch_as_a_failure(self) -> None:
        spec = self.specs()[0]
        downloader = mock.Mock(
            side_effect=setup.InstallerDownloadError("installer SHA-256 did not match")
        )
        runner = mock.Mock(side_effect=AssertionError("must not run"))
        with tempfile.TemporaryDirectory() as directory:
            results = setup.install_selected(
                [spec],
                {"HOME": directory, "PATH": "/nonexistent"},
                1,
                dry_run=True,
                downloader=downloader,
                installer_runner=runner,
            )
        self.assertEqual("failed", results[0].status)
        self.assertIn("did not match", results[0].message)
        runner.assert_not_called()

    def test_runner_uses_argv_and_never_shell_true(self) -> None:
        process = mock.Mock()
        process.wait.return_value = 0
        with mock.patch.object(setup.subprocess, "Popen", return_value=process) as popen:
            result = setup.run_installer(["sh", "/tmp/installer.sh"], {"PATH": "/bin"}, 1)
        self.assertEqual(0, result)
        self.assertEqual(["sh", "/tmp/installer.sh"], popen.call_args.args[0])
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_runner_bounds_term_and_kill_waits_after_timeout(self) -> None:
        process = mock.Mock(pid=4321)
        process.wait.side_effect = (
            subprocess.TimeoutExpired(["sh"], setup.INSTALL_TIMEOUT_SECONDS),
            subprocess.TimeoutExpired(["sh"], 2),
            subprocess.TimeoutExpired(["sh"], 2),
        )
        with (
            mock.patch.object(setup.subprocess, "Popen", return_value=process),
            mock.patch.object(setup.os, "killpg") as killpg,
        ):
            result = setup.run_installer(["sh", "/tmp/installer.sh"], {"PATH": "/bin"}, 1)
        self.assertEqual(124, result)
        self.assertEqual(
            [mock.call(4321, signal.SIGTERM), mock.call(4321, signal.SIGKILL)],
            killpg.call_args_list,
        )
        self.assertEqual(
            [
                mock.call(timeout=setup.INSTALL_TIMEOUT_SECONDS),
                mock.call(timeout=2),
                mock.call(timeout=2),
            ],
            process.wait.call_args_list,
        )

    def test_interruption_propagates_and_removes_private_tempfile(self) -> None:
        spec = self.specs()[0]
        seen_path: Path | None = None

        def interrupted_runner(argv: list[str], _env: dict[str, str], _fd: int) -> int:
            nonlocal seen_path
            seen_path = Path(argv[-1])
            self.assertEqual(0o600, seen_path.stat().st_mode & 0o777)
            raise setup.TerminalSignal(signal.SIGINT)

        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaises(setup.TerminalSignal),
        ):
            setup.install_selected(
                [spec],
                {"HOME": directory, "PATH": "/nonexistent"},
                1,
                downloader=lambda _spec: b"#!/bin/sh\n",
                installer_runner=interrupted_runner,  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
            )
        self.assertIsNotNone(seen_path)
        assert seen_path is not None
        self.assertFalse(seen_path.exists())


class SetupOrchestrationTests(unittest.TestCase):
    def test_cli_setup_command_maps_manifest_or_tty_errors_to_exit_two(self) -> None:
        with (
            mock.patch.object(
                cli,
                "run_harness_setup",
                side_effect=setup.HarnessSetupError("no terminal"),
            ),
            mock.patch.object(cli.sys, "stderr", io.StringIO()) as stderr,
        ):
            result = cli.main(["setup", "harnesses", "--dry-run", "--no-color"])
        self.assertEqual(2, result)
        self.assertIn("no terminal", stderr.getvalue())

    def test_all_installed_succeeds_without_opening_tty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary_dir = root / "bin"
            for name in (
                "agy",
                "cline",
                "codex",
                "claude",
                "dsh",
                "goose",
                "grok",
                "hermes",
                "kimi",
                "muse",
                "opencode",
                "pi",
                "qwen",
                "zcode",
            ):
                executable(binary_dir / name)
            output = io.StringIO()
            result = setup.run_harness_setup(
                {"HOME": str(root / "home"), "PATH": str(binary_dir)},
                tty_path=str(root / "missing-tty"),
                output=output,
            )
        self.assertEqual(0, result)
        self.assertRegex(output.getvalue(), r"All \d+ harness CLIs are already detected")

    def test_missing_harnesses_without_tty_is_an_invocation_error(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(setup.HarnessSetupError, "interactive terminal"),
        ):
            setup.run_harness_setup(
                {"HOME": directory, "PATH": "/nonexistent"},
                tty_path=str(Path(directory) / "missing-tty"),
            )

    def test_full_dry_run_selection_downloads_but_never_runs_an_installer(self) -> None:
        fake = FakeSession([" ", "\r", "y"])
        downloader = mock.Mock(
            return_value=setup.InstallerDownload(
                b"#!/bin/sh\n", "https://example.test/i.sh", "b" * 64
            )
        )
        runner = mock.Mock(side_effect=AssertionError("must not run"))
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(setup, "TtySession", return_value=fake),
        ):
            result = setup.run_harness_setup(
                {"HOME": directory, "PATH": "/nonexistent", "TERM": "xterm"},
                dry_run=True,
                downloader=downloader,
                installer_runner=runner,
            )
        self.assertEqual(0, result)
        self.assertIn("dry-run", fake.output)
        self.assertIn("verifies", fake.output)
        self.assertTrue(fake.input_mode_restored)
        downloader.assert_called()
        runner.assert_not_called()

    def test_ctrl_c_maps_to_exit_130(self) -> None:
        fake = FakeSession([])
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(setup, "TtySession", return_value=fake),
            mock.patch.object(fake, "read_key", side_effect=setup.TerminalSignal(signal.SIGINT)),
        ):
            result = setup.run_harness_setup(
                {"HOME": directory, "PATH": "/nonexistent", "TERM": "xterm"},
            )
        self.assertEqual(130, result)


class PinnedPiToolchainTests(unittest.TestCase):
    """``pitwall agents setup pi`` installs the pinned Pi and its subagents backend."""

    PINNED = (
        "@earendil-works/pi-coding-agent@1.1.0",
        "@tintinweb/pi-subagents@0.19.0",
    )

    def pi_spec(self) -> setup.HarnessInstallSpec:
        return next(
            spec
            for spec in setup.load_install_specs(system_name="Linux")
            if spec.harness_id == "pi"
        )

    def test_the_pi_recipe_pins_both_packages(self) -> None:
        recipe = self.pi_spec().recipe
        self.assertEqual(
            ("npm", "@earendil-works/pi-coding-agent", "1.1.0"),
            (recipe.kind, recipe.package, recipe.version),
        )
        self.assertEqual((("@tintinweb/pi-subagents", "0.19.0"),), recipe.extra_packages)

    def test_extra_packages_must_be_pinned_npm_names(self) -> None:
        manifest = json.loads(
            (RESOURCE_ROOT / "config/harness-installers.json").read_text(encoding="utf-8")
        )
        for bad in (
            [{"package": "../evil", "version": "1.0.0"}],
            [{"package": "ok", "version": "latest"}],
            [{"package": "ok"}],
            [{"package": "ok", "version": "1.0.0", "flag": "--x"}],
            "not-a-list",
        ):
            with self.subTest(extra=bad), tempfile.TemporaryDirectory() as directory:
                broken = json.loads(json.dumps(manifest))
                for platform in ("darwin", "linux"):
                    broken["harnesses"]["pi"]["platforms"][platform]["extraPackages"] = bad
                config = Path(directory) / "config"
                config.mkdir()
                (config / "harness-installers.json").write_text(json.dumps(broken))
                (config / "harness-registry.json").write_text(
                    (RESOURCE_ROOT / "config/harness-registry.json").read_text(encoding="utf-8")
                )
                with self.assertRaises(setup.HarnessSetupError):
                    setup.load_install_specs(Path(directory), system_name="Linux")

    def test_install_selected_runs_one_npm_install_with_every_pinned_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            bin_dir = Path(directory) / "bin"
            executable(bin_dir / "npm")
            seen: list[list[str]] = []

            def runner(argv: list[str], child_env: dict[str, str], _fd: int) -> int:
                seen.append(list(argv))
                executable(Path(child_env["HOME"]) / ".local/bin/pi")
                return 0

            results = setup.install_selected(
                [self.pi_spec()],
                {"HOME": str(home), "PATH": str(bin_dir)},
                1,
                installer_runner=runner,  # type: ignore[arg-type]  # reason: the test passes a test double
                verifier=lambda *_: (True, "verified"),
            )
            self.assertEqual("installed", results[0].status)
            self.assertEqual(["install", "-g", "--ignore-scripts", *self.PINNED], seen[0][1:])

    def test_setup_pi_reinstalls_the_pins_even_when_pi_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            bin_dir = Path(directory) / "bin"
            executable(bin_dir / "npm")
            executable(home / ".local/bin/pi")
            seen: list[list[str]] = []

            def runner(argv: list[str], _env: dict[str, str], _fd: int) -> int:
                seen.append(list(argv))
                return 0

            output = io.StringIO()
            code = setup.run_pi_setup(
                {"HOME": str(home), "PATH": str(bin_dir)},
                output=output,
                installer_runner=runner,  # type: ignore[arg-type]  # reason: the test passes a test double
                verifier=lambda *_: (True, "verified"),
            )
            self.assertEqual(0, code)
            self.assertEqual(["install", "-g", "--ignore-scripts", *self.PINNED], seen[0][1:])
            self.assertIn("installed", output.getvalue())

    def test_setup_pi_dry_run_prints_the_command_and_runs_nothing(self) -> None:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            code = setup.run_pi_setup(
                {"HOME": directory, "PATH": directory},
                dry_run=True,
                output=output,
                installer_runner=mock.Mock(side_effect=AssertionError("must not run")),
            )
        self.assertEqual(0, code)
        self.assertIn("npm install -g --ignore-scripts " + " ".join(self.PINNED), output.getvalue())

    def test_setup_pi_reports_failure_with_a_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            code = setup.run_pi_setup({"HOME": directory, "PATH": "/nonexistent"}, output=output)
        self.assertEqual(1, code)
        self.assertIn("npm", output.getvalue())

    def test_the_cli_accepts_setup_pi(self) -> None:
        with mock.patch.object(cli, "run_pi_setup", return_value=0) as run:
            self.assertEqual(0, cli.main(["setup", "pi", "--dry-run"]))
        self.assertTrue(run.call_args.kwargs["dry_run"])


if __name__ == "__main__":
    unittest.main()
