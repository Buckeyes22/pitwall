"""Tests for [agents.profiles] persistence, validation, and resolution."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from pitwall.agents import profiles, profiles_resolve
from pitwall.agents.broker import PITWALL_TOKEN_ENV
from pitwall.agents.errors import (
    ProfileConfigError,
    UsageError,
)
from pitwall.agents.harnesses import get_adapter
from pitwall.agents.registry import (
    load_registry,
)
from tests.agents.profiles_fixture import read_profiles, write_profiles
from tests.agents.shim_test_support import PITWALL
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
BROKER_TOKEN_ENV = "PITWALL_API_TOKEN"  # the broker's own variable; agents never read it
ROUTE_FIXTURES = ROOT / "tests/agents" / "fixtures" / "routes"


def example() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
        "models": {
            "glimmer": {
                "model": "meta-models/Muse-Glimmer-30B",
                "endpoint": {"baseUrl": "http://gpu-1:8000/v1", "apiKeyEnv": "GLIMMER_API_KEY"},
                "seat": "local",
            },
            "glm": {
                "model": "zai-coding-plan/glm-5.3",
                "harness": "opencode",
                "seat": "default-author",
            },
            "sol": {
                "model": "gpt-5.6-sol",
                "args": ["-c", "model_reasoning_effort=high"],
                "seat": "critical",
            },
        },
    }


class RoutesConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def env(self, directory: str) -> dict[str, str]:
        return {"HOME": directory, "XDG_CONFIG_HOME": str(Path(directory) / "config")}

    def test_missing_file_yields_empty_defaults_and_override_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = self.env(directory)
            self.assertEqual(
                profiles.empty_profiles(), profiles.load_profiles(env, registry=self.registry)
            )
            self.assertEqual(
                Path(directory) / "config" / "pitwall" / "pitwall.toml",
                profiles.profiles_path(env),
            )
            env["PITWALL_AGENTS_PROFILES"] = str(Path(directory) / "elsewhere.toml")
            self.assertEqual(Path(directory) / "elsewhere.toml", profiles.profiles_path(env))

    def test_example_validates_and_normalizes_sorted(self) -> None:
        normalized = profiles.validate_profiles(example(), registry=self.registry)
        self.assertEqual(["glimmer", "glm", "sol"], list(normalized["models"]))
        self.assertEqual([], normalized["models"]["glm"]["args"])
        self.assertEqual({}, normalized["models"]["glm"]["env"])

    def test_shared_schema_fixtures_match_runtime_structural_validation(self) -> None:
        positives = sorted((ROUTE_FIXTURES / "positive").glob("*.json"))
        negatives = sorted((ROUTE_FIXTURES / "negative").glob("*.json"))
        self.assertGreaterEqual(len(positives), 2)
        self.assertGreaterEqual(len(negatives), 3)
        for path in positives:
            with self.subTest(fixture=path.name):
                profiles.validate_profiles(
                    json.loads(path.read_text(encoding="utf-8")),
                    registry=self.registry,
                )
        for path in negatives:
            with self.subTest(fixture=path.name), self.assertRaises(profiles.ProfilesError):
                profiles.validate_profiles(
                    json.loads(path.read_text(encoding="utf-8")),
                    registry=self.registry,
                )

    def test_harness_defaults_and_route_effort_validate_and_normalize(self) -> None:
        data = example()
        data["harnesses"] = {
            "codex": {"effort": "high"},
            "agy": {"effort": "low", "args": ["--sandbox"]},
        }
        data["models"]["fast"] = {"model": "gemini-3.7-flash", "effort": "medium"}  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        normalized = profiles.validate_profiles(data, registry=self.registry)
        self.assertEqual(
            {
                "codex": {"effort": "high", "args": []},
                "agy": {"effort": "low", "args": ["--sandbox"]},
            },
            normalized["harnesses"],
        )
        self.assertEqual("medium", normalized["models"]["fast"]["effort"])
        self.assertEqual(
            {}, profiles.validate_profiles(example(), registry=self.registry)["harnesses"]
        )
        self.assertIn("harnesses", profiles.empty_profiles())

    def test_effort_is_checked_against_harness_and_model(self) -> None:
        cases = {
            "unknown harness": ({"nope": {"effort": "high"}}, {}, "harnesses.nope"),
            "bad harness value": (
                {"codex": {"effort": "turbo"}},
                {},
                "minimal, low, medium, high, xhigh",
            ),
            "no effort control": ({"kimi": {"effort": "high"}}, {}, "no effort control"),
            "bad args": ({"codex": {"args": "x"}}, {}, "args"),
            "unknown field": ({"codex": {"seat": "x"}}, {}, "unknown fields"),
            "route value not for model": (
                {},
                {"pro": {"model": "gemini-3.1-pro", "effort": "medium"}},
                "low, high",
            ),
            "route value not for harness": (
                {},
                {"sol": {"model": "gpt-5.6-sol", "effort": "ultra"}},
                "none, minimal, low, medium, high, xhigh, max",
            ),
            "route effort on none harness": (
                {},
                {"k": {"model": "kimi-code/k3", "effort": "high"}},
                "no effort control",
            ),
        }
        for label, (harnesses, models, needle) in cases.items():
            with self.subTest(case=label):
                data = example()
                data["harnesses"] = harnesses
                data["models"].update(models)  # type: ignore[union-attr]  # reason: the test navigates a loosely typed JSON document
                with self.assertRaisesRegex(profiles.ProfilesError, needle):
                    profiles.validate_profiles(data, registry=self.registry)
        # opencode's --variant is harness-defined: any non-empty string is accepted
        data = example()
        data["harnesses"] = {"opencode": {"effort": "max"}}
        profiles.validate_profiles(data, registry=self.registry)

    def test_effort_helpers_follow_the_registry(self) -> None:
        self.assertEqual(
            (
                "config",
                "model_reasoning_effort",
                ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
            ),
            profiles.effort_spec(self.registry, "codex"),
        )
        self.assertEqual(
            ["-c", "model_reasoning_effort=high"],
            profiles.render_effort(self.registry, "codex", "high"),
        )
        self.assertEqual(["--effort", "low"], profiles.render_effort(self.registry, "agy", "low"))
        self.assertTrue(
            profiles.caller_sets_effort(
                self.registry, "codex", ["-c", "model_reasoning_effort=low"]
            )
        )
        self.assertTrue(profiles.caller_sets_effort(self.registry, "agy", ["--effort=low"]))
        self.assertFalse(profiles.caller_sets_effort(self.registry, "agy", ["--sandbox"]))
        defaults = profiles.empty_profiles()["defaults"]
        self.assertEqual(
            "codex", profiles.entry_harness({"model": "gpt-5.6-sol"}, defaults, self.registry)
        )
        self.assertEqual(
            "qwen",
            profiles.entry_harness(
                {"model": "x/y", "endpoint": {"baseUrl": "http://h/v1"}}, defaults, self.registry
            ),
        )
        self.assertEqual(
            "opencode", profiles.entry_harness({"model": "x/y"}, defaults, self.registry)
        )
        self.assertEqual(
            "pi", profiles.entry_harness({"model": "x/y", "harness": "pi"}, defaults, self.registry)
        )
        data = profiles.add_profile(
            profiles.empty_profiles(), "fast", model="gemini-3.7-flash", effort="medium"
        )
        self.assertEqual("medium", data["models"]["fast"]["effort"])

    def test_names_must_match_pattern_and_never_contain_at(self) -> None:
        for name in ("@glimmer", "glimmer@qwen", "1st", "a" * 65, ""):
            with self.subTest(name=name):
                data = example()
                data["models"][name] = {"model": "x/y"}  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
                with self.assertRaisesRegex(profiles.ProfilesError, "route name"):
                    profiles.validate_profiles(data, registry=self.registry)

    def test_inline_secrets_are_rejected_but_api_key_env_is_allowed(self) -> None:
        for key in ("apiKey", "api_key", "token", "secret", "password"):
            with self.subTest(key=key):
                data = example()
                data["models"]["glimmer"]["endpoint"][key] = "sk-live"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
                with self.assertRaisesRegex(profiles.ProfilesError, "secret"):
                    profiles.validate_profiles(data, registry=self.registry)
        profiles.validate_profiles(example(), registry=self.registry)

    def test_shared_endpoint_reference_resolves_for_dispatch_and_materializers(self) -> None:
        data = example()
        data["endpoints"] = {
            "local": {
                "baseUrl": "http://localhost:9292/v1",
                "apiKeyEnv": "MY_ENDPOINT_KEY",
            }
        }
        models = data["models"]
        self.assertIsInstance(models, dict)
        glimmer = models["glimmer"]
        self.assertIsInstance(glimmer, dict)
        glimmer["endpoint"] = "local"
        normalized = profiles.validate_profiles(data, registry=self.registry)

        endpoint = normalized["models"]["glimmer"]["endpoint"]
        self.assertIsInstance(endpoint, str)
        self.assertEqual("local", endpoint)
        self.assertEqual("http://localhost:9292/v1", endpoint["baseUrl"])
        self.assertEqual("MY_ENDPOINT_KEY", endpoint.get("apiKeyEnv"))

        resolved = profiles_resolve.resolve_profile(
            "glimmer",
            registry=self.registry,
            routes=normalized,
            env={"MY_ENDPOINT_KEY": "secret"},
            home=Path("/nonexistent-home"),
            prompt_source="<prompt>",
            caller_args=(),
        )
        self.assertEqual("localhost:9292", resolved.endpoint_host)

        adapter = get_adapter("opencode")
        with tempfile.TemporaryDirectory() as directory:
            _path, _before, rendered = adapter.plan_endpoint_sync(
                {"glimmer": normalized["models"]["glimmer"]},
                {},
                Path(directory),
            )
        self.assertIn("http://localhost:9292/v1", rendered)
        self.assertIn("MY_ENDPOINT_KEY", rendered)

    def test_shared_endpoint_is_rejected_on_pitwall_origin_routes(self) -> None:
        data = example()
        data["endpoints"] = {
            "local": {"baseUrl": "http://localhost:9292/v1", "apiKeyEnv": "MY_ENDPOINT_KEY"}
        }
        models = data["models"]
        self.assertIsInstance(models, dict)
        glimmer = models["glimmer"]
        self.assertIsInstance(glimmer, dict)
        glimmer["endpoint"] = "local"
        glimmer["origin"] = {
            "kind": "pitwall",
            "capability": "llm.glimmer",
            "leaseId": None,
            "url": "http://127.0.0.1:8080",
        }
        with self.assertRaisesRegex(profiles.ProfilesError, "inline endpoint"):
            profiles.validate_profiles(data, registry=self.registry)

    def test_shared_endpoint_rejects_unknown_names_and_invalid_definitions(self) -> None:
        data = example()
        models = data["models"]
        self.assertIsInstance(models, dict)
        glimmer = models["glimmer"]
        self.assertIsInstance(glimmer, dict)
        glimmer["endpoint"] = "missing"
        with self.assertRaisesRegex(profiles.ProfilesError, "unknown shared endpoint 'missing'"):
            profiles.validate_profiles(data, registry=self.registry)

        invalid_cases = {
            "bad name": ({"1local": {"baseUrl": "http://localhost:9292/v1"}}, "endpoint name"),
            "bad url": ({"local": {"baseUrl": "localhost:9292/v1"}}, "baseUrl"),
            "bad key env": (
                {"local": {"baseUrl": "http://localhost:9292/v1", "apiKeyEnv": "lower"}},
                "apiKeyEnv",
            ),
            "inline secret": (
                {"local": {"baseUrl": "http://localhost:9292/v1", "apiKey": "secret"}},
                "inline secret",
            ),
        }
        for label, (endpoints, needle) in invalid_cases.items():
            with self.subTest(case=label):
                broken = example()
                broken["endpoints"] = endpoints
                with self.assertRaisesRegex(profiles.ProfilesError, needle):
                    profiles.validate_profiles(broken, registry=self.registry)

    def test_env_token_counts_are_allowed_but_token_secrets_are_rejected(self) -> None:
        data = example()
        models = data["models"]
        self.assertIsInstance(models, dict)
        glimmer = models["glimmer"]
        self.assertIsInstance(glimmer, dict)
        glimmer["env"] = {"QWEN_CODE_MAX_OUTPUT_TOKENS": "1024"}
        normalized = profiles.validate_profiles(data, registry=self.registry)
        self.assertEqual(
            {"QWEN_CODE_MAX_OUTPUT_TOKENS": "1024"}, normalized["models"]["glimmer"]["env"]
        )

        glimmer["env"] = {"MY_API_TOKEN": "x"}
        with self.assertRaisesRegex(profiles.ProfilesError, "looks like an inline secret"):
            profiles.validate_profiles(data, registry=self.registry)

    def test_harness_seat_workspace_url_and_env_are_checked(self) -> None:
        cases = {
            "unknown harness": ({"model": "x/y", "harness": "not-a-harness"}, "harness"),
            "bad seat": ({"model": "x/y", "seat": "boss"}, "seat"),
            "bad workspace": ({"model": "x/y", "workspace": "cloud"}, "workspace"),
            "bad url": ({"model": "x/y", "endpoint": {"baseUrl": "gpu-1:8000"}}, "baseUrl"),
            "bad key env": (
                {"model": "x/y", "endpoint": {"baseUrl": "http://h/v1", "apiKeyEnv": "lower"}},
                "apiKeyEnv",
            ),
            "bad env key": ({"model": "x/y", "env": {"api-key": "v"}}, "env"),
            "missing model": ({"harness": "opencode"}, "model"),
        }
        for label, (entry, needle) in cases.items():
            with self.subTest(case=label):
                data = example()
                data["models"]["bad"] = entry  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
                with self.assertRaisesRegex(profiles.ProfilesError, needle):
                    profiles.validate_profiles(data, registry=self.registry)
        data = example()
        data["defaults"]["endpointHarness"] = "codex"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(profiles.ProfilesError, "endpointHarness"):
            profiles.validate_profiles(data, registry=self.registry)

    def test_save_writes_private_file_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = self.env(directory)
            path = profiles.save_profiles(env, example(), registry=self.registry)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)
            self.assertEqual(
                profiles.validate_profiles(example(), registry=self.registry),
                profiles.load_profiles(env, registry=self.registry),
            )
            path.write_text("[agents.profiles", encoding="utf-8")
            with self.assertRaisesRegex(profiles.ProfilesError, "cannot read agent profiles"):
                profiles.load_profiles(env, registry=self.registry)

    def test_add_replaces_and_remove_deletes(self) -> None:
        data = profiles.add_profile(
            profiles.empty_profiles(),
            "glimmer",
            model="meta-models/Muse-Glimmer-30B",
            base_url="http://gpu-1:8000/v1",
            api_key_env="GLIMMER_API_KEY",
            seat="local",
        )
        self.assertEqual(
            {"baseUrl": "http://gpu-1:8000/v1", "apiKeyEnv": "GLIMMER_API_KEY"},
            data["models"]["glimmer"]["endpoint"],
        )
        data = profiles.add_profile(
            data, "glimmer", model="meta-models/Muse-Glimmer-30B-GGUF", harness="qwen"
        )
        self.assertNotIn("endpoint", data["models"]["glimmer"])
        self.assertEqual("qwen", data["models"]["glimmer"]["harness"])
        data = profiles.remove_profile(data, "glimmer")
        self.assertEqual({}, data["models"])
        with self.assertRaisesRegex(profiles.ProfilesError, "no route named"):
            profiles.remove_profile(data, "glimmer")
        with self.assertRaisesRegex(profiles.ProfilesError, "api_key_env"):
            profiles.add_profile(data, "x", model="a/b", api_key_env="K")

    def test_expires_at_and_origin_are_validated_and_normalized(self) -> None:
        data = example()
        data["models"]["glimmer"]["expiresAt"] = "2026-08-27T10:00:00Z"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        data["models"]["glimmer"]["origin"] = {
            "kind": "pitwall",
            "capability": "llm.glimmer",
            "leaseId": "lease_abc",
            "url": "http://127.0.0.1:8080",
        }  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        normalized = profiles.validate_profiles(data, registry=self.registry)
        self.assertEqual("2026-08-27T10:00:00Z", normalized["models"]["glimmer"]["expiresAt"])
        self.assertEqual("llm.glimmer", normalized["models"]["glimmer"]["origin"]["capability"])
        for bad in ("2026-08-27 10:00", "2026-08-27T10:00:00+02:00", "soon"):
            with self.subTest(expires=bad):
                broken = example()
                broken["models"]["glimmer"]["expiresAt"] = bad  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
                with self.assertRaisesRegex(profiles.ProfilesError, "expiresAt"):
                    profiles.validate_profiles(broken, registry=self.registry)
        for bad_origin in (
            {"kind": "aws"},
            {"kind": "pitwall"},
            {"kind": "pitwall", "capability": "x", "url": "ftp://h", "leaseId": None},
            {
                "kind": "pitwall",
                "capability": "x",
                "url": "http://h",
                "leaseId": None,
                "token": "t",
            },
        ):
            with self.subTest(origin=bad_origin):
                broken = example()
                broken["models"]["glimmer"]["origin"] = bad_origin  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
                with self.assertRaises(profiles.ProfilesError):
                    profiles.validate_profiles(broken, registry=self.registry)

    def test_origin_state_and_auto_serve_are_validated_and_normalized(self) -> None:
        data = example()
        models = data["models"]
        self.assertIsInstance(models, dict)
        entry = models["glimmer"]
        self.assertIsInstance(entry, dict)
        entry["origin"] = {
            "kind": "pitwall",
            "capability": "llm.glimmer",
            "leaseId": "lease_abc",
            "url": "http://127.0.0.1:8080",
            "state": "active",
        }
        entry["autoServe"] = {
            "maxUsdPerHour": 2.5,
            "ttlMinutes": 60,
            "idleTimeoutMinutes": 20,
            "readyTimeoutMinutes": 15,
        }
        normalized = profiles.validate_profiles(data, registry=self.registry)
        self.assertEqual("active", normalized["models"]["glimmer"]["origin"]["state"])
        self.assertEqual(entry["autoServe"], normalized["models"]["glimmer"]["autoServe"])

        bad_cases = {
            "bad state": ({"origin": {**entry["origin"], "state": "paused"}}, "state"),
            "non-Pitwall": ({"origin": None, "autoServe": {}}, "Pitwall"),
            "idle too short": ({"autoServe": {"idleTimeoutMinutes": 4}}, "idleTimeoutMinutes"),
            "zero ttl": ({"autoServe": {"ttlMinutes": 0}}, "ttlMinutes"),
            "long ttl": ({"autoServe": {"ttlMinutes": 50000}}, "ttlMinutes"),
            "zero cap": ({"autoServe": {"maxUsdPerHour": 0}}, "maxUsdPerHour"),
            "unknown key": ({"autoServe": {"foo": 1}}, "unknown fields"),
        }
        for label, (changes, needle) in bad_cases.items():
            with self.subTest(case=label):
                broken = example()
                broken_models = broken["models"]
                self.assertIsInstance(broken_models, dict)
                broken_entry = broken_models["glimmer"]
                self.assertIsInstance(broken_entry, dict)
                broken_entry["origin"] = dict(entry["origin"])
                broken_entry["autoServe"] = dict(entry["autoServe"])
                for key, value in changes.items():
                    if value is None:
                        broken_entry.pop(key, None)
                    else:
                        broken_entry[key] = value
                with self.assertRaisesRegex(profiles.ProfilesError, needle):
                    profiles.validate_profiles(broken, registry=self.registry)

    def test_parse_auto_serve_maps_cli_names_and_rejects_bad_values(self) -> None:
        self.assertEqual(
            {
                "maxUsdPerHour": 2.5,
                "ttlMinutes": 60,
                "idleTimeoutMinutes": 20,
                "readyTimeoutMinutes": 15,
            },
            profiles.parse_auto_serve("max-usd-per-hour=2.5,ttl=60,idle=20,ready-timeout=15"),
        )
        for spec in ("idle=abc", "nope=1"):
            with self.subTest(spec=spec), self.assertRaises(profiles.ProfilesError):
                profiles.parse_auto_serve(spec)

    def test_parse_limits_accepts_context_or_output_and_rejects_bad_values(self) -> None:
        self.assertEqual(
            {"context": 32768, "output": 1024}, profiles.parse_limits("context=32768,output=1024")
        )
        self.assertEqual({"context": 32768}, profiles.parse_limits("context=32768"))
        self.assertEqual({"output": 1024}, profiles.parse_limits("output=1024"))
        for spec in ("context=abc", "nope=1"):
            with self.subTest(spec=spec), self.assertRaises(profiles.ProfilesError):
                profiles.parse_limits(spec)

    def test_expiry_state_buckets(self) -> None:
        now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)
        self.assertEqual(("none", None), profiles.expiry_state({"model": "m"}, now=now))
        self.assertEqual(
            ("ok", 3600),
            profiles.expiry_state({"model": "m", "expiresAt": "2026-08-27T11:00:00Z"}, now=now),
        )
        self.assertEqual(
            ("expiring", 600),
            profiles.expiry_state({"model": "m", "expiresAt": "2026-08-27T10:10:00Z"}, now=now),
        )
        self.assertEqual(
            ("expired", -120),
            profiles.expiry_state({"model": "m", "expiresAt": "2026-08-27T09:58:00Z"}, now=now),
        )

    def test_add_route_accepts_expiry_and_origin(self) -> None:
        data = profiles.add_profile(
            profiles.empty_profiles(),
            "glimmer",
            model="m",
            base_url="http://h/v1",
            api_key_env="PITWALL_API_TOKEN",
            expires_at="2026-08-27T10:00:00Z",
            origin={
                "kind": "pitwall",
                "capability": "llm.glimmer",
                "leaseId": None,
                "url": "http://h",
            },
        )
        entry = profiles.validate_profiles(data, registry=self.registry)["models"]["glimmer"]
        self.assertEqual("2026-08-27T10:00:00Z", entry["expiresAt"])
        self.assertIsNone(entry["origin"]["leaseId"])


class RouteResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def resolve(
        self,
        spec: str,
        *,
        data: dict[str, object] | None = None,
        env: dict[str, str] | None = None,
        home: Path | None = None,
        caller: tuple[str, ...] = (),
    ) -> profiles_resolve.ResolvedProfile:
        config = profiles.validate_profiles(
            example() if data is None else data, registry=self.registry
        )
        return profiles_resolve.resolve_profile(
            spec,
            registry=self.registry,
            routes=config,
            env=env or {"PATH": "/nonexistent"},
            home=home or Path("/nonexistent-home"),
            prompt_source="/tmp/p.md",
            caller_args=caller,
        )

    def test_spec_parsing_rejects_malformed_specs(self) -> None:
        self.assertEqual(("glimmer", None), profiles_resolve.parse_spec("glimmer"))
        self.assertEqual(("glimmer", "qwen"), profiles_resolve.parse_spec("glimmer@qwen"))
        for spec in ("", "@qwen", "glimmer@", "glimmer@Q!", "a@b@c"):
            with self.subTest(spec=spec), self.assertRaises(UsageError):
                profiles_resolve.parse_spec(spec)

    def test_registry_model_synthesizes_its_vendor_harness(self) -> None:
        route = self.resolve("gpt-5.6-sol")
        self.assertEqual(
            ("gpt-5.6-sol", "codex", "gpt-5.6-sol"), (route.name, route.harness, route.model)
        )
        self.assertEqual(("/tmp/p.md", "-m", "gpt-5.6-sol"), route.argv)
        self.assertEqual("not-required", route.sync_status)
        claude = self.resolve("sonnet")
        self.assertEqual(("claude", ("claude",)), (claude.harness, claude.native_to))
        self.assertEqual(("/tmp/p.md", "--model", "sonnet"), claude.argv)
        muse = self.resolve("muse-spark-1.2")
        self.assertEqual(("muse", "muse-spark-1.2"), (muse.harness, muse.model))
        self.assertEqual(("/tmp/p.md", "--model", "muse-spark-1.2"), muse.argv)

    def test_route_family_models_pass_through_to_their_vendor_harness(self) -> None:
        # An unregistered id claimed by a harness's routeFamilies patterns (e.g. an effort-suffixed
        # Antigravity slug) resolves like a registry model instead of failing as an unknown route.
        gemini = self.resolve("gemini-3.7-flash-high")
        self.assertEqual(("agy", "gemini-3.7-flash-high"), (gemini.harness, gemini.model))
        self.assertEqual(("/tmp/p.md", "--model", "gemini-3.7-flash-high"), gemini.argv)
        qwen = self.resolve("qwen3-coder-next")
        self.assertEqual(("qwen", "qwen3-coder-next"), (qwen.harness, qwen.model))
        with self.assertRaisesRegex(UsageError, "belongs to agy"):
            self.resolve("gemini-3.7-flash-high@codex")

    def test_entries_pin_harness_and_append_args_before_caller_flags(self) -> None:
        sol = self.resolve("sol", caller=("--extra",))
        self.assertEqual(
            ("/tmp/p.md", "-m", "gpt-5.6-sol", "-c", "model_reasoning_effort=high", "--extra"),
            sol.argv,
        )
        glm = self.resolve("glm")
        self.assertEqual(
            ("opencode", ("zai-coding-plan/glm-5.3", "/tmp/p.md")), (glm.harness, glm.argv)
        )
        self.assertEqual(
            {
                "spec": "glm",
                "name": "glm",
                "harness": "opencode",
                "model": "zai-coding-plan/glm-5.3",
                "endpointHost": None,
                "effort": None,
                "effortSource": None,
            },
            glm.to_public_dict(),
        )

    def test_effort_renders_per_harness_and_harness_defaults_apply(self) -> None:
        data = example()
        data["harnesses"] = {
            "codex": {"effort": "high", "args": ["--full-auto"]},
            "agy": {"effort": "low"},
        }
        data["models"]["fast"] = {"model": "gemini-3.7-flash", "effort": "medium"}  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        data["models"]["sol"] = {"model": "gpt-5.6-sol"}  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        sol = self.resolve("sol", data=data)
        self.assertEqual(("high", "harness"), (sol.effort, sol.effort_source))
        self.assertEqual(
            ("/tmp/p.md", "-m", "gpt-5.6-sol", "--full-auto", "-c", "model_reasoning_effort=high"),
            sol.argv,
        )
        fast = self.resolve("fast", data=data)
        self.assertEqual(("medium", "route"), (fast.effort, fast.effort_source))
        self.assertEqual(
            ("/tmp/p.md", "--model", "gemini-3.7-flash", "--effort", "medium"), fast.argv
        )
        caller = self.resolve("fast", data=data, caller=("--effort", "high"))
        self.assertEqual(("high", "caller"), (caller.effort, caller.effort_source))
        self.assertEqual(
            ("/tmp/p.md", "--model", "gemini-3.7-flash", "--effort", "high"), caller.argv
        )
        plain = self.resolve("gemini-3.7-flash", data=data)
        self.assertEqual(("low", "harness"), (plain.effort, plain.effort_source))
        self.assertEqual(
            {
                "spec": "fast",
                "name": "fast",
                "harness": "agy",
                "model": "gemini-3.7-flash",
                "endpointHost": None,
                "effort": "medium",
                "effortSource": "route",
            },
            fast.to_public_dict(),
        )

    def test_route_effort_must_suit_an_overridden_harness(self) -> None:
        data = example()
        data["models"]["k"] = {"model": "kimi-code/k3", "harness": "opencode", "effort": "max"}  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(ProfileConfigError, "no effort control"):
            self.resolve("k@kimi", data=data)
        row = profiles_resolve.describe_profile(
            "k",
            registry=self.registry,
            routes=profiles.validate_profiles(data, registry=self.registry),
            env={"PATH": "/nonexistent"},
            home=Path("/nonexistent-home"),
        )
        self.assertEqual("max", row["effort"])

    def test_endpoint_entry_uses_env_delivery_and_requires_the_key(self) -> None:
        route = self.resolve("glimmer", env={"PATH": "/nonexistent", "GLIMMER_API_KEY": "sk-test"})
        self.assertEqual("qwen", route.harness)
        self.assertEqual("gpu-1:8000", route.endpoint_host)
        self.assertEqual(
            {
                "OPENAI_BASE_URL": "http://gpu-1:8000/v1",
                "OPENAI_API_KEY": "sk-test",
                "QWEN_MODEL": "meta-models/Muse-Glimmer-30B",
            },
            dict(route.env_updates),
        )
        self.assertEqual(("/tmp/p.md", "-m", "meta-models/Muse-Glimmer-30B"), route.argv)
        self.assertNotIn("sk-test", json.dumps(route.to_public_dict()))
        with self.assertRaisesRegex(ProfileConfigError, "GLIMMER_API_KEY"):
            self.resolve("glimmer")

    def test_broker_token_variable_is_not_an_alias_for_the_agents_token(self) -> None:
        agents_route = example()
        agents_route["models"]["glimmer"]["endpoint"]["apiKeyEnv"] = PITWALL_TOKEN_ENV  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(ProfileConfigError, PITWALL_TOKEN_ENV):
            self.resolve(
                "glimmer",
                data=agents_route,
                env={"PATH": "/nonexistent", BROKER_TOKEN_ENV: "broker-token"},
            )
        resolved = self.resolve(
            "glimmer",
            data=agents_route,
            env={"PATH": "/nonexistent", PITWALL_TOKEN_ENV: "agents-token"},
        )
        self.assertEqual("agents-token", resolved.env_updates["OPENAI_API_KEY"])
        self.assertNotIn("agents-token", json.dumps(resolved.to_public_dict()))

        named_route = example()
        named_route["models"]["glimmer"]["endpoint"]["apiKeyEnv"] = BROKER_TOKEN_ENV  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(ProfileConfigError, BROKER_TOKEN_ENV):
            self.resolve(
                "glimmer",
                data=named_route,
                env={"PATH": "/nonexistent", PITWALL_TOKEN_ENV: "agents-token"},
            )
        resolved = self.resolve(
            "glimmer",
            data=named_route,
            env={"PATH": "/nonexistent", BROKER_TOKEN_ENV: "named-token"},
        )
        self.assertEqual("named-token", resolved.env_updates["OPENAI_API_KEY"])

    def test_arbitrary_endpoint_key_never_uses_the_agents_token(self) -> None:
        data = example()
        data["models"]["glimmer"]["endpoint"]["apiKeyEnv"] = "CUSTOM_PITWALL_TOKEN"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(ProfileConfigError, "CUSTOM_PITWALL_TOKEN"):
            self.resolve(
                "glimmer",
                data=data,
                env={
                    "PATH": "/nonexistent",
                    PITWALL_TOKEN_ENV: "new-token",
                    BROKER_TOKEN_ENV: "legacy-token",
                },
            )

    def test_qwen_output_limit_is_injected_before_entry_environment(self) -> None:
        data = example()
        models = data["models"]
        self.assertIsInstance(models, dict)
        glimmer = models["glimmer"]
        self.assertIsInstance(glimmer, dict)
        glimmer["limits"] = {"output": 1024}
        env = {"PATH": "/nonexistent", "GLIMMER_API_KEY": "key"}
        route = self.resolve("glimmer", data=data, env=env)
        self.assertEqual("1024", route.env_updates["QWEN_CODE_MAX_OUTPUT_TOKENS"])

        glimmer["env"] = {"QWEN_CODE_MAX_OUTPUT_TOKENS": "512"}
        overridden = self.resolve("glimmer", data=data, env=env)
        self.assertEqual("512", overridden.env_updates["QWEN_CODE_MAX_OUTPUT_TOKENS"])

        del glimmer["env"]
        for harness in ("goose",):  # hermes is config-sync; see the materializer tests
            with self.subTest(harness=harness):
                resolved = self.resolve(f"glimmer@{harness}", data=data, env=env)
                self.assertNotIn("QWEN_CODE_MAX_OUTPUT_TOKENS", resolved.env_updates)

    def test_no_model_argument_when_selectors_are_empty(self) -> None:
        harness_def = json.loads(json.dumps(self.registry["harnesses"]["qwen"]))
        harness_def["modelSelectors"] = []
        self.assertEqual(
            ["/tmp/p.md", "--x"],
            profiles_resolve.build_argv(harness_def, "m", "/tmp/p.md", [], ("--x",)),
        )

    def test_dsh_endpoint_uses_headless_profile_without_model_argument(self) -> None:
        data = example()
        models = data["models"]
        self.assertIsInstance(models, dict)
        glimmer = models["glimmer"]
        self.assertIsInstance(glimmer, dict)
        glimmer["harness"] = "dsh"
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = {"PATH": "/nonexistent", "GLIMMER_API_KEY": "k"}
            adapter = get_adapter("dsh")
            plans = adapter.plan_endpoint_sync(models, env, home)
            for path, _, after in plans:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(after, encoding="utf-8")
            route = self.resolve("glimmer", data=data, env=env, home=home)
        self.assertEqual("dsh", route.harness)
        self.assertEqual(("/tmp/p.md", "--profile", "headless"), route.argv)

    def test_endpoint_hooks_extend_argv_and_environment(self) -> None:
        from unittest import mock

        from pitwall.agents.harnesses.qwen import QwenAdapter

        with (
            mock.patch.object(QwenAdapter, "endpoint_argv", return_value=["--provider", "custom"]),
            mock.patch.object(
                QwenAdapter, "endpoint_environment_extras", return_value={"EXTRA": "1"}
            ),
        ):
            route = self.resolve("glimmer", env={"PATH": "/nonexistent", "GLIMMER_API_KEY": "k"})
        self.assertEqual(
            ("/tmp/p.md", "-m", "meta-models/Muse-Glimmer-30B", "--provider", "custom"), route.argv
        )
        self.assertEqual("1", dict(route.env_updates)["EXTRA"])

    def test_keyless_endpoint_injects_no_api_key(self) -> None:
        data = example()
        del data["models"]["glimmer"]["endpoint"]["apiKeyEnv"]  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        route = self.resolve("glimmer", data=data)
        self.assertEqual({"OPENAI_BASE_URL", "QWEN_MODEL"}, set(route.env_updates))

    def test_override_to_config_sync_harness_requires_a_synced_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = {
                "PATH": "/nonexistent",
                "XDG_CONFIG_HOME": str(home / "config"),
                "GLIMMER_API_KEY": "k",
            }
            with self.assertRaisesRegex(ProfileConfigError, "profiles sync --harness opencode"):
                self.resolve("glimmer@opencode", env=env, home=home)
            config = home / "config" / "opencode" / "opencode.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "provider": {
                            "glimmer": {
                                "npm": "@ai-sdk/openai-compatible",
                                "name": "pitwall: glimmer",
                                "options": {
                                    "baseURL": "http://gpu-1:8000/v1",
                                    "apiKey": "{env:GLIMMER_API_KEY}",
                                },
                                "models": {
                                    "meta-models/Muse-Glimmer-30B": {
                                        "name": "meta-models/Muse-Glimmer-30B"
                                    }
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            route = self.resolve("glimmer@opencode", env=env, home=home)
            self.assertEqual("synced", route.sync_status)
            self.assertEqual(("glimmer/meta-models/Muse-Glimmer-30B", "/tmp/p.md"), route.argv)
            self.assertEqual({}, dict(route.env_updates))
            stale = json.loads(config.read_text(encoding="utf-8"))
            stale["provider"]["glimmer"]["options"]["baseURL"] = "http://other:1/v1"
            config.write_text(json.dumps(stale), encoding="utf-8")
            with self.assertRaisesRegex(ProfileConfigError, "stale"):
                self.resolve("glimmer@opencode", env=env, home=home)

    def test_model_bound_harness_rejects_endpoints_and_foreign_models(self) -> None:
        with self.assertRaisesRegex(UsageError, "model-bound"):
            self.resolve("glimmer@codex", env={"PATH": "/nonexistent", "GLIMMER_API_KEY": "k"})
        with self.assertRaisesRegex(UsageError, "belongs to codex"):
            self.resolve("sol@kimi")
        with self.assertRaisesRegex(UsageError, "belongs to opencode"):
            self.resolve("glm@grok")

    def test_pass_through_and_unknown_names(self) -> None:
        route = self.resolve("local/mymodel")
        self.assertEqual(("opencode", ("local/mymodel", "/tmp/p.md")), (route.harness, route.argv))
        self.assertTrue(any("own harness configuration" in notice for notice in route.notices))
        with self.assertRaisesRegex(UsageError, "unknown route 'nosuch'"):
            self.resolve("nosuch")
        with self.assertRaisesRegex(UsageError, "unknown harness"):
            self.resolve("glm@not-a-harness")

    def test_entry_workspace_defaults_are_added_unless_the_caller_sets_them(self) -> None:
        data = example()
        data["models"]["glm"].update({"workspace": "isolated", "taskMode": "write"})  # type: ignore[union-attr]  # reason: the test navigates a loosely typed JSON document
        route = self.resolve("glm", data=data)
        self.assertEqual(
            (
                "zai-coding-plan/glm-5.3",
                "/tmp/p.md",
                "--routing-workspace",
                "isolated",
                "--routing-task-mode",
                "write",
            ),
            route.argv,
        )
        explicit = self.resolve("glm", data=data, caller=("--routing-workspace", "shared"))
        self.assertNotIn("isolated", explicit.argv)
        self.assertIn("--routing-task-mode", explicit.argv)

    def test_expired_routes_are_refused_with_the_remedy(self) -> None:
        data = example()
        data["models"]["glimmer"]["expiresAt"] = "2026-08-27T10:00:00Z"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        data["models"]["glimmer"]["origin"] = {
            "kind": "pitwall",
            "capability": "llm.glimmer",
            "leaseId": "lease_1",
            "url": "http://127.0.0.1:1",
        }  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        env = {"PATH": "/nonexistent", "GLIMMER_API_KEY": "k"}
        config = profiles.validate_profiles(data, registry=self.registry)
        common = {
            "registry": self.registry,
            "routes": config,
            "env": env,
            "home": Path("/nonexistent-home"),
            "prompt_source": "/tmp/p.md",
            "caller_args": (),
        }
        with self.assertRaisesRegex(
            ProfileConfigError, "expired at 2026-08-27T10:00:00Z.*profiles refresh glimmer"
        ):
            profiles_resolve.resolve_profile(
                "glimmer", now=datetime(2026, 8, 27, 10, 5, 0, tzinfo=UTC), **common
            )
        # 30 s past expiry is inside the skew allowance
        profiles_resolve.resolve_profile(
            "glimmer", now=datetime(2026, 8, 27, 10, 0, 30, tzinfo=UTC), **common
        )
        row = profiles_resolve.describe_profile(
            "glimmer",
            registry=self.registry,
            routes=config,
            env=env,
            home=Path("/nonexistent-home"),
            now=datetime(2026, 8, 27, 10, 5, 0, tzinfo=UTC),
        )
        self.assertEqual(("expired", "2026-08-27T10:00:00Z"), (row["status"], row["expiresAt"]))
        self.assertLess(row["expiresIn"], 0)


class GatewaySeatConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def test_gateway_seat_is_accepted_by_validator_and_add_route(self) -> None:
        self.assertIn("gateway", profiles.SEATS)
        data = profiles.add_profile(
            profiles.empty_profiles(),
            "gw",
            model="gw/glm-flash",
            base_url="http://127.0.0.1:8000/v1/openai/coding.chat/v1",
            api_key_env="PITWALL_API_TOKEN",
            seat="gateway",
        )
        normalized = profiles.validate_profiles(data, registry=self.registry)
        entry = normalized["models"]["gw"]
        self.assertIsInstance(entry, dict)
        self.assertEqual("gateway", entry["seat"])
        self.assertEqual(
            "http://127.0.0.1:8000/v1/openai/coding.chat/v1", entry["endpoint"]["baseUrl"]
        )
        self.assertEqual("PITWALL_API_TOKEN", entry["endpoint"]["apiKeyEnv"])
        listed = profiles_resolve.describe_profile(
            "gw",
            registry=self.registry,
            routes=normalized,
            env={"PATH": "/nonexistent"},
            home=Path("/nonexistent-home"),
        )
        self.assertEqual("gateway", listed["seat"])


class RoutesCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.env = {
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "PATH": os.environ.get("PATH", ""),
        }

    def tearDown(self) -> None:
        self.directory.cleanup()

    def cli(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "profiles",
                *args,
            ],
            env=env or self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=HANG_GUARD_SECS,
        )

    def write_routes(self, data: dict[str, object]) -> None:
        write_profiles(Path(self.env["XDG_CONFIG_HOME"]) / "pitwall" / "pitwall.toml", data)

    def routes_file(self) -> dict[str, object]:
        return read_profiles(Path(self.env["XDG_CONFIG_HOME"]) / "pitwall" / "pitwall.toml")

    def test_list_show_and_resolve_report_effort(self) -> None:
        data = example()
        data["harnesses"] = {"codex": {"effort": "high"}}
        data["models"]["sol"] = {"model": "gpt-5.6-sol", "seat": "critical"}  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        self.write_routes(data)
        listing = self.cli("list")
        self.assertEqual(0, listing.returncode, listing.stderr)
        sol_line = next(line for line in listing.stdout.splitlines() if line.startswith("sol\t"))
        self.assertTrue(sol_line.endswith("\thigh"), sol_line)
        shown = json.loads(self.cli("show", "sol").stdout)
        self.assertEqual(
            ("high", "harness"), (shown["resolved"]["effort"], shown["resolved"]["effortSource"])
        )
        resolved = json.loads(self.cli("resolve", "sol", "--json").stdout)
        self.assertEqual("high", resolved["effort"])

    def test_add_list_show_remove_round_trip(self) -> None:
        added = self.cli(
            "add",
            "glimmer",
            "--model",
            "meta-models/Muse-Glimmer-30B",
            "--base-url",
            "http://gpu-1:8000/v1",
            "--api-key-env",
            "GLIMMER_API_KEY",
            "--seat",
            "local",
        )
        self.assertEqual(0, added.returncode, added.stderr)
        path = Path(self.env["XDG_CONFIG_HOME"]) / "pitwall" / "pitwall.toml"
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        listed = self.cli("list")
        self.assertEqual(0, listed.returncode, listed.stderr)
        self.assertIn("glimmer", listed.stdout)
        self.assertIn("qwen", listed.stdout)
        self.assertIn("needs-key", listed.stdout)
        as_json = json.loads(self.cli("list", "--json").stdout)
        self.assertEqual("gpu-1:8000", as_json[0]["endpointHost"])
        shown = self.cli("show", "glimmer", env={**self.env, "GLIMMER_API_KEY": "sk-secret"})
        self.assertEqual(0, shown.returncode, shown.stderr)
        self.assertIn("OPENAI_API_KEY", shown.stdout)
        self.assertNotIn("sk-secret", shown.stdout)
        removed = self.cli("remove", "glimmer")
        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertEqual(1, self.cli("remove", "glimmer").returncode)
        self.assertEqual([], json.loads(self.cli("list", "--json").stdout))

    def test_add_shared_endpoint_round_trip_and_show_both_forms(self) -> None:
        data = example()
        data["endpoints"] = {
            "local": {
                "baseUrl": "http://localhost:9292/v1",
                "apiKeyEnv": "MY_ENDPOINT_KEY",
            }
        }
        self.write_routes(data)

        added = self.cli("add", "local-model", "--model", "local/model", "--endpoint", "local")
        self.assertEqual(0, added.returncode, added.stderr)
        self.assertEqual("local", self.routes_file()["models"]["local-model"]["endpoint"])

        shown = self.cli("show", "local-model", env={**self.env, "MY_ENDPOINT_KEY": "secret"})
        self.assertEqual(0, shown.returncode, shown.stderr)
        payload = json.loads(shown.stdout)
        self.assertEqual("local", payload["entry"]["endpoint"])
        self.assertEqual(
            {"apiKeyEnv": "MY_ENDPOINT_KEY", "baseUrl": "http://localhost:9292/v1"},
            payload["resolvedEndpoint"],
        )

        rejected = self.cli(
            "add",
            "bad",
            "--model",
            "local/model",
            "--endpoint",
            "local",
            "--base-url",
            "http://localhost:9292/v1",
        )
        self.assertEqual(2, rejected.returncode)

    def test_add_accepts_and_validates_effort(self) -> None:
        self.write_routes(example())
        added = self.cli("add", "fast", "--model", "gemini-3.7-flash", "--effort", "medium")
        self.assertEqual(0, added.returncode, added.stderr)
        self.assertEqual("medium", self.routes_file()["models"]["fast"]["effort"])
        rejected = self.cli("add", "bad", "--model", "gemini-3.1-pro", "--effort", "medium")
        self.assertEqual(2, rejected.returncode)
        self.assertIn("low, high", rejected.stderr)

    def test_add_limits_round_trip_and_reject_output_over_context(self) -> None:
        added = self.cli(
            "add",
            "local",
            "--model",
            "local/model",
            "--limits",
            "context=32768,output=1024",
        )
        self.assertEqual(0, added.returncode, added.stderr)
        self.assertEqual(
            {"context": 32768, "output": 1024},
            self.routes_file()["models"]["local"]["limits"],
        )

        rejected = self.cli(
            "add",
            "bad",
            "--model",
            "local/model",
            "--limits",
            "context=1024,output=32768",
        )
        self.assertEqual(2, rejected.returncode)
        self.assertIn("output", rejected.stderr)

    def test_add_env_round_trips_and_entry_env_applies_last(self) -> None:
        added = self.cli(
            "add",
            "local",
            "--model",
            "local/model",
            "--base-url",
            "http://localhost:9292/v1",
            "--env",
            "QWEN_MODEL=entry-model",
            "--env",
            "QWEN_CODE_MAX_OUTPUT_TOKENS=1024",
        )
        self.assertEqual(0, added.returncode, added.stderr)
        saved = self.routes_file()
        self.assertEqual(
            {"QWEN_CODE_MAX_OUTPUT_TOKENS": "1024", "QWEN_MODEL": "entry-model"},
            saved["models"]["local"]["env"],
        )

        registry = load_registry()
        config = profiles.validate_profiles(saved, registry=registry)
        resolved = profiles_resolve.resolve_profile(
            "local",
            registry=registry,
            routes=config,
            env={"QWEN_MODEL": "parent-model"},
            home=Path(self.directory.name),
            prompt_source="<prompt>",
            caller_args=(),
        )
        self.assertEqual("entry-model", resolved.env_updates["QWEN_MODEL"])
        self.assertEqual("1024", resolved.env_updates["QWEN_CODE_MAX_OUTPUT_TOKENS"])

    def test_add_refuses_inline_secrets_and_invalid_values(self) -> None:
        refused = self.cli(
            "add", "x", "--model", "a/b", "--base-url", "http://h/v1", "--api-key", "sk"
        )
        self.assertEqual(2, refused.returncode)
        self.assertIn("apiKeyEnv", refused.stderr)
        bad_seat = self.cli("add", "x", "--model", "a/b", "--seat", "boss")
        self.assertEqual(2, bad_seat.returncode)
        bad_env = self.cli("add", "x", "--model", "a/b", "--env", "NOEQUALS")
        self.assertEqual(2, bad_env.returncode)
        secret_env = self.cli("add", "x", "--model", "a/b", "--env", "MY_API_TOKEN=x")
        self.assertEqual(2, secret_env.returncode)
        self.assertIn("looks like an inline secret", secret_env.stderr)

    def test_add_accepts_gateway_seat_and_lists_it(self) -> None:
        added = self.cli(
            "add",
            "gw",
            "--model",
            "gw/glm-flash",
            "--base-url",
            "http://127.0.0.1:8000/v1/openai/coding.chat/v1",
            "--api-key-env",
            "PITWALL_API_TOKEN",
            "--seat",
            "gateway",
        )
        self.assertEqual(0, added.returncode, added.stderr)
        saved = self.routes_file()["models"]["gw"]
        self.assertEqual("gateway", saved["seat"])
        listed = self.cli("list", "--json")
        self.assertEqual(0, listed.returncode, listed.stderr)
        rows = json.loads(listed.stdout)
        gw_row = next(row for row in rows if row["name"] == "gw")
        self.assertEqual("gateway", gw_row["seat"])

    def test_list_shows_expiry_column(self) -> None:
        self.cli("add", "g", "--model", "m", "--base-url", "http://h/v1")
        path = Path(self.env["XDG_CONFIG_HOME"]) / "pitwall" / "pitwall.toml"
        data = read_profiles(path)
        data["models"]["g"]["expiresAt"] = "2000-01-01T00:00:00Z"
        write_profiles(path, data)
        listed = self.cli("list")
        self.assertIn("expired", listed.stdout)
        self.assertEqual("expired", json.loads(self.cli("list", "--json").stdout)[0]["status"])

    def test_resolve_mirrors_shim_exit_codes_and_reports_native_hosts(self) -> None:
        self.cli(
            "add",
            "glimmer",
            "--model",
            "meta-models/Muse-Glimmer-30B",
            "--base-url",
            "http://gpu-1:8000/v1",
            "--api-key-env",
            "GLIMMER_API_KEY",
        )
        self.assertEqual(78, self.cli("resolve", "glimmer").returncode)
        resolved = self.cli(
            "resolve", "glimmer", "--json", env={**self.env, "GLIMMER_API_KEY": "k"}
        )
        self.assertEqual(0, resolved.returncode, resolved.stderr)
        payload = json.loads(resolved.stdout)
        self.assertEqual("qwen", payload["harness"])
        self.assertEqual(["OPENAI_API_KEY", "OPENAI_BASE_URL", "QWEN_MODEL"], payload["envKeys"])
        self.assertEqual(["<prompt>", "-m", "meta-models/Muse-Glimmer-30B"], payload["argv"])
        self.assertNotIn("k", json.dumps(payload["envKeys"]))
        self.assertEqual(64, self.cli("resolve", "nosuch").returncode)
        native = json.loads(self.cli("resolve", "sonnet", "--json").stdout)
        self.assertEqual(["claude"], native["nativeTo"])


if __name__ == "__main__":
    unittest.main()
