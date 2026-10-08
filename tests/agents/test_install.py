"""`pitwall agents install` and `uninstall`: generated shims, plugins, and channel registration."""

from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import tomllib
import unittest
from collections.abc import Sequence
from pathlib import Path

from pitwall.agents import installation
from pitwall.agents.installation import InstallationError, install, shim_names, uninstall

SHIM_GUARD = (
    'command -v pitwall >/dev/null 2>&1 || { echo "pitwall: command not found" >&2; exit 127; }'
)

ROOT = Path(__file__).resolve().parents[2]
HARNESSES = (
    "agy",
    "claude",
    "cline",
    "codex",
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
)


def snapshot(root: Path) -> dict[str, str]:
    """Every path under *root* with its kind and, for files, its bytes."""

    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        found[relative] = path.read_text(encoding="utf-8") if path.is_file() else "<dir>"
    return found


class InstallSandbox:
    def __init__(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="pitwall-install-test-")
        self.root = Path(self._temp.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        self.pitwall = self.write_command("pitwall", "exit 0")
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
        }
        self.commands: list[tuple[str, ...]] = []

    def cleanup(self) -> None:
        self._temp.cleanup()

    def write_command(self, name: str, body: str) -> Path:
        target = self.bin / name
        target.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        target.chmod(0o755)
        return target

    def runner(self, argv: Sequence[str]) -> int:
        self.commands.append(tuple(argv))
        return 0

    def install(self, **kwargs: object) -> installation.InstallResult:
        return install(self.env, self.home, runner=self.runner, **kwargs)  # type: ignore[arg-type]  # reason: test helper forwards optional keyword arguments


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = InstallSandbox()
        self.addCleanup(self.sandbox.cleanup)

    def test_install_writes_all_shims_executable(self) -> None:
        self.sandbox.install(harnesses=(), plugin_hosts=())
        scripts = self.sandbox.home / ".claude" / "scripts"
        expected = {f"{name}-shim.sh" for name in HARNESSES} | {"route-shim.sh"}
        self.assertEqual(15, len(expected))
        self.assertEqual(expected, set(shim_names()))
        self.assertEqual(expected, {path.name for path in scripts.iterdir()})
        for name in expected:
            path = scripts / name
            self.assertEqual(0o755, stat.S_IMODE(path.stat().st_mode), name)
            target = name.removesuffix("-shim.sh")
            self.assertEqual(
                f'#!/usr/bin/env bash\n{SHIM_GUARD}; exec pitwall agents _shim {target} "$@"\n',
                path.read_text(encoding="utf-8"),
            )

    def test_shim_invokes_pitwall_agents(self) -> None:
        log = self.sandbox.root / "argv.log"
        self.sandbox.write_command("pitwall", f'printf "%s\\n" "$@" > "{log}"; exit 3')
        self.sandbox.install(harnesses=(), plugin_hosts=())
        scripts = self.sandbox.home / ".claude" / "scripts"
        for shim, target in (("codex-shim.sh", "codex"), ("route-shim.sh", "route")):
            result = subprocess.run(
                ["/bin/bash", str(scripts / shim), "prompt.md", "--flag"],
                env={"HOME": str(self.sandbox.home), "PATH": self.sandbox.env["PATH"]},
                capture_output=True,
                check=False,
            )
            self.assertEqual(3, result.returncode)
            self.assertEqual(
                ["agents", "_shim", target, "prompt.md", "--flag"],
                log.read_text(encoding="utf-8").splitlines(),
            )

    def test_shim_runs_under_the_pitwall_console_script_never_a_bare_python3(self) -> None:
        marker = self.sandbox.root / "python3-ran"
        self.sandbox.write_command("python3", f'touch "{marker}"; exit 97')
        self.sandbox.write_command("pitwall", "echo ran-by-pitwall")
        self.sandbox.install(harnesses=(), plugin_hosts=())
        for shim in shim_names():
            text = (self.sandbox.home / ".claude" / "scripts" / shim).read_text(encoding="utf-8")
            self.assertNotIn("python", text, shim)
        result = subprocess.run(
            ["/bin/bash", str(self.sandbox.home / ".claude" / "scripts" / "kimi-shim.sh")],
            env={"HOME": str(self.sandbox.home), "PATH": self.sandbox.env["PATH"]},
            capture_output=True,
            check=False,
        )
        self.assertEqual((0, b"ran-by-pitwall\n"), (result.returncode, result.stdout))
        self.assertFalse(marker.exists())

    def test_plugins_are_installed_with_generated_references_and_the_local_marketplace(
        self,
    ) -> None:
        self.sandbox.install(harnesses=(), plugin_hosts=())
        plugins = self.sandbox.home / ".local" / "share" / "pitwall" / "plugins"
        self.assertTrue((plugins / "claude" / ".claude-plugin" / "plugin.json").is_file())
        self.assertTrue((plugins / "codex" / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((plugins / "copilot" / "plugin.json").is_file())
        source = installation.generated_source()
        for host in ("claude", "codex", "copilot"):
            references = plugins / host / "skills" / "subagent-model-routing" / "references"
            for name in installation.GENERATED_FILES:
                self.assertEqual(
                    (source / name).read_bytes(), (references / name).read_bytes(), (host, name)
                )
        for relative in (
            ".claude-plugin/marketplace.json",
            ".agents/plugins/marketplace.json",
            ".github/plugin/marketplace.json",
        ):
            manifest = json.loads((plugins / relative).read_text(encoding="utf-8"))
            self.assertEqual("pitwall-local", manifest["name"], relative)
        claude = json.loads((plugins / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(["pitwall"], [entry["name"] for entry in claude["plugins"]])

    def test_plugin_registration_commands_use_the_local_marketplace(self) -> None:
        self.sandbox.install(harnesses=(), plugin_hosts=("claude", "codex", "copilot"))
        joined = [" ".join(command) for command in self.sandbox.commands]
        self.assertTrue(any(c.startswith("claude plugin marketplace add") for c in joined))
        self.assertIn("claude plugin install pitwall@pitwall-local --scope user", joined)
        self.assertIn("codex plugin add pitwall-codex@pitwall-local", joined)
        self.assertIn("copilot plugin install pitwall-copilot@pitwall-local", joined)

    def reinstall(
        self, failing: Sequence[str], listing: str, hosts: Sequence[str]
    ) -> installation.InstallResult:
        """Install with a runner that fails commands starting with *failing*."""

        def runner(argv: Sequence[str]) -> int:
            self.sandbox.commands.append(tuple(argv))
            return 1 if tuple(argv[: len(failing)]) == tuple(failing) else 0

        def lister(argv: Sequence[str]) -> str:
            self.assertEqual(("copilot", "plugin", "marketplace", "list"), tuple(argv))
            return listing

        return install(
            self.sandbox.env,
            self.sandbox.home,
            harnesses=(),
            plugin_hosts=hosts,
            runner=runner,
            lister=lister,
        )

    def test_copilot_already_registered_marketplace_is_not_a_failure(self) -> None:
        result = self.reinstall(
            ("copilot", "plugin", "marketplace", "add"),
            "Registered marketplaces:\n  \u2022 pitwall-local (Local: /plugins)\n",
            ("copilot",),
        )
        self.assertEqual([], result.warnings)
        joined = [" ".join(command) for command in self.sandbox.commands]
        self.assertIn("copilot plugin install pitwall-copilot@pitwall-local", joined)
        locations = installation.InstallLocations.for_home(self.sandbox.home, self.sandbox.env)
        manifest = json.loads(locations.manifest.read_text(encoding="utf-8"))
        self.assertEqual(["copilot"], manifest["pluginHosts"])

    def test_copilot_marketplace_add_failure_still_warns(self) -> None:
        result = self.reinstall(
            ("copilot", "plugin", "marketplace", "add"),
            "Registered marketplaces:\n  \u2022 other (Local: /elsewhere)\n",
            ("copilot",),
        )
        self.assertEqual(1, len(result.warnings))
        self.assertIn(
            "copilot: plugin registration failed: copilot plugin marketplace add",
            result.warnings[0],
        )

    def test_copilot_plugin_install_failure_still_warns_when_registered(self) -> None:
        result = self.reinstall(("copilot", "plugin", "install"), "pitwall-local", ("copilot",))
        self.assertEqual(1, len(result.warnings))
        self.assertIn("copilot plugin install", result.warnings[0])

    def test_claude_and_codex_add_failures_are_not_forgiven(self) -> None:
        for host in ("claude", "codex"):
            result = self.reinstall(
                (host, "plugin", "marketplace", "add"), "pitwall-local", (host,)
            )
            self.assertEqual(1, len(result.warnings), host)

    def test_channel_server_registered_as_pitwall_mcp(self) -> None:
        self.sandbox.install(harnesses=("codex", "kimi"), plugin_hosts=())
        pitwall = str(self.sandbox.pitwall.resolve())
        codex = tomllib.loads(
            (self.sandbox.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        )["mcp_servers"]["pitwall-channel"]
        self.assertEqual((pitwall, ["mcp", "serve", "channel"]), (codex["command"], codex["args"]))
        kimi = json.loads(
            (self.sandbox.home / ".kimi-code" / "mcp.json").read_text(encoding="utf-8")
        )["mcpServers"]["pitwall-channel"]
        self.assertEqual((pitwall, ["mcp", "serve", "channel"]), (kimi["command"], kimi["args"]))

    def test_uninstall_removes_exactly_installed_files(self) -> None:
        home = self.sandbox.home
        (home / ".claude" / "scripts").mkdir(parents=True)
        (home / ".claude" / "scripts" / "mine.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").write_text('model = "x"\n', encoding="utf-8")
        before = snapshot(home)

        result = self.sandbox.install(harnesses=("codex", "kimi"), plugin_hosts=("claude",))
        self.assertNotEqual(before, snapshot(home))
        self.assertIn(home / ".claude" / "scripts" / "codex-shim.sh", result.files)

        removal = uninstall(self.sandbox.env, home, runner=self.sandbox.runner)
        self.assertEqual([], removal.warnings)
        self.maxDiff = None
        self.assertEqual(before, snapshot(home))

    def test_uninstall_without_an_install_removes_nothing(self) -> None:
        (self.sandbox.home / "keep.txt").write_text("keep\n", encoding="utf-8")
        before = snapshot(self.sandbox.home)
        removal = uninstall(self.sandbox.env, self.sandbox.home, runner=self.sandbox.runner)
        self.assertEqual([], removal.removed)
        self.assertEqual(before, snapshot(self.sandbox.home))

    def test_reinstall_is_idempotent(self) -> None:
        self.sandbox.install(harnesses=("codex",), plugin_hosts=())
        first = snapshot(self.sandbox.home)
        self.sandbox.install(harnesses=("codex",), plugin_hosts=())
        self.assertEqual(first, snapshot(self.sandbox.home))

    def test_install_refuses_to_overwrite_a_foreign_shim(self) -> None:
        scripts = self.sandbox.home / ".claude" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "codex-shim.sh").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        before = snapshot(self.sandbox.home)
        with self.assertRaisesRegex(InstallationError, "codex-shim.sh"):
            self.sandbox.install(harnesses=(), plugin_hosts=())
        self.assertEqual(before, snapshot(self.sandbox.home))

    def test_shims_are_not_committed(self) -> None:
        self.assertFalse((ROOT / "src/pitwall/agents/resources/scripts").exists())


if __name__ == "__main__":
    unittest.main()
