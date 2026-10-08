"""Characterization tests for the argparse-based ``pitwall`` CLI dispatcher.

All tests drive ``pitwall.cli.main`` / ``cmd_*`` with explicit argv and patch
``pitwall.cli`` names; nothing touches the network or a real DB. They lock in
current behavior (exit codes, dispatch routing, dry-run output).
"""

from __future__ import annotations

import importlib
import io
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from runpod.error import QueryError

from pitwall import cli
from pitwall.cli import capabilities as cli_capabilities
from pitwall.cli import config_check as cli_config_check
from pitwall.cli import dashboard as cli_dashboard
from pitwall.cli import endpoints as cli_endpoints
from pitwall.cli import init as cli_init
from pitwall.cli import mcp as cli_mcp
from pitwall.cli import models as cli_models
from pitwall.cli import onboarding as cli_onboarding
from pitwall.cli import output as cli_output
from pitwall.cli import pods as cli_pods
from pitwall.cli import provider_ops as cli_provider_ops
from pitwall.cli import routing as cli_routing
from pitwall.cli import serve_model as cli_serve_model
from pitwall.cli import templates as cli_templates
from pitwall.cli import warm_volume as cli_warm_volume
from pitwall.config import PitwallSettings
from pitwall.cost.budget_gate import BudgetRejected, BudgetSnapshot
from pitwall.serve import ServeResult
from tests.cli.test_models_commands import miniature_catalogue


def test_console_script_is_named_pitwall() -> None:
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    scripts = data["project"]["scripts"]
    assert scripts["pitwall"] == "pitwall.cli:main"
    legacy_script = "pitwall" + "-gpu-broker"
    assert legacy_script not in scripts


