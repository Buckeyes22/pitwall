"""Provider adapters own provider-specific argv and environment only."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pitwall.agents.errors import (
    ProfileSyncError,
    UsageError,
)
from pitwall.agents.harnesses import adapter_ids, get_adapter

ROOT = Path(__file__).resolve().parents[2]
RESTRICTED_ENV = {"PITWALL_AGENTS_UNRESTRICTED": "0"}
UNRESTRICTED_ENV = {"PITWALL_AGENTS_UNRESTRICTED": "1"}


class ProviderAdapterTests(unittest.TestCase):
    def test_registry_and_adapter_ids_match(self) -> None:
        self.assertEqual(
            {
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
            },
            adapter_ids(),
        )

    def test_dsh_prepare_and_settings_sync(self) -> None:
        adapter = get_adapter("dsh")
        request = adapter.parse(["prompt.md", "--verbose"], UNRESTRICTED_ENV, Path("/tmp"))
        prepared = adapter.prepare(request, "/bin/dsh", b"prompt\n", UNRESTRICTED_ENV, {})
        self.assertEqual(
            ["/bin/dsh", "--profile", "headless", "--verbose", "prompt"], prepared.argv
        )
        selected = adapter.parse(
            ["prompt.md", "--profile", "other"], UNRESTRICTED_ENV, Path("/tmp")
        )
        self.assertEqual(
            ["/bin/dsh", "--profile", "other", "prompt"],
            adapter.prepare(selected, "/bin/dsh", b"prompt\n", UNRESTRICTED_ENV, {}).argv,
        )
        for flag in ("--patch", "--dump-config", "--dump-default-config", "web"):
            with self.subTest(flag=flag), self.assertRaisesRegex(UsageError, "managed by the shim"):
                adapter.parse(["prompt.md", flag], {}, Path("/tmp"))
        entry = {
            "harness": "dsh",
            "model": "route-model",
            "endpoint": {"baseUrl": "http://localhost:9292/v1", "apiKeyEnv": "MY_ENDPOINT_KEY"},
        }
        self.assertEqual(["--profile", "headless"], adapter.endpoint_argv("route", entry))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings_path = home / ".dsh" / "settings.yaml"
            self.assertEqual("missing", adapter.endpoint_sync_status("route", entry, {}, home))
            plans = adapter.plan_endpoint_sync({"route": entry}, {}, home)
            self.assertEqual(1, len(plans))
            self.assertEqual(settings_path, plans[0][0])
            self.assertEqual(
                "llm-pi-ai:\n"
                "  providers:\n"
                "    # managed by pitwall\n"
                "    route:\n"
                "      api: openai-completions\n"
                "      baseURL: http://localhost:9292/v1\n"
                "      apiKeyEnv: MY_ENDPOINT_KEY\n"
                "      models:\n"
                "        - id: route-model\n"
                "          contextWindow: 32768\n"
                "          maxTokens: 4096\n"
                "agent-default-model:\n"
                "  provider: route\n"
                "  model: route-model\n",
                plans[0][2],
            )
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(plans[0][2], encoding="utf-8")
            self.assertEqual("synced", adapter.endpoint_sync_status("route", entry, {}, home))
            settings_path.write_text(
                plans[0][2].replace("route-model", "other-model", 1), encoding="utf-8"
            )
            self.assertEqual("stale", adapter.endpoint_sync_status("route", entry, {}, home))
            settings_path.write_text("telemetry:\n  enabled: false  # keep me\n", encoding="utf-8")
            merged = adapter.plan_endpoint_sync({"route": entry}, {}, home)[0][2]
            self.assertIn("telemetry:\n  enabled: false  # keep me\n", merged)
            self.assertIn("\nllm-pi-ai:\n  providers:\n", merged)
            settings_path.write_text(merged, encoding="utf-8")
            pruned = adapter.plan_endpoint_sync({}, {}, home)[0][2]
            self.assertEqual(
                "telemetry:\n  enabled: false  # keep me\nllm-pi-ai:\n  providers:\n", pruned
            )
            settings_path.write_text(pruned, encoding="utf-8")
            self.assertEqual("missing", adapter.endpoint_sync_status("route", entry, {}, home))
        with self.assertRaisesRegex(
            ProfileSyncError, "routes first, second resolve to harness dsh"
        ):
            adapter.plan_endpoint_sync({"second": entry, "first": entry}, {}, Path("/tmp"))

    def test_hermes_config_sync_materializes_a_named_provider(self) -> None:
        adapter = get_adapter("hermes")
        entry = {
            "harness": "hermes",
            "model": "route-model",
            "endpoint": {"baseUrl": "http://localhost:9292/v1", "apiKeyEnv": "MY_ENDPOINT_KEY"},
        }
        # the route name selects the provider hermes will resolve credentials from
        self.assertEqual(["--provider", "route"], adapter.endpoint_argv("route", entry))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config_path = home / ".hermes" / "config.yaml"
            self.assertEqual("missing", adapter.endpoint_sync_status("route", entry, {}, home))
            plans = adapter.plan_endpoint_sync({"route": entry}, {}, home)
            self.assertEqual(1, len(plans))
            self.assertEqual(config_path, plans[0][0])
            self.assertEqual(
                "providers:\n"
                "  # managed by pitwall\n"
                "  route:\n"
                '    base_url: "http://localhost:9292/v1"\n'
                "    api_mode: chat_completions\n"
                "    key_env: MY_ENDPOINT_KEY\n",
                plans[0][2],
            )
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(plans[0][2], encoding="utf-8")
            self.assertEqual("synced", adapter.endpoint_sync_status("route", entry, {}, home))
            # a changed endpoint is detected as stale, not silently accepted
            moved = {
                **entry,
                "endpoint": {"baseUrl": "http://elsewhere:9292/v1", "apiKeyEnv": "MY_ENDPOINT_KEY"},
            }
            self.assertEqual("stale", adapter.endpoint_sync_status("route", moved, {}, home))
            # dropping the route prunes only the managed block
            pruned = adapter.plan_endpoint_sync({}, {}, home)[0][2]
            self.assertNotIn("  route:\n", pruned)

    def test_hermes_sync_preserves_user_providers_and_honors_hermes_home(self) -> None:
        adapter = get_adapter("hermes")
        entry = {
            "harness": "hermes",
            "model": "m",
            "endpoint": {"baseUrl": "http://localhost:9292/v1"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hermes_home = root / "custom-hermes"
            config = hermes_home / "config.yaml"
            config.parent.mkdir()
            config.write_text(
                "model:\n"
                "  default: something\n"
                "providers:\n"
                "  mine:\n"
                '    base_url: "https://example.invalid/v1"\n'
                "    api_mode: chat_completions\n"
                "runtime:\n"
                "  keep: true\n",
                encoding="utf-8",
            )
            plan = adapter.plan_endpoint_sync(
                {"route": entry}, {"HERMES_HOME": str(hermes_home)}, root
            )[0]
            self.assertEqual(config, plan[0])
            merged = plan[2]
            # the user's own provider and unrelated sections survive untouched
            self.assertIn('  mine:\n    base_url: "https://example.invalid/v1"\n', merged)
            self.assertIn("runtime:\n  keep: true\n", merged)
            self.assertIn("  route:\n", merged)
            # no apiKeyEnv on this entry means no key_env line at all
            self.assertNotIn("key_env", merged)
            # re-syncing is idempotent rather than appending a second block
            config.write_text(merged, encoding="utf-8")
            again = adapter.plan_endpoint_sync(
                {"route": entry}, {"HERMES_HOME": str(hermes_home)}, root
            )[0][2]
            self.assertEqual(merged, again)

    def test_dsh_sync_preserves_unmanaged_provider_and_honors_dsh_home(self) -> None:
        adapter = get_adapter("dsh")
        entry = {
            "harness": "dsh",
            "model": "route-model",
            "endpoint": {"baseUrl": "http://localhost:9292/v1"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dsh_home = root / "custom-dsh"
            settings = dsh_home / "settings.yaml"
            settings.parent.mkdir()
            settings.write_text(
                "llm-pi-ai:\n"
                "  providers:\n"
                "    user-provider:\n"
                "      api: anthropic-messages\n"
                "      models: [user-model] # preserve\n",
                encoding="utf-8",
            )
            plan = adapter.plan_endpoint_sync({"route": entry}, {"DSH_HOME": str(dsh_home)}, root)[
                0
            ]
            self.assertEqual(settings, plan[0])
            self.assertIn(
                "    user-provider:\n      api: anthropic-messages\n      models: [user-model] # preserve\n",
                plan[2],
            )
            self.assertNotIn("apiKeyEnv", plan[2])
            settings.write_text(plan[2], encoding="utf-8")
            pruned = adapter.plan_endpoint_sync({}, {"DSH_HOME": str(dsh_home)}, root)[0][2]
            self.assertIn("user-provider", pruned)
            self.assertNotIn("    route:\n", pruned)

    def test_cline_parse_prepare_and_endpoint_sync(self) -> None:
        adapter = get_adapter("cline")
        for flag in ("--json", "-i", "--tui", "--acp", "-z", "--zen"):
            with self.subTest(flag=flag), self.assertRaisesRegex(UsageError, "managed by the shim"):
                adapter.parse(["prompt.md", flag], {}, Path("/tmp"))
        request = adapter.parse(["prompt.md", "-m", "x"], {}, Path("/tmp"))
        self.assertEqual(("x", True), (request.model, request.has_model_override))
        prepared = adapter.prepare(request, "/bin/cline", b"prompt\n", UNRESTRICTED_ENV, {})
        self.assertEqual(
            ["/bin/cline", "-m", "x", "--auto-approve", "true", "prompt"], prepared.argv
        )
        restricted = adapter.prepare(
            request, "/bin/cline", b"prompt\n", {"PITWALL_AGENTS_UNRESTRICTED": "0"}, {}
        )
        self.assertEqual("false", restricted.argv[-2])
        entry = {
            "harness": "cline",
            "model": "route-model",
            "endpoint": {"baseUrl": "http://gpu-1:8000/v1", "apiKeyEnv": "CLINE_API_KEY"},
        }
        self.assertEqual(["-P", "openai-compatible"], adapter.endpoint_argv("route", entry))
        self.assertEqual(
            [
                [
                    "/bin/cline",
                    "auth",
                    "--provider",
                    "openai-compatible",
                    "--baseurl",
                    "http://gpu-1:8000/v1",
                    "--modelid",
                    "route-model",
                    "--apikey",
                    "{env:CLINE_API_KEY}",
                ]
            ],
            adapter.endpoint_sync_commands(
                {"route": entry}, {"CLINE_BIN": "/bin/cline"}, Path("/tmp")
            ),
        )
        self.assertEqual([], adapter.endpoint_sync_commands({}, {}, Path("/tmp")))
        with self.assertRaisesRegex(
            ProfileSyncError, "routes first, second resolve to harness cline"
        ):
            adapter.endpoint_sync_commands({"second": entry, "first": entry}, {}, Path("/tmp"))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertEqual("missing", adapter.endpoint_sync_status("route", entry, {}, home))
            settings = home / ".cline" / "data" / "settings"
            settings.mkdir(parents=True)
            (settings / "providers.json").write_text(
                '{"openai-compatible":{"base":"http://other"}}', encoding="utf-8"
            )
            self.assertEqual("stale", adapter.endpoint_sync_status("route", entry, {}, home))
            (settings / "providers.json").write_text(
                '{"base":"http://gpu-1:8000/v1","model":"route-model"}', encoding="utf-8"
            )
            self.assertEqual("synced", adapter.endpoint_sync_status("route", entry, {}, home))
            self.assertEqual([], adapter.endpoint_sync_commands({"route": entry}, {}, home))

    def test_goose_configured_model_prepare_and_endpoint_environment(self) -> None:
        adapter = get_adapter("goose")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".config" / "goose"
            config.mkdir(parents=True)
            (config / "config.yaml").write_text("GOOSE_MODEL: configured-model\n", encoding="utf-8")
            configured = adapter.parse(["prompt.md"], {}, home)
            environment = adapter.parse(["prompt.md"], {"GOOSE_MODEL": "environment-model"}, home)
            explicit = adapter.parse(
                ["prompt.md", "--model", "explicit-model"],
                {"GOOSE_MODEL": "environment-model"},
                home,
            )
        self.assertEqual("configured-model", configured.model)
        self.assertEqual("environment-model", environment.model)
        self.assertEqual(("explicit-model", True), (explicit.model, explicit.has_model_override))
        self.assertEqual("goose-default", adapter.parse(["prompt.md"], {}, Path("/missing")).model)
        for flag in (
            "--instructions",
            "-t",
            "--text=other",
            "--output-format",
            "-s",
            "--interactive",
            "-q",
            "--quiet",
            "--no-session",
        ):
            with self.subTest(flag=flag), self.assertRaisesRegex(UsageError, "managed by the shim"):
                adapter.parse(["prompt.md", flag], {}, Path("/tmp"))
        request = adapter.parse(["prompt.md", "--max-turns", "20"], {}, Path("/tmp"))
        prepared = adapter.prepare(request, "/bin/goose", b"prompt\\n", UNRESTRICTED_ENV, {})
        self.assertEqual(
            [
                "/bin/goose",
                "run",
                "--no-session",
                "-q",
                "--output-format",
                "text",
                "--max-turns",
                "20",
                "--instructions",
                "-",
            ],
            prepared.argv,
        )
        self.assertEqual(b"prompt\\n", prepared.stdin)
        self.assertEqual("auto", prepared.env["GOOSE_MODE"])
        restricted = adapter.prepare(
            request, "/bin/goose", b"prompt\\n", {"PITWALL_AGENTS_UNRESTRICTED": "0"}, {}
        )
        self.assertNotIn("GOOSE_MODE", restricted.env)
        self.assertEqual(
            {
                "OPENAI_HOST": "http://gpu-1:8000",
                "OPENAI_BASE_PATH": "v1/chat/completions",
                "GOOSE_PROVIDER": "openai",
            },
            adapter.endpoint_environment_extras({}, {"OPENAI_HOST": "http://gpu-1:8000/v1"}),
        )
        self.assertEqual(
            "v1/chat/completions",
            adapter.endpoint_environment_extras({}, {"OPENAI_HOST": "https://h/"})[
                "OPENAI_BASE_PATH"
            ],
        )

    def test_muse_parse_prepare_and_reserved_flags(self) -> None:
        adapter = get_adapter("muse")
        default = adapter.parse(["prompt.md"], {}, Path("/tmp"))
        override = adapter.parse(["prompt.md", "--model=muse-spark-test"], {}, Path("/tmp"))
        self.assertEqual(("muse-spark-1.3", False), (default.model, default.has_model_override))
        self.assertEqual(("muse-spark-test", True), (override.model, override.has_model_override))
        for flag in ("--json", "--prompt-file=other.md", "--session-id"):
            with self.subTest(flag=flag), self.assertRaisesRegex(UsageError, "managed by the shim"):
                adapter.parse(["prompt.md", flag], {}, Path("/tmp"))
        prepared = adapter.prepare(
            default,
            "/bin/muse",
            b"prompt\\n",
            UNRESTRICTED_ENV,
            {"promptFile": "/run/prompt.deliver.md"},
        )
        self.assertEqual(
            [
                "/bin/muse",
                "exec",
                "--yolo",
                "--model",
                "muse-spark-1.3",
                "--prompt-file",
                "/run/prompt.deliver.md",
            ],
            prepared.argv,
        )
        self.assertEqual(
            "/custom/muse",
            adapter.resolve_binary({"MUSE_BIN": "/custom/muse"}, Path("/tmp")),
        )

    def test_hermes_configured_model_reserved_flags_and_endpoint_argv(self) -> None:
        adapter = get_adapter("hermes")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".hermes"
            config.mkdir()
            (config / "config.yaml").write_text(
                "model:\n  default: configured-model\n", encoding="utf-8"
            )
            configured = adapter.parse(["prompt.md"], {}, home)
            environment = adapter.parse(["prompt.md"], {"HERMES_MODEL": "environment-model"}, home)
            explicit = adapter.parse(
                ["prompt.md", "-m", "explicit-model"],
                {"HERMES_MODEL": "environment-model"},
                home,
            )
            bare = adapter.parse(["prompt.md"], {}, home / "empty")
        self.assertEqual("configured-model", configured.model)
        self.assertEqual("environment-model", environment.model)
        self.assertEqual(("explicit-model", True), (explicit.model, explicit.has_model_override))
        self.assertEqual("hermes-default", bare.model)
        for flag in ("-z", "-q", "--query", "--query-file=x", "-Q", "--quiet"):
            with self.subTest(flag=flag), self.assertRaisesRegex(UsageError, "managed by the shim"):
                adapter.parse(["prompt.md", flag], {}, Path("/tmp"))
        # the route name, so hermes resolves the provider profiles sync materialized
        self.assertEqual(["--provider", "glimmer"], adapter.endpoint_argv("glimmer", {}))

    def test_hermes_tools_use_dispatch_workspace_including_isolated_worktrees(self) -> None:
        adapter = get_adapter("hermes")
        for inherited in ({}, {"TERMINAL_CWD": "/wrong/inherited/workspace"}):
            with self.subTest(inherited=inherited):
                env = dict(inherited)
                request = adapter.parse(["prompt.md"], env, Path("/tmp"))
                prepared = adapter.prepare(
                    request,
                    "/bin/hermes",
                    b"repair fixture",
                    env,
                    {"workspacePath": "/owned/isolated/worktree"},
                )
                self.assertEqual("/owned/isolated/worktree", prepared.env.get("TERMINAL_CWD"))
                self.assertEqual(inherited, env)

    def test_hermes_prepare_respects_restricted_policy(self) -> None:
        adapter = get_adapter("hermes")
        request = adapter.parse(["prompt.md"], {}, Path("/tmp"))
        prepared = adapter.prepare(request, "/bin/hermes", b"hello\n", UNRESTRICTED_ENV, {})
        self.assertEqual(["/bin/hermes", "--yolo", "-z", "hello"], prepared.argv)
        restricted = adapter.prepare(
            request,
            "/bin/hermes",
            b"hello\n",
            {"PITWALL_AGENTS_UNRESTRICTED": "0"},
            {},
        )
        self.assertEqual(["/bin/hermes", "-z", "hello"], restricted.argv)

    def test_pi_prepare_and_reserved_flags(self) -> None:
        adapter = get_adapter("pi")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".pi" / "agent").mkdir(parents=True)
            (home / ".pi" / "agent" / "settings.json").write_text(
                json.dumps({"defaultModel": {"provider": "openai", "id": "gpt-x"}}),
                encoding="utf-8",
            )
            request = adapter.parse(["/tmp/p.md", "--thinking", "high"], {}, home)
            self.assertEqual(("gpt-x", False), (request.model, request.has_model_override))
            delivery = {"promptFile": "/run/prompt.deliver.md"}
            prepared = adapter.prepare(request, "/bin/pi", b"hello\n", UNRESTRICTED_ENV, delivery)
            self.assertEqual(
                [
                    "/bin/pi",
                    "-p",
                    "--no-session",
                    "--approve",
                    "--thinking",
                    "high",
                    "@/run/prompt.deliver.md",
                ],
                prepared.argv,
            )
            restricted = adapter.prepare(
                request,
                "/bin/pi",
                b"hello\n",
                {"PITWALL_AGENTS_UNRESTRICTED": "0"},
                delivery,
            )
            self.assertIn("--no-approve", restricted.argv)
            self.assertEqual("@/run/prompt.deliver.md", prepared.sanitized_args[-1])
            for flag in ("-p", "--print", "--mode=json", "--export"):
                with self.subTest(flag=flag), self.assertRaises(UsageError):
                    adapter.parse(["/tmp/p.md", flag], {}, home)
            self.assertEqual(["--provider", "glimmer"], adapter.endpoint_argv("glimmer", {}))

    def test_pi_sync_plan_and_status(self) -> None:
        adapter = get_adapter("pi")
        entry = {
            "model": "meta-models/Muse-Glimmer-30B",
            "endpoint": {"baseUrl": "http://gpu-1:8000/v1", "apiKeyEnv": "GLIMMER_API_KEY"},
            "args": [],
            "env": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertEqual("missing", adapter.endpoint_sync_status("glimmer", entry, {}, home))
            path, before, after = adapter.plan_endpoint_sync({"glimmer": entry}, {}, home)
            self.assertEqual(home / ".pi" / "agent" / "models.json", path)
            block = json.loads(after)["providers"]["glimmer"]
            self.assertEqual(
                {
                    "baseUrl": "http://gpu-1:8000/v1",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "meta-models/Muse-Glimmer-30B",
                            "contextWindow": 32768,
                            "maxTokens": 4096,
                        }
                    ],
                    "apiKey": "$GLIMMER_API_KEY",
                },
                block,
            )
            path.parent.mkdir(parents=True)
            path.write_text(after, encoding="utf-8")
            self.assertEqual("synced", adapter.endpoint_sync_status("glimmer", entry, {}, home))
            self.assertEqual(after, adapter.plan_endpoint_sync({"glimmer": entry}, {}, home)[2])

        limited = {**entry, "limits": {"context": 65536, "output": 2048}}
        after = adapter.plan_endpoint_sync({"glimmer": limited}, {}, Path("/tmp"))[2]
        self.assertEqual(
            {"id": "meta-models/Muse-Glimmer-30B", "contextWindow": 65536, "maxTokens": 2048},
            json.loads(after)["providers"]["glimmer"]["models"][0],
        )

    def test_pi_sync_preserves_enriched_provider_and_models(self) -> None:
        adapter = get_adapter("pi")
        entry = {
            "model": "local/qwen",
            "endpoint": {"baseUrl": "http://gpu-1:8000/v1", "apiKeyEnv": "LOCAL_KEY"},
            "limits": {"context": 65536, "output": 4096},
        }
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / ".pi" / "agent" / "models.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "local": {
                                "baseUrl": "http://gpu-1:8000/v1",
                                "api": "openai-completions",
                                "apiKey": "$OLD_KEY",
                                "compat": {"supportsImages": True},
                                "headers": {"X-Client": "keep"},
                                "models": [
                                    {
                                        "id": "local/qwen",
                                        "contextWindow": 106496,
                                        "maxTokens": 32768,
                                        "reasoning": True,
                                        "thinkingLevelMap": {"high": "deep"},
                                        "input": ["text", "image"],
                                        "samplingParams": {"temperature": 0.2},
                                    },
                                    {
                                        "id": "local/other",
                                        "contextWindow": 8192,
                                        "maxTokens": 1024,
                                        "custom": "keep",
                                    },
                                ],
                            },
                        },
                        "defaults": {"provider": "local"},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            planned = adapter.plan_endpoint_sync({"local": entry}, {}, home)[2]
            result = json.loads(planned)
            provider = result["providers"]["local"]
            self.assertEqual({"supportsImages": True}, provider["compat"])
            self.assertEqual({"X-Client": "keep"}, provider["headers"])
            self.assertEqual("$LOCAL_KEY", provider["apiKey"])
            self.assertEqual(
                {
                    "id": "local/qwen",
                    "contextWindow": 65536,
                    "maxTokens": 4096,
                    "reasoning": True,
                    "thinkingLevelMap": {"high": "deep"},
                    "input": ["text", "image"],
                    "samplingParams": {"temperature": 0.2},
                },
                provider["models"][0],
            )
            self.assertEqual("local/other", provider["models"][1]["id"])
            self.assertEqual("keep", provider["models"][1]["custom"])
            path.write_text(planned, encoding="utf-8")
            self.assertEqual("synced", adapter.endpoint_sync_status("local", entry, {}, home))
            self.assertEqual(planned, adapter.plan_endpoint_sync({"local": entry}, {}, home)[2])

    def test_pi_sync_rejects_endpoint_and_api_conflicts(self) -> None:
        adapter = get_adapter("pi")
        entry = {"model": "m", "endpoint": {"baseUrl": "http://new/v1"}}
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / ".pi" / "agent" / "models.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "route": {
                                "baseUrl": "http://old/v1",
                                "api": "openai-completions",
                                "models": [],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ProfileSyncError, "points elsewhere"):
                adapter.plan_endpoint_sync({"route": entry}, {}, home)
            path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "route": {
                                "baseUrl": "http://new/v1",
                                "api": "anthropic-messages",
                                "models": [],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ProfileSyncError, "uses API"):
                adapter.plan_endpoint_sync({"route": entry}, {}, home)
            path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "route": {
                                "baseUrl": "http://new/v1",
                                "api": "openai-completions",
                                "models": [{"id": "m", "api": "anthropic-messages"}],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ProfileSyncError, "model .* uses API"):
                adapter.plan_endpoint_sync({"route": entry}, {}, home)

    def test_pi_sync_rejects_model_endpoint_override(self) -> None:
        adapter = get_adapter("pi")
        entry = {"model": "m", "endpoint": {"baseUrl": "http://new/v1"}}
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / ".pi" / "agent" / "models.json"
            path.parent.mkdir(parents=True)
            original = json.dumps(
                {
                    "providers": {
                        "route": {
                            "baseUrl": "http://new/v1",
                            "api": "openai-completions",
                            "models": [
                                {
                                    "id": "m",
                                    "baseUrl": "http://other/v1",
                                    "contextWindow": 32768,
                                    "maxTokens": 4096,
                                }
                            ],
                        }
                    }
                }
            )
            path.write_text(original, encoding="utf-8")
            self.assertEqual("stale", adapter.endpoint_sync_status("route", entry, {}, home))
            with self.assertRaisesRegex(ProfileSyncError, "model .* points elsewhere"):
                adapter.plan_endpoint_sync({"route": entry}, {}, home)
            self.assertEqual(original, path.read_text(encoding="utf-8"))

    def test_pi_sync_rejects_duplicate_exact_model_ids(self) -> None:
        adapter = get_adapter("pi")
        entry = {"model": "m", "endpoint": {"baseUrl": "http://new/v1"}}
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / ".pi" / "agent" / "models.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "route": {
                                "baseUrl": "http://new/v1",
                                "api": "openai-completions",
                                "models": [{"id": "m"}, {"id": "m"}],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ProfileSyncError, "duplicate model id"):
                adapter.plan_endpoint_sync({"route": entry}, {}, home)

    def test_pi_sync_status_detects_owned_endpoint_api_and_limit_drift(self) -> None:
        adapter = get_adapter("pi")
        entry = {
            "model": "m",
            "endpoint": {"baseUrl": "http://localhost:8000/v1", "apiKeyEnv": "LOCAL_KEY"},
            "limits": {"context": 65536, "output": 4096},
        }
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            custom_agent = home / "dedicated-pi-agent"
            path = custom_agent / "models.json"
            path.parent.mkdir(parents=True)
            base = {
                "providers": {
                    "route": {
                        "baseUrl": "http://localhost:8000/v1",
                        "api": "openai-completions",
                        "apiKey": "$LOCAL_KEY",
                        "models": [{"id": "m", "contextWindow": 65536, "maxTokens": 4096}],
                    }
                }
            }
            path.write_text(json.dumps(base), encoding="utf-8")
            env = {"PI_CODING_AGENT_DIR": str(custom_agent)}
            self.assertEqual("synced", adapter.endpoint_sync_status("route", entry, env, home))
            for change in (
                {"baseUrl": "http://elsewhere:8000/v1"},
                {"api": "anthropic-messages"},
                {"models": [{"id": "m", "contextWindow": 32768, "maxTokens": 4096}]},
                {"models": [{"id": "m", "contextWindow": 65536, "maxTokens": 2048}]},
                {
                    "models": [
                        {
                            "id": "m",
                            "api": "anthropic-messages",
                            "contextWindow": 65536,
                            "maxTokens": 4096,
                        }
                    ]
                },
            ):
                candidate = json.loads(json.dumps(base))
                candidate["providers"]["route"].update(change)
                path.write_text(json.dumps(candidate), encoding="utf-8")
                self.assertEqual(
                    "stale", adapter.endpoint_sync_status("route", entry, env, home), change
                )

            for models in (
                [
                    {"id": "m", "contextWindow": 65536, "maxTokens": 4096},
                    {"id": "m", "contextWindow": 65536, "maxTokens": 4096},
                ],
                [{"id": "m", "contextWindow": 65536, "maxTokens": 4096}, "malformed"],
            ):
                candidate = json.loads(json.dumps(base))
                candidate["providers"]["route"]["models"] = models
                path.write_text(json.dumps(candidate), encoding="utf-8")
                self.assertEqual("stale", adapter.endpoint_sync_status("route", entry, env, home))

    def test_pi_keyless_sync_has_no_dummy_auth_and_preserves_existing_auth(self) -> None:
        adapter = get_adapter("pi")
        entry = {"model": "local/m", "endpoint": {"baseUrl": "http://localhost:8000/v1"}}
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / ".pi" / "agent" / "models.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "route": {
                                "baseUrl": "http://localhost:8000/v1",
                                "api": "openai-completions",
                                "apiKey": "$USER_KEY",
                                "models": [],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = json.loads(adapter.plan_endpoint_sync({"route": entry}, {}, home)[2])
            self.assertEqual("$USER_KEY", result["providers"]["route"]["apiKey"])
            self.assertNotEqual("dummy", result["providers"]["route"]["apiKey"])
            fresh = json.loads(adapter.plan_endpoint_sync({"fresh": entry}, {}, home)[2])
            self.assertNotIn("apiKey", fresh["providers"]["fresh"])

    def test_opencode_sync_plan_materializes_limits_for_every_model(self) -> None:
        adapter = get_adapter("opencode")
        endpoint = {"baseUrl": "http://localhost:9292/v1", "apiKeyEnv": "MY_ENDPOINT_KEY"}
        entries = {
            "default": {"model": "local/default", "endpoint": endpoint},
            "limited": {
                "model": "local/limited",
                "endpoint": endpoint,
                "limits": {"context": 65536, "output": 2048},
            },
        }

        after = adapter.plan_endpoint_sync(entries, {}, Path("/tmp"))[2]
        providers = json.loads(after)["provider"]
        self.assertEqual(
            {"context": 32768, "output": 4096},
            providers["default"]["models"]["local/default"]["limit"],
        )
        self.assertEqual(
            {"context": 65536, "output": 2048},
            providers["limited"]["models"]["local/limited"]["limit"],
        )
        for provider in providers.values():
            for model in provider["models"].values():
                self.assertIn("limit", model)

    def test_codex_model_and_environment(self) -> None:
        adapter = get_adapter("codex")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".codex").mkdir()
            (home / ".codex/config.toml").write_text('model = "configured"\n', encoding="utf-8")
            request = adapter.parse(["prompt.md", "--model=gpt-test"], {}, home)
            prepared = adapter.prepare(
                request,
                "/bin/codex",
                b"prompt\n",
                {"OTEL_RESOURCE_ATTRIBUTES": "service.name=test", **UNRESTRICTED_ENV},
                {},
            )
        self.assertEqual("gpt-test", request.model)
        self.assertEqual(b"prompt\n", prepared.stdin)
        self.assertEqual(
            "service.name=test,gen_ai.request.model=gpt-test",
            prepared.env["OTEL_RESOURCE_ATTRIBUTES"],
        )
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", prepared.argv)

    def test_grok_prompt_delivered_as_private_file(self) -> None:
        adapter = get_adapter("grok")
        self.assertEqual("file", adapter.prompt_delivery)
        request = adapter.parse(["prompt.md", "-m=grok-test"], {}, Path("/tmp"))
        self.assertEqual("grok-test", request.model)
        prepared = adapter.prepare(
            request,
            "/bin/grok",
            b"grok secret prompt\n",
            {},
            {"promptFile": "/run/prompt.deliver.md"},
        )
        self.assertEqual(
            "/run/prompt.deliver.md", prepared.argv[prepared.argv.index("--prompt-file") + 1]
        )
        self.assertNotIn("-p", prepared.argv)
        self.assertFalse(any("grok secret" in argument for argument in prepared.argv))
        self.assertIsNone(prepared.stdin)

    def test_claude_delivers_prompt_on_stdin(self) -> None:
        adapter = get_adapter("claude")
        request = adapter.parse(["prompt.md", "--model", "opus"], {}, Path("/tmp"))
        prepared = adapter.prepare(request, "/bin/claude", b"prompt\n\n", {}, {})
        self.assertEqual("opus", request.model)
        self.assertEqual(b"prompt\n\n", prepared.stdin)
        self.assertNotIn("--", prepared.argv)
        self.assertFalse(any("prompt" in argument for argument in prepared.argv[1:]))

    def test_kimi_uses_configured_model_and_noninteractive_prompt_mode(self) -> None:
        adapter = get_adapter("kimi")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".kimi-code"
            config.mkdir()
            (config / "config.toml").write_text(
                'default_model = "kimi-code/k3"\n', encoding="utf-8"
            )
            request = adapter.parse(["prompt.md"], UNRESTRICTED_ENV, home)
            prepared = adapter.prepare(
                request,
                "/bin/kimi",
                b"prompt\n\n",
                {"KIMI_DISABLE_TELEMETRY": "1", **UNRESTRICTED_ENV},
                {},
            )
        self.assertEqual("kimi-code/k3", request.model)
        self.assertIsNone(prepared.stdin)
        self.assertEqual("prompt", prepared.argv[prepared.argv.index("--prompt") + 1])
        self.assertEqual("text", prepared.argv[prepared.argv.index("--output-format") + 1])
        self.assertNotIn("--yolo", prepared.argv)
        self.assertNotIn("--auto", prepared.argv)
        self.assertEqual("1", prepared.env["KIMI_CODE_NO_AUTO_UPDATE"])
        self.assertEqual("1", prepared.env["KIMI_DISABLE_TELEMETRY"])

    def test_kimi_environment_model_precedes_config_and_cli_override_precedes_both(self) -> None:
        adapter = get_adapter("kimi")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".kimi-code"
            config.mkdir()
            (config / "config.toml").write_text(
                'default_model = "configured-model"\n', encoding="utf-8"
            )
            environment = {"KIMI_MODEL_NAME": "environment-model", **UNRESTRICTED_ENV}
            from_environment = adapter.parse(["prompt.md"], environment, home)
            from_cli = adapter.parse(["prompt.md", "--model", "explicit-model"], environment, home)
        self.assertEqual("environment-model", from_environment.model)
        self.assertEqual("explicit-model", from_cli.model)

    def test_kimi_rejects_prompt_mode_conflicts_and_shim_owned_flags(self) -> None:
        adapter = get_adapter("kimi")
        for flag in (
            "-y",
            "--yolo",
            "--auto",
            "-p",
            "--prompt=other",
            "--output-format",
            "--output-format=stream-json",
        ):
            with self.subTest(flag=flag), self.assertRaises(UsageError):
                adapter.parse(["prompt.md", flag], {}, Path("/tmp"))

    def test_qwen_uses_settings_model_and_noninteractive_prompt_mode(self) -> None:
        adapter = get_adapter("qwen")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".qwen"
            config.mkdir()
            (config / "settings.json").write_text(
                '{"model": {"name": "settings-model"}}', encoding="utf-8"
            )
            request = adapter.parse(["prompt.md"], {}, home)
            prepared = adapter.prepare(
                request,
                "/bin/qwen",
                b"prompt\n\n",
                UNRESTRICTED_ENV,
                {},
            )
        self.assertEqual("settings-model", request.model)
        self.assertIsNone(prepared.stdin)
        self.assertEqual("prompt", prepared.argv[prepared.argv.index("--prompt") + 1])
        self.assertEqual("text", prepared.argv[prepared.argv.index("--output-format") + 1])
        self.assertIn("--yolo", prepared.argv)

    def test_qwen_settings_string_model_form_is_accepted(self) -> None:
        adapter = get_adapter("qwen")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".qwen"
            config.mkdir()
            (config / "settings.json").write_text('{"model": "plain-model"}', encoding="utf-8")
            request = adapter.parse(["prompt.md"], {}, home)
        self.assertEqual("plain-model", request.model)

    def test_qwen_dotenv_model_attribution_between_settings_and_fallback(self) -> None:
        adapter = get_adapter("qwen")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".qwen"
            config.mkdir()
            (config / ".env").write_text(
                "OPENAI_API_KEY=k\nOPENAI_BASE_URL=http://localhost/v1\nQWEN_MODEL=dotenv-model\n",
                encoding="utf-8",
            )
            from_dotenv = adapter.parse(["prompt.md"], {}, home)
            (config / "settings.json").write_text(
                '{"model": {"name": "settings-model"}}', encoding="utf-8"
            )
            settings_wins = adapter.parse(["prompt.md"], {}, home)
        self.assertEqual("dotenv-model", from_dotenv.model)
        self.assertEqual("settings-model", settings_wins.model)

    def test_qwen_environment_model_precedes_settings_and_cli_override_precedes_both(self) -> None:
        adapter = get_adapter("qwen")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".qwen"
            config.mkdir()
            (config / "settings.json").write_text(
                '{"model": {"name": "settings-model"}}', encoding="utf-8"
            )
            environment = {"QWEN_MODEL": "environment-model"}
            from_environment = adapter.parse(["prompt.md"], environment, home)
            from_cli = adapter.parse(["prompt.md", "--model", "explicit-model"], environment, home)
            bare = adapter.parse(["prompt.md"], {}, Path(directory) / "empty-home")
        self.assertEqual("environment-model", from_environment.model)
        self.assertEqual("explicit-model", from_cli.model)
        self.assertEqual("qwen-default", bare.model)

    def test_qwen_falls_back_to_the_standalone_installer_shim_when_not_on_path(self) -> None:
        # The Qwen standalone installer documents its wrapper at ~/.local/bin/qwen, which is
        # also npm's global bin under a user prefix; mirror the opencode home-relative fallback.
        adapter = get_adapter("qwen")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = {"PATH": "/nonexistent"}
            self.assertIsNone(adapter.resolve_binary(env, home))
            fallback = home / ".local" / "bin" / "qwen"
            fallback.parent.mkdir(parents=True)
            fallback.write_text("#!/bin/sh\n", encoding="utf-8")
            self.assertIsNone(
                adapter.resolve_binary(env, home), "non-executable file must not resolve"
            )
            fallback.chmod(0o755)
            self.assertEqual(str(fallback), adapter.resolve_binary(env, home))
            self.assertEqual(
                "/custom/qwen", adapter.resolve_binary({**env, "QWEN_BIN": "/custom/qwen"}, home)
            )

    def test_qwen_rejects_prompt_mode_conflicts_and_shim_owned_flags(self) -> None:
        adapter = get_adapter("qwen")
        for flag in (
            "-y",
            "--yolo",
            "--approval-mode",
            "--approval-mode=yolo",
            "-p",
            "--prompt=other",
            "--output-format",
            "--output-format=stream-json",
        ):
            with self.subTest(flag=flag), self.assertRaises(UsageError):
                adapter.parse(["prompt.md", flag], {}, Path("/tmp"))

    def test_agy_default_argv_records_effective_effort_and_workspace(self) -> None:
        adapter = get_adapter("agy")
        request = adapter.parse(["prompt.md"], {}, Path("/tmp"))
        prepared = adapter.prepare(
            request,
            "/bin/agy",
            b"prompt\n\n",
            UNRESTRICTED_ENV,
            {"workspacePath": "/tmp/worktree"},
        )
        self.assertEqual("gemini-3.8-flash", request.model)
        self.assertEqual("medium", request.adapter_data["effort"])
        self.assertEqual(
            [
                "/bin/agy",
                "--model",
                "gemini-3.8-flash",
                "--effort",
                "medium",
                "--add-dir",
                "/tmp/worktree",
                "--dangerously-skip-permissions",
                "--print-timeout",
                "1200s",
                "--output-format",
                "text",
                "-p",
                "prompt",
            ],
            prepared.argv,
        )
        self.assertEqual(UNRESTRICTED_ENV, prepared.env)
        self.assertIsNone(prepared.stdin)
        self.assertEqual("<prompt>", prepared.sanitized_args[-1])

    def test_agy_preserves_explicit_model_effort_and_extra_args(self) -> None:
        adapter = get_adapter("agy")
        request = adapter.parse(
            ["prompt.md", "--model=gemini-3.1-pro", "--effort", "high", "--sandbox"],
            {},
            Path("/tmp"),
        )
        prepared = adapter.prepare(
            request,
            "agy",
            b"prompt",
            {"PITWALL_AGENTS_TIMEOUT_SECS": "60"},
            {"workspacePath": "/workspace"},
        )
        self.assertEqual("gemini-3.1-pro", request.model)
        self.assertEqual("high", request.adapter_data["effort"])
        self.assertEqual(1, prepared.argv.count("--model=gemini-3.1-pro"))
        self.assertNotIn("medium", prepared.argv)
        self.assertLess(prepared.argv.index("--sandbox"), prepared.argv.index("--print-timeout"))
        self.assertEqual("120s", prepared.argv[prepared.argv.index("--print-timeout") + 1])
        self.assertEqual(["-p", "prompt"], prepared.argv[-2:])

    def test_agy_effort_suffixed_model_does_not_inject_effort(self) -> None:
        adapter = get_adapter("agy")
        request = adapter.parse(["prompt.md", "--model", "gemini-3.7-flash-low"], {}, Path("/tmp"))
        prepared = adapter.prepare(request, "agy", b"prompt", {}, {})
        self.assertEqual("low", request.adapter_data["effort"])
        self.assertNotIn("--effort", prepared.argv)

    def test_agy_rejects_effort_with_suffixed_model(self) -> None:
        adapter = get_adapter("agy")
        with self.assertRaisesRegex(
            UsageError,
            "--model <slug> already carries an effort suffix; drop --effort",
        ):
            adapter.parse(
                ["prompt.md", "--model=gemini-3.7-flash-high", "--effort=low"],
                {},
                Path("/tmp"),
            )

    def test_agy_rejects_all_shim_managed_flags(self) -> None:
        adapter = get_adapter("agy")
        for flag in (
            "-p",
            "--print",
            "--prompt=other",
            "-i",
            "--prompt-interactive",
            "--output-format",
            "--input-format=json",
            "--json-schema",
            "--print-timeout=2m",
            "--dangerously-skip-permissions",
            "-c",
            "--continue",
            "--conversation=abc",
        ):
            with self.subTest(flag=flag), self.assertRaisesRegex(UsageError, "managed by the shim"):
                adapter.parse(["prompt.md", flag], {}, Path("/tmp"))

    def test_agy_soft_denial_detection_keys_on_exit_zero_and_the_jetski_marker(self) -> None:
        adapter = get_adapter("agy")
        marker = (
            'jetski: no output produced — a tool required the "command" permission '
            "that headless mode cannot prompt for, so it was auto-denied."
        ).encode()
        reason = adapter.detect_soft_denial(0, b"noise before\n" + marker + b"\n")
        self.assertIsNotNone(reason)
        self.assertIn("permissions.allow", reason or "")
        self.assertIn("77", reason or "")
        self.assertIsNone(adapter.detect_soft_denial(0, b"ordinary diagnostics\n"))
        self.assertIsNone(adapter.detect_soft_denial(1, marker))
        self.assertIsNone(get_adapter("codex").detect_soft_denial(0, marker))

    def test_hermes_soft_denial_detection_uses_the_last_non_empty_line(self) -> None:
        adapter = get_adapter("hermes")
        failure = b"diagnostic\nHTTP 401: Missing Authentication header\n\n"
        reason = adapter.detect_soft_denial(0, failure)
        self.assertIsNotNone(reason)
        self.assertIn("did not attach credentials", reason or "")
        self.assertIn("profiles sync --harness hermes", reason or "")
        self.assertIn("loopback address", reason or "")
        self.assertIsNone(adapter.detect_soft_denial(0, failure + b"normal answer\n"))
        self.assertIsNone(adapter.detect_soft_denial(1, failure))

    def test_agy_resolves_override_path_and_executable_home_fallback(self) -> None:
        adapter = get_adapter("agy")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = {"PATH": "/nonexistent"}
            self.assertIsNone(adapter.resolve_binary(env, home))
            fallback = home / ".local" / "bin" / "agy"
            fallback.parent.mkdir(parents=True)
            fallback.write_text("#!/bin/sh\n", encoding="utf-8")
            self.assertIsNone(adapter.resolve_binary(env, home))
            fallback.chmod(0o755)
            self.assertEqual(str(fallback), adapter.resolve_binary(env, home))
            self.assertEqual(
                "/custom/agy", adapter.resolve_binary({**env, "AGY_BIN": "/custom/agy"}, home)
            )

    def test_agy_restricted_mode_keeps_antigravity_permissions(self) -> None:
        adapter = get_adapter("agy")
        env = {"PITWALL_AGENTS_UNRESTRICTED": "0"}
        request = adapter.parse(["prompt.md"], env, Path("/tmp"))
        prepared = adapter.prepare(request, "agy", b"prompt", env, {})
        self.assertNotIn("--dangerously-skip-permissions", prepared.argv)

    def test_restricted_provider_flags(self) -> None:
        env = {"PITWALL_AGENTS_UNRESTRICTED": "0"}
        codex = get_adapter("codex")
        request = codex.parse(["prompt.md"], env, Path("/tmp"))
        prepared = codex.prepare(request, "codex", b"prompt", env, {})
        self.assertEqual("workspace-write", prepared.argv[prepared.argv.index("--sandbox") + 1])
        for provider in ("agy", "claude", "grok", "opencode", "pi", "qwen"):
            adapter = get_adapter(provider)
            argv = ["provider/model", "prompt.md"] if provider == "opencode" else ["prompt.md"]
            request = adapter.parse(argv, env, Path("/tmp"))
            prepared = adapter.prepare(
                request, provider, b"prompt", env, {"promptFile": "/run/prompt.deliver.md"}
            )
            self.assertNotIn("--dangerously-skip-permissions", prepared.argv)
            self.assertNotIn("--always-approve", prepared.argv)
            self.assertNotIn("--yolo", prepared.argv)

    def test_binary_overrides_are_additive(self) -> None:
        expected = {
            "codex": ("CODEX_BIN", "/custom/codex"),
            "claude": ("CLAUDE_BIN", "/custom/claude"),
            "grok": ("GROK_BIN", "/custom/grok"),
            "kimi": ("KIMI_BIN", "/custom/kimi"),
            "opencode": ("OPENCODE_BIN", "/custom/opencode"),
            "pi": ("PI_BIN", "/custom/pi"),
            "muse": ("MUSE_BIN", "/custom/muse"),
            "qwen": ("QWEN_BIN", "/custom/qwen"),
            "agy": ("AGY_BIN", "/custom/agy"),
        }
        for provider, (variable, path) in expected.items():
            with self.subTest(provider=provider):
                self.assertEqual(
                    path, get_adapter(provider).resolve_binary({variable: path}, Path("/tmp"))
                )

    def test_sanitized_arguments_redact_secret_values(self) -> None:
        adapter = get_adapter("codex")
        self.assertEqual(
            ["--api-key", "<redacted>", "--token=<redacted>", "--model=gpt-test"],
            adapter.sanitize_args(
                ["--api-key", "secret-one", "--token=secret-two", "--model=gpt-test"]
            ),
        )


ALL_ADAPTERS = (
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
)
PROMPT_FILE_DATA = {"promptFile": "/run/prompt.deliver.md"}


def _provider_argv(provider: str, *extra: str) -> list[str]:
    return (
        ["provider/model", "prompt.md", *extra] if provider == "opencode" else ["prompt.md", *extra]
    )


def _prepare(
    provider: str, env: dict[str, str], extra: tuple[str, ...] = (), *, prompt: bytes = b"prompt\n"
):
    adapter = get_adapter(provider)
    request = adapter.parse(_provider_argv(provider, *extra), env, Path("/tmp"))
    preflight = dict(PROMPT_FILE_DATA)
    if provider == "opencode" and adapter.unrestricted(env):
        preflight["permissionFlag"] = "--dangerously-skip-permissions"
    return adapter.prepare(request, f"/bin/{provider}", prompt, env, preflight)


# provider -> (argv token that only an unrestricted run carries, or an env assignment)
UNRESTRICTED_MARKERS = {
    "agy": ("argv", "--dangerously-skip-permissions"),
    "claude": ("argv", "--dangerously-skip-permissions"),
    "cline": ("argv-pair", ("--auto-approve", "true")),
    "codex": ("argv", "--dangerously-bypass-approvals-and-sandbox"),
    "goose": ("env", ("GOOSE_MODE", "auto")),
    "grok": ("argv", "--always-approve"),
    "hermes": ("argv", "--yolo"),
    "muse": ("argv", "--yolo"),
    "opencode": ("argv", "--dangerously-skip-permissions"),
    "pi": ("argv", "--approve"),
    "qwen": ("argv", "--yolo"),
}
# CLIs whose non-interactive mode has no documented approval-bypass flag.
NO_BYPASS_FLAG = ("kimi", "dsh")


def _has_marker(marker: tuple[str, object], prepared) -> bool:
    kind, value = marker
    if kind == "argv":
        return value in prepared.argv
    if kind == "env":
        key, expected = value  # type: ignore[misc]  # reason: the test navigates a loosely typed JSON document
        return prepared.env.get(key) == expected
    flag, expected = value  # type: ignore[misc]  # reason: the test navigates a loosely typed JSON document
    return flag in prepared.argv and prepared.argv[prepared.argv.index(flag) + 1] == expected


class UnrestrictedSettingTests(unittest.TestCase):
    def test_unrestricted_honoured_by_every_adapter(self) -> None:
        self.assertEqual(set(ALL_ADAPTERS), set(UNRESTRICTED_MARKERS) | set(NO_BYPASS_FLAG))
        for provider in ALL_ADAPTERS:
            with self.subTest(provider=provider):
                if provider in NO_BYPASS_FLAG:
                    _prepare(provider, dict(UNRESTRICTED_ENV))
                    with self.assertRaisesRegex(UsageError, "PITWALL_AGENTS_UNRESTRICTED=1"):
                        _prepare(provider, dict(RESTRICTED_ENV))
                    continue
                marker = UNRESTRICTED_MARKERS[provider]
                unrestricted = _prepare(provider, dict(UNRESTRICTED_ENV))
                restricted = _prepare(provider, dict(RESTRICTED_ENV))
                self.assertTrue(
                    _has_marker(marker, unrestricted), f"{provider} unrestricted lacks {marker}"
                )
                self.assertFalse(
                    _has_marker(marker, restricted), f"{provider} restricted still carries {marker}"
                )


class UnsetUnrestrictedDefaultTests(unittest.TestCase):
    """An unset PITWALL_AGENTS_UNRESTRICTED keeps each CLI's own sandbox and approvals."""

    def test_codex_unset_runs_workspace_write_sandbox(self) -> None:
        prepared = _prepare("codex", {})
        self.assertIn("--sandbox", prepared.argv)
        self.assertIn("workspace-write", prepared.argv)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", prepared.argv)
        self.assertEqual("cli-policy", get_adapter("codex").policy_profile({}))

    def test_claude_unset_keeps_permission_prompts(self) -> None:
        self.assertNotIn("--dangerously-skip-permissions", _prepare("claude", {}).argv)

    def test_every_bypassing_adapter_is_restricted_when_unset(self) -> None:
        for provider, marker in UNRESTRICTED_MARKERS.items():
            with self.subTest(provider=provider):
                self.assertFalse(_has_marker(marker, _prepare(provider, {})))
                self.assertEqual("cli-policy", get_adapter(provider).policy_profile({}))

    def test_kimi_and_dsh_unset_refuse_and_name_the_opt_in(self) -> None:
        for provider in NO_BYPASS_FLAG:
            with (
                self.subTest(provider=provider),
                self.assertRaisesRegex(UsageError, "PITWALL_AGENTS_UNRESTRICTED=1"),
            ):
                _prepare(provider, {})

    def test_explicit_one_still_bypasses(self) -> None:
        prepared = _prepare("codex", dict(UNRESTRICTED_ENV))
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", prepared.argv)
        self.assertEqual("unrestricted", get_adapter("codex").policy_profile(UNRESTRICTED_ENV))