def test_repository_has_no_old_script_name() -> None:
    import subprocess
    from pathlib import Path

    root = Path(__file__).parents[2]
    legacy_script = "pitwall" + "-gpu-broker"
    hits = subprocess.run(
        [
            "git",
            "grep",
            "-l",
            legacy_script,
            "--",
            ".",
            ":!docs/evidence",
            ":!docs/superpowers",
            ":!CHANGELOG.md",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert hits == []


def test_plan_only_dispatches_to_serve_model_without_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall import cli

    monkeypatch.delenv("DATABASE_URL", raising=False)
    seen: list[list[str]] = []
    monkeypatch.setattr(cli_serve_model, "cmd_serve_model", lambda argv: seen.append(argv) or 0)

    assert cli.main(["serve", "--plan-only", "--model", "x", "--gpu-class", "y"]) == 0
    assert seen == [["--plan-only", "--model", "x", "--gpu-class", "y"]]
    assert cli.main(["serve-model"]) == 1  # removed name is an error


def test_bare_invocation_opens_console(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall import cli

    launched: list[bool] = []
    monkeypatch.setattr(cli_dashboard, "cmd_dashboard", lambda argv: launched.append(True) or 0)

    assert cli.main([]) == 0
    assert launched == [True]


def test_usage_lists_personal_verbs_first(capsys: pytest.CaptureFixture[str]) -> None:
    from pitwall import cli

    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    first_line = out.splitlines()[0]
    assert first_line.startswith("Usage: pitwall {setup|serve|status|stop|")
    assert "serve-model" not in out
    serve_lines = [line for line in out.splitlines() if line.strip().startswith("pitwall serve ")]
    assert len(serve_lines) == 1, f"serve must be listed once, got {serve_lines}"


def test_registry_serve_help_uses_the_current_command_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The registry-backed `serve --help` epilog must not advertise the removed name."""

    with pytest.raises(SystemExit):
        cli_serve_model._parse_serve_model_args(["--help"])
    out = capsys.readouterr().out
    assert "serve-model" not in out
    assert "pitwall serve --plan-only" in out


def _termination_patch(*, side_effect: Exception | None = None):
    mutation = MagicMock(changed=True, already_absent=False)
    return patch(
        "pitwall.cli.pods._terminate_pod_via_control_plane",
        new=AsyncMock(return_value=mutation, side_effect=side_effect),
    )


async def async_selfhosted_serve_result(*args: Any, **kwargs: Any) -> ServeResult:
    del args, kwargs
    return ServeResult(
        capability="llm.selfhosted",
        lease_id=None,
        expires_at=None,
        model_id="<model-id>",
        proxy_base_url="http://test/v1/openai/llm.selfhosted/v1",
        engine="vllm",
        variant=None,
        gpu_count=1,
        workload_id=None,
        template_id=None,
        provider_id="prov_selfhosted",
        provider_kind="self_hosted",
        dry_run=False,
        created=True,
        cost_estimate_usd=None,
    )


@pytest.mark.usefixtures("registry_backend")
def test_capability_only_cli_prints_self_hosted_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_pool() -> object:
        return object()

    monkeypatch.setenv("DATABASE_URL", "postgresql://test.invalid/pitwall")
    monkeypatch.setattr("pitwall.db.get_pool", fake_pool)
    monkeypatch.setattr("pitwall.config.get_settings", PitwallSettings)
    monkeypatch.setattr("pitwall.models.load_catalogue", object)
    monkeypatch.setattr("pitwall.serve.serve_model", async_selfhosted_serve_result)
    assert cli.main(["serve", "--capability", "llm.selfhosted", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["provider_kind"] == "self_hosted"
    assert body["lease_id"] is None


def test_models_fit_inventory_uses_context_and_avoids_price_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text(
        "inventory:\n"
        "  gpus: [{name: <gpu-name>, count: 1, vram_gb: 24, "
        "arch: sm_86, nvlink: false}]\n",
        encoding="utf-8",
    )
    prices = AsyncMock(side_effect=AssertionError("local fit requested cloud prices"))
    monkeypatch.setattr(cli_models, "load_catalogue", miniature_catalogue)
    monkeypatch.setattr(cli_models, "load_gpu_price_snapshot", prices)
    assert (
        cli.main(
            [
                "models",
                "fit",
                "org/model",
                "--inventory",
                str(path),
                "--context",
                "4096",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["inventory"] == str(path)
    assert result["context_length"] == 4096
    assert "reason" in result["options"][0]


@pytest.mark.parametrize("context", ["0", "-1"])
def test_models_fit_context_requires_positive_context_length(
    context: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models", "fit", "org/model", "--context", context])

    assert exc_info.value.code == 2
    assert "context length must be >= 1" in capsys.readouterr().err


def test_main_no_args_opens_console(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[bool] = []
    monkeypatch.setattr(cli_dashboard, "cmd_dashboard", lambda argv: launched.append(True) or 0)

    assert cli.main([]) == 0
    assert launched == [True]


def test_main_dispatches_provider_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched = MagicMock(return_value=0)
    monkeypatch.setattr(cli_provider_ops, "cmd_provider_ops", dispatched)

    assert cli.main(["provider-ops", "health", "prov-vast", "--json"]) == 0
    dispatched.assert_called_once_with(["health", "prov-vast", "--json"])


def test_main_dispatches_production_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched = MagicMock(return_value=0)
    monkeypatch.setattr(cli_routing, "cmd_routing", dispatched)

    assert cli.main(["routing", "plan", "embedding.test", "--json"]) == 0
    dispatched.assert_called_once_with(["plan", "embedding.test", "--json"])


def test_main_dispatches_runpod_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched = MagicMock(return_value=0)
    monkeypatch.setattr(cli_onboarding, "cmd_runpod_onboard", dispatched)

    assert cli.main(["runpod-onboard", "request.json", "--json"]) == 0
    dispatched.assert_called_once_with(["request.json", "--json"])


@pytest.mark.parametrize("flag", ["-h", "--help", "help"])
def test_main_help_prints_stdout_and_returns_0(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main([flag])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Usage: pitwall" in captured.out
    assert "routing" in captured.out
    assert "runpod-onboard" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_main_version_prints_installed_version(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main([flag])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip()
    assert captured.err == ""


def test_main_unknown_group_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["bogus-group"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "Unknown command group: bogus-group" in err
    assert "Usage: pitwall" in err


def test_main_db_group_delegates_to_db_main() -> None:
    with patch("pitwall.db.main", return_value=0) as db_main:
        rc = cli.main(["db", "status"])
    assert rc == 0
    db_main.assert_called_once_with(["status"])


def test_main_register_template_routes_to_cmd() -> None:
    with patch("pitwall.cli.templates.cmd_register_template", return_value=0) as cmd:
        rc = cli.main(["register-template", "--image", "ghcr.io/x/y:v1", "--dry-run"])
    assert rc == 0
    cmd.assert_called_once_with(["--image", "ghcr.io/x/y:v1", "--dry-run"])


def test_main_terminate_pod_routes_to_cmd() -> None:
    with patch("pitwall.cli.pods.cmd_terminate_pod", return_value=0) as cmd:
        rc = cli.main(["terminate-pod", "--pod-id", "pod_123"])
    assert rc == 0
    cmd.assert_called_once_with(["--pod-id", "pod_123"])


def test_main_register_endpoint_routes_to_cmd() -> None:
    with patch("pitwall.cli.endpoints.cmd_register_endpoint", return_value=0) as cmd:
        rc = cli.main(
            [
                "register-endpoint",
                "--endpoint-id",
                "e1",
                "--provider-type",
                "serverless_queue",
                "--capability-id",
                "c1",
                "--name",
                "n1",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
            ]
        )
    assert rc == 0
    cmd.assert_called_once_with(
        [
            "--endpoint-id",
            "e1",
            "--provider-type",
            "serverless_queue",
            "--capability-id",
            "c1",
            "--name",
            "n1",
            "--gpu-class",
            "NVIDIA H100 80GB HBM3",
        ]
    )


def test_main_set_provider_health_routes_to_cmd() -> None:
    with patch("pitwall.cli.endpoints.cmd_set_provider_health", return_value=0) as cmd:
        rc = cli.main(["set-provider-health", "prov_123", "healthy"])
    assert rc == 0
    cmd.assert_called_once_with(["prov_123", "healthy"])


def test_main_warm_volume_routes_to_cmd() -> None:
    with patch("pitwall.cli.warm_volume.cmd_warm_volume", return_value=0) as cmd:
        rc = cli.main(["warm-volume", "--capability", "c1", "--volume-id", "v1"])
    assert rc == 0
    cmd.assert_called_once_with(["--capability", "c1", "--volume-id", "v1"])


def test_main_burn_rate_routes_to_cmd() -> None:
    with patch("pitwall.cli.burn_rate.cmd_burn_rate", return_value=0) as cmd:
        rc = cli.main(["burn-rate", "--window-days", "7", "--json"])
    assert rc == 0
    cmd.assert_called_once_with(["--window-days", "7", "--json"])


def test_main_cost_routes_to_cmd() -> None:
    with patch("pitwall.cost.cli.cmd_cost", return_value=0) as cmd:
        rc = cli.main(["cost", "workloads", "--limit", "7", "--json"])
    assert rc == 0
    cmd.assert_called_once_with(["workloads", "--limit", "7", "--json"])


def test_main_guardrails_routes_to_cmd() -> None:
    with patch("pitwall.cli.guardrails.cmd_guardrails", return_value=0) as cmd:
        rc = cli.main(["guardrails", "status", "--json"])
    assert rc == 0
    cmd.assert_called_once_with(["status", "--json"])


def test_main_volume_files_routes_to_cmd() -> None:
    with patch("pitwall.cli.volume_files.cmd_volume_files", return_value=0) as cmd:
        rc = cli.main(["volume-files", "logs", "pod-1", "--json"])
    assert rc == 0
    cmd.assert_called_once_with(["logs", "pod-1", "--json"])


def test_main_runpod_resources_routes_to_cmd() -> None:
    with patch("pitwall.cli.runpod_resources.cmd_runpod_resources", return_value=0) as cmd:
        rc = cli.main(["runpod", "pods", "list", "--json"])
    assert rc == 0
    cmd.assert_called_once_with(["pods", "list", "--json"])


def test_main_runpod_catalogue_routes_to_market_cmd() -> None:
    with patch("pitwall.cli.runpod_resources.cmd_runpod_resources", return_value=0) as cmd:
        rc = cli.main(["runpod", "catalogue", "--refresh", "--json"])
    assert rc == 0
    cmd.assert_called_once_with(["catalogue", "--refresh", "--json"])


@pytest.mark.usefixtures("registry_backend")
def test_main_serve_model_routes_to_cmd() -> None:
    argv = [
        "--capability",
        "llm.serve-test",
        "--model",
        "org/model",
        "--gpu-class",
        "NVIDIA H100 80GB HBM3",
    ]
    with patch("pitwall.cli.serve_model.cmd_serve_model", return_value=0) as cmd:
        rc = cli.main(["serve", *argv])
    assert rc == 0
    cmd.assert_called_once_with(argv)


def test_parse_serve_model_args_forwards_every_flag() -> None:
    args = cli_serve_model._parse_serve_model_args(
        [
            "--capability",
            "llm.serve-test",
            "--model",
            "org/model",
            "--gpu-class",
            "NVIDIA H100 80GB HBM3",
            "--gpu-count",
            "2",
            "--engine",
            "llama.cpp",
            "--variant",
            "gguf:q4",
            "--template-id",
            "template-serve",
            "--ttl-minutes",
            "90",
            "--idle-timeout-min",
            "20",
            "--max-usd-per-hour",
            "2.50",
            "--renewal",
            "manual",
            "--route",
            "glimmer",
            "--image",
            "example/llama-server:test",
            "--served-model-name",
            "served-model",
            "--datacenter",
            "US-EXAMPLE-1",
            "--container-disk-gb",
            "80",
            "--rate-per-second",
            "0.004",
            "--gated",
            "--env",
            "ROW_ENV=caller",
            "--env",
            "EMPTY=",
            "--start-arg=--tensor-split",
            "--start-arg=1,1",
            "--dry-run",
            "--idempotency-key",
            "serve-request-1",
            "--json",
        ]
    )
    assert args.capability == "llm.serve-test" and args.model == "org/model"
    assert args.gpu_count == 2 and args.engine == "llama.cpp"
    assert args.variant == "gguf:q4" and args.template_id == "template-serve"
    assert args.ttl_minutes == 90 and args.container_disk_gb == 80
    assert args.idle_timeout_min == 20
    assert args.max_usd_per_hour == Decimal("2.50")
    assert args.renewal == "manual"
    assert args.route == "glimmer"
    assert args.rate_per_second == Decimal("0.004")
    assert args.env == [("ROW_ENV", "caller"), ("EMPTY", "")]
    assert args.start_args == ["--tensor-split", "1,1"]
    assert args.gated is True and args.dry_run is True and args.json is True


def test_parse_serve_model_defaults_and_rejects_bad_env() -> None:
    required = [
        "--capability",
        "llm.serve-test",
        "--model",
        "org/model",
        "--gpu-class",
        "NVIDIA L4",
    ]
    args = cli_serve_model._parse_serve_model_args(required)
    assert (args.gpu_count, args.engine, args.ttl_minutes) == (None, None, None)
    assert args.env == [] and args.start_args == []
    with pytest.raises(SystemExit) as exc_info:
        cli_serve_model._parse_serve_model_args([*required, "--env", "MISSING_EQUALS"])
    assert exc_info.value.code == 2


def test_parse_serve_model_accepts_ttl_alias() -> None:
    args = cli_serve_model._parse_serve_model_args(
        [
            "--capability",
            "llm.serve-test",
            "--model",
            "org/model",
            "--gpu-class",
            "NVIDIA L4",
            "--ttl",
            "45",
        ]
    )

    assert args.ttl_minutes == 45


def test_serve_model_rejects_overprecise_price_cap_with_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    rc = cli_serve_model.cmd_serve_model(
        [
            "--capability",
            "llm.serve-test",
            "--model",
            "org/model",
            "--gpu-class",
            "NVIDIA L4",
            "--max-usd-per-hour",
            "0.12345",
        ]
    )

    assert rc == 1
    assert "max_usd_per_hour" in capsys.readouterr().err


def test_parse_serve_model_accepts_sglang_without_changing_default() -> None:
    required = [
        "--capability",
        "llm.serve-test",
        "--model",
        "org/model",
        "--gpu-class",
        "NVIDIA L4",
    ]

    assert (
        cli_serve_model._parse_serve_model_args([*required, "--engine", "sglang"]).engine
        == "sglang"
    )
    assert cli_serve_model._parse_serve_model_args(required).engine is None


def test_serve_model_catalogue_engine_is_resolved_when_flag_is_omitted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.config import PitwallSettings
    from pitwall.serve import ServeResult

    seen: dict[str, Any] = {}

    async def fake_serve(*args: Any, **kwargs: Any) -> ServeResult:
        seen["request"] = args[1]
        return ServeResult(
            capability="llm.qwen",
            lease_id=None,
            expires_at=None,
            model_id="Qwen/Qwen3.8-27B",
            proxy_base_url="http://127.0.0.1:8080/v1/openai/llm.qwen/v1",
            engine="llama.cpp",
            variant="gguf:UD-Q4_K_XL",
            gpu_count=1,
            workload_id=None,
            template_id="template-plan",
            provider_id="prov-serve",
            dry_run=True,
            created=True,
            cost_estimate_usd="0.48",
        )

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr("pitwall.db.get_pool", AsyncMock(return_value=object()))
    monkeypatch.setattr("pitwall.config.get_settings", PitwallSettings)
    monkeypatch.setattr("pitwall.models.load_catalogue", object)
    monkeypatch.setattr("pitwall.serve.serve_model", fake_serve)

    rc = cli_serve_model.cmd_serve_model(
        [
            "--capability",
            "llm.qwen",
            "--model",
            "Qwen/Qwen3.8-27B",
            "--gpu-class",
            "NVIDIA GeForce RTX 4090",
            "--variant",
            "gguf:UD-Q4_K_XL",
            "--dry-run",
        ]
    )

    assert rc == 0
    assert seen["request"].engine is None
    assert "engine: llama.cpp" in capsys.readouterr().out


@pytest.mark.parametrize("json_flag", [[], ["--json"]])
def test_serve_model_requires_database_url_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    json_flag: list[str],
) -> None:
    service = AsyncMock(return_value=0)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(cli_serve_model, "_serve_model_async", service)

    rc = cli_serve_model.cmd_serve_model(
        [
            "--capability",
            "llm.serve-test",
            "--model",
            "org/model",
            "--gpu-class",
            "NVIDIA L4",
            "--dry-run",
            *json_flag,
        ]
    )

    assert rc == 2
    service.assert_not_awaited()
    message = (
        "serve needs DATABASE_URL (registry-backed dry run); run "
        "`pitwall init` or export DATABASE_URL"
    )
    captured = capsys.readouterr()
    if json_flag:
        assert json.loads(captured.out) == {"error": "missing_database_url", "detail": message}
    else:
        assert message in captured.err


def test_serve_model_plan_only_succeeds_without_database_or_pool(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.config import PitwallSettings
    from pitwall.serve import ServePlanResult

    get_pool = AsyncMock(side_effect=AssertionError("plan-only must not create a pool"))
    plan = AsyncMock(
        return_value=ServePlanResult(
            model_id="qwen3.8-27b",
            engine="llama.cpp",
            variant="gguf:UD-Q4_K_XL",
            gpu_class="NVIDIA GeForce RTX 3090",
            gpu_count=1,
            image="example/llama.cpp:cuda",
            argv=["--hf-repo", "unsloth/Qwen3.8-27B-GGUF"],
            volume_cache_env={"LLAMA_CACHE": "/workspace/llama-cache"},
            fit="fits",
            startup_timeout_s=900,
            cost_estimate_usd=None,
            price_source="fallback",
        )
    )
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)
    monkeypatch.setattr("pitwall.config.get_settings", PitwallSettings)
    monkeypatch.setattr("pitwall.models.load_catalogue", object)
    monkeypatch.setattr("pitwall.serve.plan_catalogue_model", plan)

    argv = [
        "--plan-only",
        "--model",
        "Qwen/Qwen3.8-27B",
        "--variant",
        "gguf:UD-Q4_K_XL",
        "--gpu-class",
        "RTX_3090",
    ]
    rc = cli.main(["serve", *argv, "--json"])

    assert rc == 0
    get_pool.assert_not_awaited()
    request = plan.await_args.args[0]
    assert request.gpu_class == "RTX_3090"
    assert json.loads(capsys.readouterr().out)["price_source"] == "fallback"

    assert cli.main(["serve", *argv]) == 0
    plain = capsys.readouterr().out
    assert "Serve Model Plan" in plain
    assert 'argv: ["--hf-repo", "unsloth/Qwen3.8-27B-GGUF"]' in plain
    assert "price_source: fallback" in plain
    get_pool.assert_not_awaited()


def test_serve_model_plan_only_and_dry_run_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_serve_model._parse_serve_model_args(
            [
                "--plan-only",
                "--dry-run",
                "--model",
                "org/model",
                "--gpu-class",
                "NVIDIA L4",
            ]
        )

    assert exc_info.value.code == 2


def test_serve_model_help_includes_operator_examples(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_serve_model._parse_serve_model_args(["--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "NVIDIA GeForce RTX 4090" in help_text
    assert "--variant gguf:UD-Q4_K_XL" in help_text
    assert "--image example/vllm-openai:latest" in help_text
    assert "--ttl-minutes, --ttl N" in help_text
    assert "alias of --ttl-minutes" in " ".join(help_text.split())
    assert "--plan-only" in help_text


def test_serve_model_json_delegates_to_shared_service(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.config import PitwallSettings
    from pitwall.serve import ServeResult

    pool = object()
    catalogue = object()
    seen: dict[str, Any] = {}

    async def fake_pool() -> object:
        return pool

    async def fake_serve(
        actual_pool: Any,
        request: Any,
        *,
        base_url: str,
        settings: Any,
        catalogue: Any = None,
    ) -> ServeResult:
        seen.update(
            pool=actual_pool,
            request=request,
            base_url=base_url,
            settings=settings,
            catalogue=catalogue,
        )
        return ServeResult(
            capability="llm.serve-test",
            lease_id=None,
            expires_at=None,
            model_id="org/model",
            proxy_base_url=("http://127.0.0.1:8080/v1/openai/llm.serve-test/v1"),
            engine="vllm",
            variant=None,
            gpu_count=1,
            workload_id=None,
            template_id="template-plan",
            provider_id="prov-serve",
            dry_run=True,
            created=True,
            cost_estimate_usd="0.48",
        )

    settings = PitwallSettings(pitwall_base_url="http://127.0.0.1:8080/")
    monkeypatch.setattr("pitwall.db.get_pool", fake_pool)
    monkeypatch.setattr("pitwall.config.get_settings", lambda: settings)
    monkeypatch.setattr("pitwall.models.load_catalogue", lambda: catalogue)
    monkeypatch.setattr("pitwall.serve.serve_model", fake_serve)

    argv = [
        "--capability",
        "llm.serve-test",
        "--model",
        "org/model",
        "--gpu-class",
        "NVIDIA L4",
        "--rate-per-second",
        "0.004",
        "--env",
        "ROW_ENV=caller",
        "--start-arg=--trust-remote-code",
        "--dry-run",
    ]
    rc = cli_serve_model.cmd_serve_model([*argv, "--json"])
    output = json.loads(capsys.readouterr().out)
    assert rc == 0 and output["cost_estimate_usd"] == "0.48"
    assert seen["pool"] is pool and seen["catalogue"] is catalogue
    assert seen["base_url"] == "http://127.0.0.1:8080"
    assert seen["request"].env == {"ROW_ENV": "caller"}
    assert seen["request"].start_args == ["--trust-remote-code"]

    plain_rc = cli_serve_model.cmd_serve_model(argv)
    assert plain_rc == 0
    assert "dry_run: True" in capsys.readouterr().out


def test_serve_model_capability_only_delegates_and_no_history_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pitwall.api.exceptions import ServeNoServeHistory

    monkeypatch.setenv("DATABASE_URL", "postgresql://test.invalid/pitwall")
    delegated = AsyncMock(return_value=0)
    monkeypatch.setattr(cli_serve_model, "_serve_model_async", delegated)
    assert cli_serve_model.cmd_serve_model(["--capability", "llm.history", "--json"]) == 0
    args = delegated.await_args.args[0]
    assert args.model is None and args.gpu_class is None

    delegated.side_effect = ServeNoServeHistory("llm.missing")
    assert cli_serve_model.cmd_serve_model(["--capability", "llm.missing", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "no_serve_history"


def test_serve_model_rejects_secret_start_arg_with_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("pitwall.config.get_settings", PitwallSettings)
    monkeypatch.setattr("pitwall.models.load_catalogue", object)

    assert (
        cli_serve_model.cmd_serve_model(
            [
                "--plan-only",
                "--model",
                "org/model",
                "--gpu-class",
                "NVIDIA L4",
                "--start-arg=--token=hf_test_cli_token",
            ]
        )
        == 1
    )
    # Validation errors report the field and error type only, never message text.
    err = capsys.readouterr().err
    assert "start_args (value_error)" in err
    assert "hf_test_cli_token" not in err


@pytest.mark.parametrize(
    ("error_name", "error_args", "expected"),
    [
        ("ServeConflict", ("llm.serve-test", "org/other"), 2),
        ("ServeInvalidGpuClass", ("L4", ("NVIDIA L4",)), 2),
        ("ServeRateRequired", ("rate is required",), 2),
        ("ServeUnknownVariant", ("org/model", "missing"), 2),
        ("ServeTemplateInvalid", ("image is required",), 2),
        ("ServeVerificationFailed", ("org/model", ["other"]), 1),
        ("ServeLaunchFailed", ("launch failed",), 1),
    ],
)
def test_serve_model_maps_service_errors(
    error_name: str,
    error_args: tuple[Any, ...],
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exceptions = importlib.import_module("pitwall.api.exceptions")
    error = getattr(exceptions, error_name)(*error_args)
    monkeypatch.setattr(cli_serve_model, "_serve_model_async", AsyncMock(side_effect=error))
    rc = cli_serve_model.cmd_serve_model(
        [
            "--capability",
            "llm.serve-test",
            "--model",
            "org/model",
            "--gpu-class",
            "NVIDIA L4",
            "--json",
        ]
    )
    assert rc == expected
    assert json.loads(capsys.readouterr().out)["error"] == error.error_code


def _invalid_serve_request() -> Exception:
    from pydantic import ValidationError

    from pitwall.serve import ServeRequest

    try:
        ServeRequest.model_validate({"capability_name": f"bad name {_LEAK_MARKER}"})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


@pytest.mark.parametrize(
    ("make_error", "code", "exception"),
    [
        (lambda: QueryError(f"Unauthorized {_LEAK_MARKER}"), "launch_failed", "QueryError"),
        (
            lambda: importlib.import_module("pitwall.runpod_client.pods").RunPodError(
                f"pod create failed {_LEAK_MARKER}"
            ),
            "launch_failed",
            "RunPodError",
        ),
        (
            lambda: importlib.import_module("pitwall.api.exceptions").ServeLaunchFailed(
                f"upstream said {_LEAK_MARKER}"
            ),
            "launch_failed",
            "ServeLaunchFailed",
        ),
        (
            lambda: importlib.import_module("pitwall.api.exceptions").ServeNoServeHistory(
                f"cap {_LEAK_MARKER}"
            ),
            "no_serve_history",
            "ServeNoServeHistory",
        ),
        (_invalid_serve_request, "invalid_request", "ValidationError"),
    ],
)
@pytest.mark.parametrize("as_json", [False, True])
def test_serve_model_sibling_branches_never_reflect_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_error: Any,
    code: str,
    exception: str,
    as_json: bool,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(cli_serve_model, "_serve_model_async", AsyncMock(side_effect=make_error()))
    argv = ["--capability", "llm.serve-test", "--model", "org/model", "--gpu-class", "NVIDIA L4"]
    rc = cli_serve_model.cmd_serve_model([*argv, *(["--json"] if as_json else [])])
    captured = capsys.readouterr()
    assert rc in {1, 2}
    assert "SYNTHETIC_TEST_MARKER" not in captured.out + captured.err
    assert code in captured.out + captured.err
    if as_json:
        body = json.loads(captured.out)
        assert body["error"] == code
        assert body["exception" if code != "invalid_request" else "errors"]


def test_serve_model_request_validation_reports_field_locations_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    argv = ["--capability", f"bad name {_LEAK_MARKER}", "--max-usd-per-hour", "1", "--json"]
    assert cli_serve_model.cmd_serve_model(argv) == 1
    captured = capsys.readouterr()
    assert "SYNTHETIC_TEST_MARKER" not in captured.out + captured.err
    body = json.loads(captured.out)
    assert body["error"] == "invalid_request"
    assert body["errors"] == [{"loc": "capability_name", "type": "string_pattern_mismatch"}]


def test_serve_model_maps_runpod_query_error_to_launch_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli_serve_model, "_serve_model_async", AsyncMock(side_effect=QueryError("Unauthorized"))
    )
    rc = cli_serve_model.cmd_serve_model(
        [
            "--capability",
            "llm.serve-test",
            "--model",
            "org/model",
            "--gpu-class",
            "NVIDIA L4",
            "--json",
        ]
    )
    assert rc == 1
    # A provider error's text is never relayed: the code and the exception class only.
    assert json.loads(capsys.readouterr().out) == {
        "error": "launch_failed",
        "exception": "QueryError",
    }


def test_serve_model_maps_budget_rejection_to_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = BudgetSnapshot(
        monthly_budget_usd=Decimal("10"),
        per_request_max_usd=Decimal("5"),
        mtd_spend_usd=Decimal("9"),
        estimate_usd=Decimal("2"),
        budget_remaining_usd=Decimal("1"),
    )
    monkeypatch.setattr(
        cli_serve_model,
        "_serve_model_async",
        AsyncMock(side_effect=BudgetRejected("monthly_budget", snapshot)),
    )
    rc = cli_serve_model.cmd_serve_model(
        [
            "--capability",
            "llm.serve-test",
            "--model",
            "org/model",
            "--gpu-class",
            "NVIDIA L4",
            "--json",
        ]
    )
    assert rc == 2
    body = json.loads(capsys.readouterr().out)
    assert body["error"] == "budget_exhausted"
    assert body["reason"] == "monthly_budget"
    assert body["snapshot"]["estimate_usd"] == "2"


def test_serve_model_maps_kill_switch_rejection_to_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.api.admin.kill_switch import KillSwitchEngaged

    monkeypatch.setattr(
        cli_serve_model,
        "_serve_model_async",
        AsyncMock(side_effect=KillSwitchEngaged()),
    )
    rc = cli_serve_model.cmd_serve_model(
        [
            "--capability",
            "llm.serve-test",
            "--model",
            "org/model",
            "--gpu-class",
            "NVIDIA L4",
            "--json",
        ]
    )

    assert rc == 2
    assert json.loads(capsys.readouterr().out) == {"error": "kill_switch_engaged"}


def test_cmd_seed_routes_to_seed_async_not_warm_volume() -> None:
    with (
        patch("pitwall.cli.capabilities._seed_async", new=AsyncMock(return_value=0)) as seed_async,
        patch(
            "pitwall.cli.warm_volume._warm_volume_async", new=AsyncMock(return_value=0)
        ) as warm_volume_async,
    ):
        rc = cli_capabilities.cmd_seed(["seed/providers.yaml"])

    assert rc == 0
    seed_async.assert_awaited_once()
    args = seed_async.await_args.args[0]
    assert args.paths == ["seed/providers.yaml"]
    warm_volume_async.assert_not_called()


def test_main_mcp_routes_to_cmd() -> None:
    with patch("pitwall.cli.mcp.cmd_mcp", return_value=0) as cmd:
        rc = cli.main(["mcp", "serve", "broker"])
    assert rc == 0
    cmd.assert_called_once_with(["serve", "broker"])


def test_main_config_check_routes_to_cmd() -> None:
    with patch("pitwall.cli.config_check.cmd_config", return_value=0) as cmd:
        rc = cli.main(["config", "check", "api"])
    assert rc == 0
    cmd.assert_called_once_with(["check", "api"])


def test_register_template_dry_run_is_hermetic(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_templates.cmd_register_template(["--image", "ghcr.io/org/worker:v1", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Template previewed:" in out


def test_register_template_guard_rejects_before_pool_without_reflection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "sk-template-canary-1234567890abcdef"
    get_pool = AsyncMock(side_effect=AssertionError("pool opened before inspection"))
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    with patch("pitwall.db.get_pool", get_pool):
        rc = cli_templates.cmd_register_template(["--image", canary, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload == {"error": "template_operation_failed"}
    assert canary not in str(payload)
    get_pool.assert_not_awaited()


def test_register_template_dry_run_guard_rejects_before_env_selection_without_reflection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "sk-template-preview-canary-1234567890abcdef"
    select_registry_auth = MagicMock(side_effect=AssertionError("env read before inspection"))
    monkeypatch.setattr(cli_templates, "get_registry_auth_id_from_env", select_registry_auth)

    rc = cli_templates.cmd_register_template(["--image", canary, "--dry-run", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload == {"error": "template_operation_failed"}
    assert canary not in str(payload)
    select_registry_auth.assert_not_called()


@pytest.mark.parametrize(
    ("pod", "expected"),
    [
        ({"desiredStatus": "EXITED"}, True),
        ({"desiredStatus": "TERMINATED"}, True),
        ({"desiredStatus": "RUNNING"}, False),
        ({}, False),
    ],
    ids=["exited", "terminated", "running", "empty"],
)
def test_is_terminated(pod: dict[str, object], expected: bool) -> None:
    assert cli_pods._is_terminated(pod) is expected


def test_terminate_pod_missing_api_key_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # HOME is redirected so a real runpodctl credential on the developer's
    # machine cannot satisfy the fallback and hide this failure.
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "RUNPOD_API_KEY" in err
    assert "runpodctl" in err


def test_terminate_pod_accepts_the_saved_runpodctl_credential(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Orphaned-pod recovery must work for a user who only authenticated runpodctl."""
    config = tmp_path / ".runpod" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('apikey = "rp-from-runpodctl"\n', encoding="utf-8")
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    with _termination_patch():
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123", "--no-verify"])

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "RUNPOD_API_KEY not set" not in captured.err
    assert "rp-from-runpodctl" not in captured.out + captured.err


def test_terminate_pod_guard_rejects_before_pool_without_reflection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "sk-terminate-canary-1234567890abcdef"
    get_pool = AsyncMock(side_effect=AssertionError("pool opened before inspection"))
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    with patch("pitwall.db.get_pool", get_pool):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", canary, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload == {"error": "terminate_failed"}
    assert canary not in str(payload)
    get_pool.assert_not_awaited()


def test_terminate_pod_no_verify_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    with _termination_patch() as term:
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123", "--no-verify"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "terminate requested for pod_123" in out
    term.assert_awaited_once_with("pod_123")


def test_terminate_pod_raise_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    with _termination_patch(side_effect=RuntimeError("boom")):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_x"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "pod termination failed" in err
    assert "boom" not in err
    assert "Manual teardown" in err


def test_terminate_pod_verify_pod_gone_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    with (
        _termination_patch(),
        patch("pitwall.cli.pods.get_pod_sync", return_value=None),
    ):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no longer returned by RunPod" in out


def test_terminate_pod_verify_reaches_exited(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    with (
        _termination_patch(),
        patch("pitwall.cli.pods.get_pod_sync", return_value={"desiredStatus": "EXITED"}),
    ):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "desiredStatus=EXITED" in out


def test_parse_mcp_serve_args_defaults() -> None:
    ns = cli_mcp._parse_mcp_args(["serve", "broker"])
    assert ns.transport == "stdio"
    assert ns.command == "serve"
    assert ns.server == "broker"


def test_parse_mcp_serve_args_rejects_network_transport() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_mcp._parse_mcp_args(["serve", "broker", "--transport", "sse"])
    assert exc_info.value.code == 2


def test_parse_mcp_serve_args_requires_serve_subcommand() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_mcp._parse_mcp_args([])
    assert exc_info.value.code == 2


@pytest.mark.parametrize("argv", [["--help"], ["serve", "--help"], ["serve", "broker", "--help"]])
def test_parse_mcp_serve_args_help_exits_zero(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_mcp._parse_mcp_args(argv)
    assert exc_info.value.code == 0


def test_main_mcp_serve_reaches_server_run() -> None:
    from pitwall import mcp

    with (
        patch("pitwall.mcp.ensure_runtime_env"),
        patch.object(mcp.mcp, "run") as run,
    ):
        rc = cli.main(["mcp", "serve", "broker"])

    assert rc == 0
    run.assert_called_once_with(transport="stdio")


def test_main_mcp_serve_json_emits_transport(capsys: pytest.CaptureFixture[str]) -> None:
    from pitwall import mcp

    with (
        patch("pitwall.mcp.ensure_runtime_env"),
        patch.object(mcp.mcp, "run"),
    ):
        rc = cli.main(["mcp", "serve", "broker", "--json"])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["transport"] == "stdio"


def test_cmd_mcp_serve_writes_nothing_to_stdout_before_mcp_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stdout must remain empty before ``mcp.run`` is called, so the
    MCP stdio transport can use it exclusively (2026-07-28 stdio binding)."""

    class RecordingStdout(io.StringIO):
        pass

    stdout = RecordingStdout()
    monkeypatch.setattr(sys, "stdout", stdout)
    seen: dict[str, object] = {}

    def fake_run(*, transport: str) -> None:
        seen["stdout_value"] = stdout.getvalue()

    from pitwall import mcp

    with (
        patch("pitwall.mcp.ensure_runtime_env"),
        patch.object(mcp.mcp, "run", side_effect=fake_run),
    ):
        rc = cli.main(["mcp", "serve", "broker", "--json"])

    assert rc == 0
    assert seen["stdout_value"] == ""


def test_parse_terminate_pod_args_defaults() -> None:
    ns = cli_pods._parse_terminate_pod_args(["--pod-id", "pod_x"])
    assert ns.pod_id == "pod_x"
    assert ns.no_verify is False
    assert ns.verify_timeout_s == cli_pods._TERMINATE_VERIFY_TIMEOUT_S


def test_parse_terminate_pod_args_no_verify() -> None:
    ns = cli_pods._parse_terminate_pod_args(["--pod-id", "pod_x", "--no-verify"])
    assert ns.no_verify is True


def test_parse_terminate_pod_args_custom_timeout() -> None:
    ns = cli_pods._parse_terminate_pod_args(["--pod-id", "pod_x", "--verify-timeout-s", "30.0"])
    assert ns.verify_timeout_s == 30.0


def test_parse_register_template_args_defaults() -> None:
    ns = cli_templates._parse_register_template_args(["--image", "img:v1"])
    assert ns.image == "img:v1"
    assert ns.template_name == "pitwall-cloud-worker"
    assert ns.container_disk_gb == 50
    assert ns.dry_run is False


def test_parse_register_template_args_dry_run() -> None:
    ns = cli_templates._parse_register_template_args(["--image", "img:v1", "--dry-run"])
    assert ns.dry_run is True


def test_parse_register_template_args_custom_template() -> None:
    ns = cli_templates._parse_register_template_args(
        ["--image", "img:v1", "--template-name", "my-template", "--container-disk-gb", "100"]
    )
    assert ns.template_name == "my-template"
    assert ns.container_disk_gb == 100


def test_parse_warm_volume_args_defaults() -> None:
    ns = cli_warm_volume._parse_warm_volume_args(["--model", "org/model", "--volume-id", "v1"])
    assert ns.model == "org/model"
    assert ns.volume_id == "v1"
    assert ns.variant is None
    assert ns.gpu_class is None
    assert ns.dry_run is False


def test_parse_warm_volume_args_full() -> None:
    ns = cli_warm_volume._parse_warm_volume_args(
        [
            "--model",
            "org/model",
            "--volume-id",
            "v1",
            "--variant",
            "fp8",
            "--gpu-class",
            "NVIDIA GeForce RTX 4090",
            "--dry-run",
        ]
    )
    assert ns.variant == "fp8"
    assert ns.gpu_class == "NVIDIA GeForce RTX 4090"
    assert ns.dry_run is True


def test_parse_register_endpoint_args_required_only() -> None:
    ns = cli_endpoints._parse_register_endpoint_args(
        [
            "--endpoint-id",
            "e1",
            "--provider-type",
            "serverless_queue",
            "--capability-id",
            "c1",
            "--name",
            "my-provider",
            "--gpu-class",
            "NVIDIA H100 80GB HBM3",
        ]
    )
    assert ns.endpoint_id == "e1"
    assert ns.provider_type == "serverless_queue"
    assert ns.capability_id == "c1"
    assert ns.name == "my-provider"
    assert ns.gpu_class == "NVIDIA H100 80GB HBM3"
    assert ns.region is None
    assert ns.cost_mode is None
    assert ns.workers_min == 0
    assert ns.idle_timeout_minutes == 0
    assert ns.flash_boot_verified is False
    assert ns.max_payload_mb == 30
    assert ns.request_timeout_s == 330
    assert ns.priority == 0
    assert ns.health == "unknown"


def test_parse_register_endpoint_args_all_options() -> None:
    ns = cli_endpoints._parse_register_endpoint_args(
        [
            "--endpoint-id",
            "e1",
            "--provider-type",
            "serverless_lb",
            "--capability-id",
            "c1",
            "--name",
            "my-provider",
            "--gpu-class",
            "NVIDIA H100 80GB HBM3",
            "--capability-name",
            "llm.qwen3-32b",
            "--region",
            "US-KS-2",
            "--cost-mode",
            "per_second",
            "--per-second-active",
            "0.0001",
            "--per-request",
            "0.002",
            "--per-million-input-tokens",
            "0.5",
            "--per-million-output-tokens",
            "1.5",
            "--workers-min",
            "1",
            "--workers-max",
            "10",
            "--idle-timeout-minutes",
            "5",
            "--flash-boot-verified",
            "--max-payload-mb",
            "50",
            "--request-timeout-s",
            "60",
            "--priority",
            "3",
            "--health",
            "healthy",
        ]
    )
    assert ns.capability_name == "llm.qwen3-32b"
    assert ns.region == "US-KS-2"
    assert ns.cost_mode == "per_second"
    assert ns.per_second_active == 0.0001
    assert ns.per_request == 0.002
    assert ns.per_million_input_tokens == 0.5
    assert ns.per_million_output_tokens == 1.5
    assert ns.workers_min == 1
    assert ns.workers_max == 10
    assert ns.idle_timeout_minutes == 5
    assert ns.flash_boot_verified is True
    assert ns.max_payload_mb == 50
    assert ns.request_timeout_s == 60
    assert ns.priority == 3
    assert ns.health == "healthy"


def test_parse_set_provider_health_args() -> None:
    ns = cli_endpoints._parse_set_provider_health_args(["prov_123", "healthy"])
    assert ns.provider_id == "prov_123"
    assert ns.health == "healthy"


def test_register_template_async_exception_handler(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def raise_error(args):
        raise RuntimeError("db connection failed")

    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setattr(cli_templates, "_register_template_async", raise_error)
    rc = cli_templates.cmd_register_template(["--image", "ghcr.io/org/worker:v1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "Error:" in err


def test_register_endpoint_async_exception_handler(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def raise_error(args):
        raise RuntimeError("endpoint error")

    monkeypatch.setattr(cli_endpoints, "_register_endpoint_async", raise_error)
    rc = cli_endpoints.cmd_register_endpoint(
        [
            "--endpoint-id",
            "e1",
            "--provider-type",
            "serverless_queue",
            "--capability-id",
            "c1",
            "--name",
            "n1",
            "--gpu-class",
            "NVIDIA H100 80GB HBM3",
        ]
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "Error:" in err


_LEAK_MARKER = "password=SYNTHETIC_TEST_MARKER"


@pytest.mark.parametrize(
    ("module", "entry", "async_name", "argv", "code"),
    [
        (
            cli_capabilities,
            "cmd_create_capability",
            "_create_capability_async",
            ["--name", "embedding.demo", "--class", "embedding"],
            "create_capability_failed",
        ),
        (cli_capabilities, "cmd_seed", "_seed_async", ["seed/providers.yaml"], "seed_failed"),
        (
            cli_endpoints,
            "cmd_register_endpoint",
            "_register_endpoint_async",
            [
                "--endpoint-id",
                "e1",
                "--provider-type",
                "serverless_queue",
                "--capability-id",
                "c1",
                "--name",
                "n1",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
            ],
            "register_endpoint_failed",
        ),
        (
            cli_endpoints,
            "cmd_set_provider_health",
            "_set_provider_health_async",
            ["prov_123", "healthy"],
            "set_provider_health_failed",
        ),
        (cli_init, "cmd_init", "_init_async", ["--non-interactive"], "init_failed"),
        (
            cli_serve_model,
            "cmd_serve_model",
            "_serve_model_async",
            ["--capability", "llm.serve-test", "--model", "org/model", "--gpu-class", "NVIDIA L4"],
            "serve_failed",
        ),
    ],
)
@pytest.mark.parametrize("as_json", [False, True])
def test_cli_error_boundaries_never_reflect_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    module: Any,
    entry: str,
    async_name: str,
    argv: list[str],
    code: str,
    as_json: bool,
) -> None:
    async def failing(*args: object, **kwargs: object) -> int:
        raise RuntimeError(f"connection failed: {_LEAK_MARKER}")

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(module, async_name, failing)
    rc = getattr(module, entry)([*argv, *(["--json"] if as_json else [])])
    captured = capsys.readouterr()
    assert rc == 1
    assert "SYNTHETIC_TEST_MARKER" not in captured.out + captured.err
    assert code in captured.out + captured.err
    assert "RuntimeError" in captured.out + captured.err
    if as_json:
        assert json.loads(captured.out) == {"error": code, "exception": "RuntimeError"}


def test_warm_volume_async_exception_handler(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def raise_error(args, out):
        raise RuntimeError("warm error")

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(cli_warm_volume, "_warm_volume_async", raise_error)
    rc = cli_warm_volume.cmd_warm_volume(["--model", "org/model", "--volume-id", "v1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "warm-volume failed" in err
    assert "warm error" not in err


def test_warm_volume_guard_rejects_before_catalogue_price_or_pool(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "sk-warm-volume-canary-1234567890abcdef"
    forbidden = MagicMock(side_effect=AssertionError("downstream opened before inspection"))
    get_pool = AsyncMock(side_effect=AssertionError("pool opened before inspection"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(cli_warm_volume, "load_catalogue", forbidden)
    monkeypatch.setattr(
        cli_warm_volume, "load_gpu_price_snapshot", AsyncMock(side_effect=forbidden)
    )
    with patch("pitwall.db.get_pool", get_pool):
        rc = cli_warm_volume.cmd_warm_volume(
            ["--model", canary, "--volume-id", "volume-cache", "--json"]
        )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert payload["error"] == "pre_spend_payload_rejected"
    assert canary not in str(payload)
    forbidden.assert_not_called()
    get_pool.assert_not_awaited()


def test_warm_volume_json_includes_resolved_gpu_and_price_source(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.serve import WarmResult

    async def fake_warm(_args: Any, out: cli_output.Output) -> int:
        out.set_json(
            WarmResult(
                capability="llm.model",
                provider_id="prov-serve",
                lease_id=None,
                volume_id="volume-cache",
                datacenter=None,
                model_id="org/model",
                variant="fp8",
                engine="vllm",
                gpu_class="NVIDIA A40",
                gpu_count=1,
                price_source="live",
                seconds_to_ready=None,
                torn_down=False,
                cost_estimate_usd="0.01",
                dry_run=True,
            ).to_dict()
        )
        out.emit()
        return 0

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(cli_warm_volume, "_warm_volume_async", fake_warm)

    assert (
        cli_warm_volume.cmd_warm_volume(
            ["--model", "org/model", "--volume-id", "volume-cache", "--dry-run", "--json"]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["gpu_class"] == "NVIDIA A40"
    assert payload["gpu_count"] == 1
    assert payload["price_source"] == "live"


def test_terminate_pod_verify_get_pod_exception(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    call_count = [0]

    def fake_get_pod(pod_id):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("pod API temporarily unavailable")
        return None

    monkeypatch.setattr(time, "sleep", lambda s: None)
    with (
        _termination_patch(),
        patch("pitwall.cli.pods.get_pod_sync", side_effect=fake_get_pod),
    ):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123", "--verify-timeout-s", "0.1"])
    out = capsys.readouterr().out
    err = capsys.readouterr().err
    assert rc == 0
    assert "no longer returned by RunPod" in out or "WARN" in err or "OK" in out


def test_terminate_pod_verify_timeout_returns_manual_verification(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    with (
        _termination_patch(),
        patch("pitwall.cli.pods.get_pod_sync", return_value={"desiredStatus": "RUNNING"}),
    ):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123", "--verify-timeout-s", "0.0"])
    out = capsys.readouterr().out
    err = capsys.readouterr().err
    assert rc == 1
    assert "did not reach EXITED/TERMINATED" in out or "Manual verification" in err


def test_register_template_non_dry_run_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    rc = cli_templates.cmd_register_template(["--image", "ghcr.io/org/worker:v1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "RUNPOD_API_KEY" in err


def test_terminate_pod_verify_waiting_loop_reaches_terminated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    call_count = [0]

    def fake_get_pod(pod_id):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"desiredStatus": "RUNNING"}
        return {"desiredStatus": "TERMINATED"}

    monkeypatch.setattr(time, "sleep", lambda s: None)
    with (
        _termination_patch(),
        patch("pitwall.cli.pods.get_pod_sync", side_effect=fake_get_pod),
    ):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123", "--verify-timeout-s", "15"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "desiredStatus=TERMINATED" in out


def test_terminate_pod_verify_waiting_message_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    call_count = [0]

    def fake_get_pod(pod_id):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"desiredStatus": "RUNNING"}
        return {"desiredStatus": "EXITED"}

    monkeypatch.setattr(time, "sleep", lambda s: None)
    with (
        _termination_patch(),
        patch("pitwall.cli.pods.get_pod_sync", side_effect=fake_get_pod),
    ):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123", "--verify-timeout-s", "15"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "... waiting" in out


def test_cmd_mcp_serve_calls_mcp_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    mcp_run_calls: list[str] = []
    fake_mcp = type(
        "FakeMCP",
        (),
        {"run": lambda self, **kw: mcp_run_calls.append(kw.get("transport", "stdio"))},
    )()

    def noop(*args, **kwargs) -> None:
        pass

    class FakeMcpModule:
        mcp = fake_mcp
        ensure_runtime_env = noop

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pitwall.mcp":
            return FakeMcpModule()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    rc = cli_mcp.cmd_mcp(["serve", "broker", "--transport", "stdio"])
    assert rc == 0
    assert mcp_run_calls == ["stdio"]


def test_cmd_mcp_serve_rejects_network_transport() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_mcp.cmd_mcp(["serve", "broker", "--transport", "sse"])
    assert exc_info.value.code == 2


def _make_mock_pool(fetchrow_side_effect=None) -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="OK")
    if fetchrow_side_effect:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value={"id": "tpl_123"})

    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


def _make_warm_volume_pool(cap_record=None, provider_record=None):
    def fetchrow_side_effect(sql, *args):
        if "capability" in sql.lower():
            if cap_record:
                return cap_record
            return {
                "id": "cap_123",
                "name": "llm.qwen3-32b",
                "class": "llm",
                "cost_mode": "per_second",
                "openai_compatible": True,
                "config": {},
                "version": "1.0",
                "description": None,
                "input_schema": {},
                "output_schema": {},
                "defaults": {},
                "hints_supported": [],
                "source": "api",
                "last_applied_yaml_hash": None,
                "enabled": True,
                "created_at": None,
                "updated_at": None,
            }
        if provider_record:
            return provider_record
        return {
            "id": "prov_456",
            "name": "test-prov",
            "priority": 1,
            "config": {"gpu_class": "NVIDIA H100 80GB HBM3"},
            "enabled": True,
            "health_status": "healthy",
        }

    conn = MagicMock()
    conn.execute = AsyncMock(return_value="OK")
    conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


async def _mock_existing_capability(*args, **kwargs):
    return MagicMock(id=args[-1])


@pytest.mark.anyio
async def test_register_template_async_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_template_id = "tpl_123"
    ensure_template = AsyncMock(return_value=mock_template_id)
    pool = _make_mock_pool()

    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")

    with (
        patch("pitwall.db.get_pool", new=AsyncMock(return_value=pool)),
        patch("pitwall.cli.templates.ensure_template", new=ensure_template),
    ):
        ns = cli_templates._parse_register_template_args(["--image", "ghcr.io/org/worker:v1"])
        rc = await cli_templates._register_template_async(ns)

    out = capsys.readouterr().out
    assert rc == 0
    assert "Template registered: tpl_123" in out
    ensure_template.assert_awaited_once_with(
        pool,
        "ghcr.io/org/worker:v1",
        template_name="pitwall-cloud-worker",
        registry_auth_id=None,
        container_disk_gb=50,
    )


@pytest.mark.anyio
async def test_register_endpoint_async_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def mock_upsert(*args, **kwargs):
        return MagicMock(id="cap_xyz")

    async def mock_create(*args, **kwargs):
        return MagicMock(
            id="prov_new",
            name="test-provider",
            capability_id="c1",
            provider_type=MagicMock(value="serverless_queue"),
            runpod_endpoint_id="e1",
            region=None,
            priority=0,
        )

    async def mock_get_by_name(*args, **kwargs):
        return None

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")

    def make_endpoint_pool():
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="OK")

        def fetchrow_side_effect(sql, *args):
            if "capability_name" in sql:
                return None
            return {
                "id": "prov_new",
                "name": "test-provider",
                "capability_id": "c1",
                "provider_type": "serverless_queue",
                "runpod_endpoint_id": "e1",
                "region": None,
                "priority": 0,
                "config": {},
                "enabled": True,
                "health_status": "unknown",
                "consecutive_failures": 0,
                "cooldown_trips": 0,
                "cold_start_p50_ms": None,
                "cold_start_p95_ms": None,
                "recent_error_rate": 0.0,
                "cooldown_until": None,
                "source": "api",
                "last_applied_yaml_hash": None,
                "updated_at": None,
            }

        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acquire_ctx)
        return pool

    with (
        patch("pitwall.db.get_pool", new=AsyncMock(return_value=make_endpoint_pool())),
        patch("pitwall.db.repository.CapabilityRepository.get", new=_mock_existing_capability),
        patch("pitwall.db.repository.CapabilityRepository.upsert", new=mock_upsert),
        patch("pitwall.db.repository.ProviderRepository.get_by_name", new=mock_get_by_name),
        patch("pitwall.db.repository.ProviderRepository.create", new=mock_create),
    ):
        ns = cli_endpoints._parse_register_endpoint_args(
            [
                "--endpoint-id",
                "e1",
                "--provider-type",
                "serverless_queue",
                "--capability-id",
                "c1",
                "--name",
                "test-provider",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
            ]
        )
        rc = await cli_endpoints._register_endpoint_async(ns)

    out = capsys.readouterr().out
    assert rc == 0
    assert "Provider registered: prov_new" in out


def test_register_template_dry_run_includes_sha_and_display_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_templates.cmd_register_template(
        ["--image", "ghcr.io/org/worker:v1", "--template-name", "my-template", "--dry-run"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    expected_name = cli_templates.templates.template_display_name(
        "my-template",
        "ghcr.io/org/worker:v1",
        env_keys=cli_templates.templates.non_secret_env_keys(
            cli_templates.templates.TEMPLATE_ENV_KEYS
        ),
    )
    assert f"Template previewed: {expected_name}" in out


def test_terminate_pod_verify_get_pod_exception_continues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    call_count = [0]

    def fake_get_pod(pod_id):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("pod API temporarily unavailable")
        return None

    monkeypatch.setattr(time, "sleep", lambda s: None)
    with (
        _termination_patch(),
        patch("pitwall.cli.pods.get_pod_sync", side_effect=fake_get_pod),
    ):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123", "--verify-timeout-s", "0.1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no longer returned by RunPod" in out


def test_terminate_pod_verify_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    with (
        _termination_patch(),
        patch("pitwall.cli.pods.get_pod_sync", return_value={"desiredStatus": "RUNNING"}),
    ):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123", "--verify-timeout-s", "0.0"])
    out = capsys.readouterr().out
    err = capsys.readouterr().err
    assert rc == 1
    assert "did not reach EXITED/TERMINATED" in out or "Manual verification" in err


def test_parse_register_template_args_custom_container_disk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = cli_templates._parse_register_template_args(
        ["--image", "img:v1", "--container-disk-gb", "100"]
    )
    assert ns.container_disk_gb == 100


def test_parse_register_template_args_custom_name(capsys: pytest.CaptureFixture[str]) -> None:
    ns = cli_templates._parse_register_template_args(
        ["--image", "img:v1", "--template-name", "my-custom-template"]
    )
    assert ns.template_name == "my-custom-template"


def test_register_endpoint_async_with_capability_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def mock_upsert(*args, **kwargs):
        return MagicMock(id="cap_xyz")

    async def mock_create(*args, **kwargs):
        return MagicMock(
            id="prov_new",
            name="my-provider",
            capability_id="c1",
            provider_type=MagicMock(value="serverless_queue"),
            runpod_endpoint_id="e1",
            region=None,
            priority=0,
        )

    async def mock_get_by_name(*args, **kwargs):
        return None

    def make_pool():
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="OK")
        conn.fetchrow = AsyncMock(
            side_effect=[
                None,
                {
                    "id": "prov_new",
                    "name": "my-provider",
                    "capability_id": "c1",
                    "provider_type": "serverless_queue",
                    "runpod_endpoint_id": "e1",
                    "region": None,
                    "priority": 0,
                    "config": {},
                    "enabled": True,
                    "health_status": "unknown",
                    "consecutive_failures": 0,
                    "cooldown_trips": 0,
                    "cold_start_p50_ms": None,
                    "cold_start_p95_ms": None,
                    "recent_error_rate": 0.0,
                    "cooldown_until": None,
                    "source": "api",
                    "last_applied_yaml_hash": None,
                    "updated_at": None,
                },
            ]
        )
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acquire_ctx)
        return pool

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")

    with (
        patch("pitwall.db.get_pool", new=AsyncMock(return_value=make_pool())),
        patch("pitwall.db.repository.CapabilityRepository.upsert", new=mock_upsert),
        patch("pitwall.db.repository.ProviderRepository.get_by_name", new=mock_get_by_name),
        patch("pitwall.db.repository.ProviderRepository.create", new=mock_create),
    ):
        cli_endpoints._parse_register_endpoint_args(
            [
                "--endpoint-id",
                "e1",
                "--provider-type",
                "serverless_queue",
                "--capability-id",
                "c1",
                "--name",
                "my-provider",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
                "--capability-name",
                "llm.qwen3-32b",
            ]
        )
        rc = cli_endpoints.cmd_register_endpoint(
            [
                "--endpoint-id",
                "e1",
                "--provider-type",
                "serverless_queue",
                "--capability-id",
                "c1",
                "--name",
                "my-provider",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
                "--capability-name",
                "llm.qwen3-32b",
            ]
        )

    assert rc == 0


def test_register_endpoint_async_provider_already_exists(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def mock_get_by_name(*args, **kwargs):
        return MagicMock(id="prov_existing", name="my-provider")

    def make_pool():
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="OK")
        conn.fetchrow = AsyncMock(
            return_value={
                "id": "prov_existing",
                "name": "my-provider",
                "capability_id": "c1",
                "provider_type": "serverless_queue",
                "runpod_endpoint_id": "e1",
                "region": None,
                "priority": 0,
                "config": {},
                "enabled": True,
                "health_status": "unknown",
                "consecutive_failures": 0,
                "cooldown_trips": 0,
                "cold_start_p50_ms": None,
                "cold_start_p95_ms": None,
                "recent_error_rate": 0.0,
                "cooldown_until": None,
                "source": "api",
                "last_applied_yaml_hash": None,
                "updated_at": None,
            }
        )
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acquire_ctx)
        return pool

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")

    with (
        patch("pitwall.db.get_pool", new=AsyncMock(return_value=make_pool())),
        patch("pitwall.db.repository.ProviderRepository.get_by_name", new=mock_get_by_name),
    ):
        rc = cli_endpoints.cmd_register_endpoint(
            [
                "--endpoint-id",
                "e1",
                "--provider-type",
                "serverless_queue",
                "--capability-id",
                "c1",
                "--name",
                "my-provider",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
            ]
        )

    err = capsys.readouterr().err
    assert rc == 1
    assert "already exists" in err


@pytest.mark.anyio
async def test_register_endpoint_missing_capability_returns_friendly_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def mock_capability_get(*args, **kwargs):
        return None

    async def mock_provider_get_by_name(*args, **kwargs):
        return None

    mock_create = AsyncMock(
        return_value=MagicMock(
            id="prov_new",
            name="my-provider",
            capability_id="cap_missing",
            provider_type=MagicMock(value="serverless_queue"),
            runpod_endpoint_id="e1",
            region=None,
            priority=0,
        )
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")

    with (
        patch("pitwall.db.get_pool", new=AsyncMock(return_value=MagicMock())),
        patch("pitwall.db.repository.CapabilityRepository.get", new=mock_capability_get),
        patch(
            "pitwall.db.repository.ProviderRepository.get_by_name", new=mock_provider_get_by_name
        ),
        patch("pitwall.db.repository.ProviderRepository.create", new=mock_create),
    ):
        ns = cli_endpoints._parse_register_endpoint_args(
            [
                "--endpoint-id",
                "e1",
                "--provider-type",
                "serverless_queue",
                "--capability-id",
                "cap_missing",
                "--name",
                "my-provider",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
            ]
        )
        rc = await cli_endpoints._register_endpoint_async(ns)

    err = capsys.readouterr().err
    assert rc == 1
    assert "capability 'cap_missing' does not exist" in err
    assert "create it first" in err
    mock_create.assert_not_awaited()


@pytest.mark.anyio
async def test_register_endpoint_then_mark_healthy_makes_provider_routable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import datetime as dt

    from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode
    from pitwall.core.models import Capability, Provider
    from pitwall.resolver.exceptions import NoHealthyProviderError
    from pitwall.resolver.service import select_stage12_provider
    from pitwall.routing import RoutingRequest

    now = dt.datetime(2026, 5, 31, 12, 0, 0, tzinfo=dt.UTC)
    capability = Capability(
        id="cap_routable",
        name="embedding.routable",
        version="1.0.0",
        class_=CapabilityClass.EMBEDDING,
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        created_at=now,
        updated_at=now,
    )
    created_provider: Provider | None = None
    patched_provider: Provider | None = None

    async def mock_capability_get(*args, **kwargs):
        capability_id = args[-1]
        return capability if capability_id == capability.id else None

    async def mock_provider_get_by_name(*args, **kwargs):
        return None

    async def mock_provider_create(*args, **kwargs):
        nonlocal created_provider
        created_provider = args[-1]
        return created_provider

    async def mock_provider_get(*args, **kwargs):
        provider_id = args[-1]
        if created_provider is not None and provider_id == created_provider.id:
            return created_provider
        return None

    async def mock_provider_patch(*args, **kwargs):
        nonlocal patched_provider
        assert created_provider is not None
        patched_provider = created_provider.model_copy(
            update={
                "health_status": kwargs["health_status"],
                "consecutive_failures": kwargs["consecutive_failures"],
                "cooldown_trips": kwargs["cooldown_trips"],
                "recent_error_rate": kwargs["recent_error_rate"],
                "cooldown_until": kwargs["cooldown_until"],
            }
        )
        return patched_provider

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")

    with (
        patch("pitwall.db.get_pool", new=AsyncMock(return_value=MagicMock())),
        patch("pitwall.db.repository.CapabilityRepository.get", new=mock_capability_get),
        patch(
            "pitwall.db.repository.ProviderRepository.get_by_name", new=mock_provider_get_by_name
        ),
        patch("pitwall.db.repository.ProviderRepository.create", new=mock_provider_create),
    ):
        ns = cli_endpoints._parse_register_endpoint_args(
            [
                "--endpoint-id",
                "e1",
                "--provider-type",
                "serverless_queue",
                "--capability-id",
                capability.id,
                "--name",
                "my-provider",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
            ]
        )
        rc = await cli_endpoints._register_endpoint_async(ns)

    assert rc == 0
    assert created_provider is not None
    assert created_provider.health_status == "unknown"
    request = RoutingRequest(capability_name=capability.name, capability_id=capability.id)
    with pytest.raises(NoHealthyProviderError):
        select_stage12_provider(request, [created_provider], capability=capability, now=now)

    with (
        patch("pitwall.db.get_pool", new=AsyncMock(return_value=MagicMock())),
        patch("pitwall.db.repository.ProviderRepository.get", new=mock_provider_get),
        patch("pitwall.db.repository.ProviderRepository.patch", new=mock_provider_patch),
    ):
        health_args = cli_endpoints._parse_set_provider_health_args(
            [created_provider.id, "healthy"]
        )
        health_rc = await cli_endpoints._set_provider_health_async(health_args)

    out = capsys.readouterr().out
    assert health_rc == 0
    assert "health_status: healthy" in out
    assert patched_provider is not None
    resolution = select_stage12_provider(
        request,
        [patched_provider],
        capability=capability,
        now=now,
    )
    assert resolution.provider.id == created_provider.id


def test_register_endpoint_async_with_cost_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def mock_create(*args, **kwargs):
        return MagicMock(
            id="prov_new",
            name="my-provider",
            capability_id="c1",
            provider_type=MagicMock(value="serverless_queue"),
            runpod_endpoint_id="e1",
            region=None,
            priority=0,
        )

    async def mock_get_by_name(*args, **kwargs):
        return None

    def make_pool():
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="OK")
        conn.fetchrow = AsyncMock(
            side_effect=[
                None,
                {
                    "id": "prov_new",
                    "name": "my-provider",
                    "capability_id": "c1",
                    "provider_type": "serverless_queue",
                    "runpod_endpoint_id": "e1",
                    "region": None,
                    "priority": 0,
                    "config": {},
                    "enabled": True,
                    "health_status": "unknown",
                    "consecutive_failures": 0,
                    "cooldown_trips": 0,
                    "cold_start_p50_ms": None,
                    "cold_start_p95_ms": None,
                    "recent_error_rate": 0.0,
                    "cooldown_until": None,
                    "source": "api",
                    "last_applied_yaml_hash": None,
                    "updated_at": None,
                },
            ]
        )
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acquire_ctx)
        return pool

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")

    with (
        patch("pitwall.db.get_pool", new=AsyncMock(return_value=make_pool())),
        patch("pitwall.db.repository.CapabilityRepository.get", new=_mock_existing_capability),
        patch("pitwall.db.repository.ProviderRepository.get_by_name", new=mock_get_by_name),
        patch("pitwall.db.repository.ProviderRepository.create", new=mock_create),
    ):
        rc = cli_endpoints.cmd_register_endpoint(
            [
                "--endpoint-id",
                "e1",
                "--provider-type",
                "serverless_queue",
                "--capability-id",
                "c1",
                "--name",
                "my-provider",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
                "--cost-mode",
                "per_second",
                "--per-second-active",
                "0.0001",
                "--per-request",
                "0.002",
                "--per-million-input-tokens",
                "0.5",
                "--per-million-output-tokens",
                "1.5",
                "--workers-min",
                "1",
                "--workers-max",
                "10",
            ]
        )

    assert rc == 0


def test_register_endpoint_async_serverless_lb(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.core.enums import ProviderType

    class FakeProviderResult:
        def __init__(self, provider_type_val):
            self.provider_type = provider_type_val
            self.id = "prov_new"
            self.name = "my-lb-provider"
            self.capability_id = "c1"
            self.runpod_endpoint_id = "e1"
            self.region = "US-KS-2"
            self.priority = 0
            self.health_status = "unknown"

    async def mock_create(*args, **kwargs):
        return FakeProviderResult(provider_type_val=ProviderType.SERVERLESS_LB)

    async def mock_get_by_name(*args, **kwargs):
        return None

    def make_pool():
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="OK")
        conn.fetchrow = AsyncMock(
            side_effect=[
                None,
                {
                    "id": "prov_new",
                    "name": "my-lb-provider",
                    "capability_id": "c1",
                    "provider_type": "serverless_lb",
                    "runpod_endpoint_id": "e1",
                    "region": "US-KS-2",
                    "priority": 0,
                    "config": {},
                    "enabled": True,
                    "health_status": "unknown",
                    "consecutive_failures": 0,
                    "cooldown_trips": 0,
                    "cold_start_p50_ms": None,
                    "cold_start_p95_ms": None,
                    "recent_error_rate": 0.0,
                    "cooldown_until": None,
                    "source": "api",
                    "last_applied_yaml_hash": None,
                    "updated_at": None,
                },
            ]
        )
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acquire_ctx)
        return pool

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")

    with (
        patch("pitwall.db.get_pool", new=AsyncMock(return_value=make_pool())),
        patch("pitwall.db.repository.CapabilityRepository.get", new=_mock_existing_capability),
        patch("pitwall.db.repository.ProviderRepository.get_by_name", new=mock_get_by_name),
        patch("pitwall.db.repository.ProviderRepository.create", new=mock_create),
    ):
        rc = cli_endpoints.cmd_register_endpoint(
            [
                "--endpoint-id",
                "e1",
                "--provider-type",
                "serverless_lb",
                "--capability-id",
                "c1",
                "--name",
                "my-lb-provider",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
                "--region",
                "US-KS-2",
            ]
        )

    out = capsys.readouterr().out
    assert rc == 0
    assert "Provider registered: prov_new" in out


def test_register_endpoint_async_public_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.core.enums import ProviderType

    class FakeProviderResult:
        def __init__(self, provider_type_val):
            self.provider_type = provider_type_val
            self.id = "prov_pub"
            self.name = "my-pub-provider"
            self.capability_id = "c1"
            self.runpod_endpoint_id = "pub1"
            self.region = "US-KS-2"
            self.priority = 0
            self.health_status = "unknown"

    async def mock_create(*args, **kwargs):
        return FakeProviderResult(provider_type_val=ProviderType.PUBLIC_ENDPOINT)

    async def mock_get_by_name(*args, **kwargs):
        return None

    def make_pool():
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="OK")
        conn.fetchrow = AsyncMock(
            side_effect=[
                None,
                {
                    "id": "prov_pub",
                    "name": "my-pub-provider",
                    "capability_id": "c1",
                    "provider_type": "public_endpoint",
                    "runpod_endpoint_id": "pub1",
                    "region": "US-KS-2",
                    "priority": 0,
                    "config": {},
                    "enabled": True,
                    "health_status": "unknown",
                    "consecutive_failures": 0,
                    "cooldown_trips": 0,
                    "cold_start_p50_ms": None,
                    "cold_start_p95_ms": None,
                    "recent_error_rate": 0.0,
                    "cooldown_until": None,
                    "source": "api",
                    "last_applied_yaml_hash": None,
                    "updated_at": None,
                },
            ]
        )
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acquire_ctx)
        return pool

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")

    with (
        patch("pitwall.db.get_pool", new=AsyncMock(return_value=make_pool())),
        patch("pitwall.db.repository.CapabilityRepository.get", new=_mock_existing_capability),
        patch("pitwall.db.repository.ProviderRepository.get_by_name", new=mock_get_by_name),
        patch("pitwall.db.repository.ProviderRepository.create", new=mock_create),
    ):
        rc = cli_endpoints.cmd_register_endpoint(
            [
                "--endpoint-id",
                "pub1",
                "--provider-type",
                "public_endpoint",
                "--capability-id",
                "c1",
                "--name",
                "my-pub-provider",
                "--gpu-class",
                "NVIDIA H100 80GB HBM3",
                "--region",
                "US-KS-2",
            ]
        )

    out = capsys.readouterr().out
    assert rc == 0
    assert "Provider registered: prov_pub" in out


class TestJsonFlag:
    """Tests for ``--json`` output on CLI commands."""

    def test_register_template_dry_run_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        rc = cli_templates.cmd_register_template(
            ["--image", "ghcr.io/org/worker:v1", "--dry-run", "--json"]
        )
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["operation"] == "template.create"
        assert data["resource_type"] == "template"
        assert data["dry_run"] is True
        assert data["changed"] is False

    def test_terminate_pod_missing_key_json(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import json

        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_x", "--json"])
        out = capsys.readouterr().out
        assert rc == 1
        data = json.loads(out)
        assert "error" in data

    def test_config_check_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        rc = cli_config_check.cmd_config(["check", "api", "--json"])
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["service"] == "api"
        assert data["status"] == "ok"


def test_terminate_pod_verify_does_not_call_an_unreachable_runpod_gone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setattr(cli_pods, "_TERMINATE_VERIFY_INTERVAL_S", 0)
    with (
        _termination_patch(),
        patch(
            "pitwall.runpod_client.pods._rest_request",
            side_effect=ConnectionError("connection refused"),
        ),
    ):
        rc = cli_pods.cmd_terminate_pod(["--pod-id", "pod_123", "--verify-timeout-s", "0.05"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no longer returned by RunPod" not in captured.out
    assert "verification temporarily unavailable" in captured.err