class ModelFlagTests(unittest.TestCase):
    FORMS = (
        ["-m", "picked-model"],
        ["--model", "picked-model"],
        ["--model=picked-model"],
        ["-m=picked-model"],
    )

    def test_model_flag_forms(self) -> None:
        for provider in ALL_ADAPTERS:
            for form in self.FORMS:
                with self.subTest(provider=provider, form=form):
                    adapter = get_adapter(provider)
                    request = adapter.parse(
                        _provider_argv(provider, *form), UNRESTRICTED_ENV, Path("/tmp")
                    )
                    self.assertEqual("picked-model", request.model)
                    self.assertTrue(request.has_model_override)

    def test_shared_parser_last_flag_wins_and_reports_no_override_by_default(self) -> None:
        parse = get_adapter("codex").parse_model_flag
        self.assertEqual(("fallback", False), parse(["--verbose", "value"], "fallback"))
        self.assertEqual(("second", True), parse(["-m", "first", "--model=second"], "fallback"))

    def test_muse_gains_the_short_model_flag(self) -> None:
        request = get_adapter("muse").parse(
            ["prompt.md", "-m", "muse-spark-test"], {}, Path("/tmp")
        )
        self.assertEqual(("muse-spark-test", True), (request.model, request.has_model_override))


class PromptDeliveryTests(unittest.TestCase):
    def test_pi_prompt_delivered_as_private_file(self) -> None:
        import os
        import stat

        adapter = get_adapter("pi")
        self.assertEqual("file", adapter.prompt_delivery)
        secret_prompt = b"top secret prompt text\n"
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            run_dir.mkdir(mode=0o700)
            request = adapter.parse(["prompt.md", "--thinking", "high"], {}, Path("/tmp"))
            store_file = run_dir / "prompt.deliver.md"
            store_file.write_bytes(secret_prompt)
            store_file.chmod(0o600)
            prepared = adapter.prepare(
                request, "/bin/pi", secret_prompt, {}, {"promptFile": str(store_file)}
            )
            self.assertIn(f"@{store_file}", prepared.argv)
            self.assertEqual(f"@{store_file}", prepared.argv[-1])
            self.assertEqual(0o600, stat.S_IMODE(os.stat(store_file).st_mode))
            self.assertEqual(secret_prompt, store_file.read_bytes())
            self.assertFalse(any("top secret" in argument for argument in prepared.argv))
            self.assertFalse(any("top secret" in argument for argument in prepared.sanitized_args))
            self.assertIsNone(prepared.stdin)

    def test_argv_prompt_over_limit_refused(self) -> None:
        from pitwall.agents.harnesses.base import ARGV_PROMPT_LIMIT_BYTES

        self.assertEqual(120 * 1024, ARGV_PROMPT_LIMIT_BYTES)
        argv_providers = [
            name for name in ALL_ADAPTERS if get_adapter(name).prompt_delivery == "argv"
        ]
        self.assertTrue({"kimi", "dsh"} <= set(argv_providers))
        for provider in argv_providers:
            with self.subTest(provider=provider):
                adapter = get_adapter(provider)
                request = adapter.parse(["prompt.md"], UNRESTRICTED_ENV, Path("/tmp"))
                at_limit = b"x" * ARGV_PROMPT_LIMIT_BYTES
                self.assertEqual(
                    at_limit.decode(),
                    adapter.prepare(
                        request, f"/bin/{provider}", at_limit, UNRESTRICTED_ENV, {}
                    ).argv[-1],
                )
                with self.assertRaisesRegex(UsageError, str(ARGV_PROMPT_LIMIT_BYTES)):
                    adapter.prepare(
                        request, f"/bin/{provider}", at_limit + b"x", UNRESTRICTED_ENV, {}
                    )
                with self.assertRaisesRegex(UsageError, str(ARGV_PROMPT_LIMIT_BYTES)):
                    adapter.check_prompt_size(at_limit + b"x")


class OldMarkerIsNotRecognisedTests(unittest.TestCase):
    """Sync recognises only the `managed by pitwall` marker; migrate rewrites the old one."""

    OLD = "managed by " + "subagent-model-routing"

    def test_dsh_and_hermes_use_only_the_new_marker(self) -> None:
        from pitwall.agents.harnesses import dsh, hermes

        self.assertEqual("    # managed by pitwall\n", dsh._MANAGED)
        self.assertEqual("  # managed by pitwall\n", hermes._MANAGED)
        for module in (dsh, hermes):
            self.assertNotIn(self.OLD, module._MANAGED)

    def test_opencode_prefix_is_the_new_identity(self) -> None:
        from pitwall.agents.harnesses import opencode

        (harness_class,) = [
            value
            for value in vars(opencode).values()
            if isinstance(value, type) and hasattr(value, "MANAGED_PREFIX")
        ]
        self.assertEqual("pitwall: ", harness_class.MANAGED_PREFIX)


if __name__ == "__main__":
    unittest.main()
