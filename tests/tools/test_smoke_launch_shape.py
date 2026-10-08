from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pitwall.models.catalogue import load_catalogue
from pitwall.serve import launch_shape
from tools.engines.smoke_launch_shape import (
    LLAMA_CPU_IMAGE,
    CommandResult,
    ProbeResult,
    VariantCase,
    _parse_argv,
    build_catalogue_cases,
    build_parse_cases,
    classify_parse_result,
    docker_argv,
    llama_parse_argv,
    main,
    render_report,
    run_parse_probes,
    verify_llama_http,
)


def test_build_catalogue_cases_calls_launch_shape_for_every_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_launch_shape(
        engine: str,
        *,
        model: str,
        served: str,
        gpu_count: int,
        repo: str | None,
        file: str | None,
        flags: tuple[str, ...],
        companion_flags: tuple[str, ...],
        start_args: tuple[str, ...],
    ) -> list[str]:
        del gpu_count, repo, file, flags, companion_flags, start_args
        calls.append((engine, model, served))
        return [engine, model, served]

    monkeypatch.setattr(
        "tools.engines.smoke_launch_shape.launch_shape",
        fake_launch_shape,
    )
    catalogue = load_catalogue(Path("docs/models"))

    cases = build_catalogue_cases(catalogue)

    assert len(cases) == sum(len(dossier.variants) for dossier in catalogue.models())
    assert len(calls) == len(cases)
    assert {case.engine for case in cases} == {"vllm", "llama.cpp", "sglang"}
    assert all(case.argv for case in cases)


def test_minimax_omni_omits_task_type_rejected_by_pinned_image() -> None:
    cases = build_catalogue_cases(load_catalogue(Path("docs/models")))
    case = next(item for item in cases if item.model_id == "MiniMaxAI/MiniMax-H3")

    assert "--omni" in case.argv
    assert "--task-type" not in case.argv


def test_parse_cases_include_every_shipped_variant_and_sglang() -> None:
    catalogue = load_catalogue(Path("docs/models"))
    cases = build_parse_cases(catalogue)

    shipped = {
        (dossier.model_id, variant.id)
        for dossier in catalogue.models()
        for variant in dossier.variants
    }
    parsed = {(case.model_id, case.variant_id) for case in cases}
    assert shipped < parsed
    assert parsed - shipped == {("smoke/sglang-parser", "synthetic-parser-probe")}


@pytest.mark.parametrize(
    ("output", "returncode", "expected", "reason"),
    [
        (
            "vllm: error: unrecognized arguments: --bad-flag",
            2,
            "SHAPE_DEFECT",
            "vllm: error: unrecognized arguments: --bad-flag",
        ),
        (
            "argument --tool-call-parser: invalid choice: 'missing'",
            2,
            "SHAPE_DEFECT",
            "argument --tool-call-parser: invalid choice: 'missing'",
        ),
        (
            "INFO: disabling Triton to prevent runtime errors.\n"
            "vllm serve: error: argument --limit-mm-per-prompt: Value image=4 cannot be converted",
            2,
            "SHAPE_DEFECT",
            "vllm serve: error: argument --limit-mm-per-prompt: Value image=4 cannot be converted",
        ),
        (
            "RuntimeError: No CUDA GPUs are available",
            1,
            "UNCLEAR",
            "RuntimeError: No CUDA GPUs are available",
        ),
        (
            "CUDA driver initialization failed",
            1,
            "UNCLEAR",
            "CUDA driver initialization failed",
        ),
        ("usage: vllm serve", 0, "OK", "arguments accepted"),
        ("unexpected traceback", 1, "UNCLEAR", "unexpected traceback"),
    ],
)
def test_classify_parse_result(
    output: str,
    returncode: int,
    expected: str,
    reason: str,
) -> None:
    classification, detail = classify_parse_result(
        CommandResult(argv=("engine",), returncode=returncode, output=output, elapsed_s=1.25)
    )

    assert classification == expected
    assert detail == reason


def test_docker_argv_uses_pinned_image_and_engine_entrypoint() -> None:
    vllm = VariantCase(
        model_id="org/model",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:tag",
        used_image="vllm/vllm-openai@sha256:abc",
        served_model_name="served",
        argv=("org/model", "--port", "8000"),
    )
    sglang = VariantCase(
        model_id="smoke/sglang",
        variant_id="synthetic-parser-probe",
        engine="sglang",
        declared_image="lmsysorg/sglang:v0.5.18-runtime",
        used_image="lmsysorg/sglang@sha256:def",
        served_model_name="served",
        argv=("python3", "-m", "sglang.launch_server", "--model-path", "org/model"),
    )

    assert docker_argv(vllm) == (
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "vllm/vllm-openai@sha256:abc",
        "org/model",
        "--port",
        "8000",
    )
    assert docker_argv(sglang) == (
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "lmsysorg/sglang@sha256:def",
        "python3",
        "-m",
        "sglang.launch_server",
        "--model-path",
        "org/model",
    )


def test_vllm_parser_probe_uses_real_parser_without_help() -> None:
    case = VariantCase(
        model_id="org/model",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:tag",
        used_image="vllm/vllm-openai@sha256:abc",
        served_model_name="served",
        argv=("org/model", "--port", "8000"),
    )

    command = _parse_argv(case)

    assert "--help" not in command
    assert command[:10] == (
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--entrypoint",
        "python3",
        "vllm/vllm-openai@sha256:abc",
    )
    assert "parse_args" in command[11]


def test_negative_control_is_run_and_must_reject_bogus_flag() -> None:
    case = VariantCase(
        model_id="org/model",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:tag",
        used_image="vllm/vllm-openai@sha256:abc",
        served_model_name="served",
        argv=("org/model", "--port", "8000"),
    )
    commands: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], timeout_s: float) -> CommandResult:
        del timeout_s
        commands.append(argv)
        output = "error: unrecognized arguments: --pitwall-bogus-flag-7"
        return CommandResult(
            argv=argv,
            returncode=2 if "--pitwall-bogus-flag-7" in " ".join(argv) else 0,
            output=output if "--pitwall-bogus-flag-7" in " ".join(argv) else "",
            elapsed_s=0.1,
        )

    results = run_parse_probes([case], runner=runner)

    assert len(commands) == 2
    assert any("--pitwall-bogus-flag-7" in " ".join(command) for command in commands)
    assert [result.classification for result in results] == ["OK_SUBSTITUTED", "SHAPE_DEFECT"]


def test_substitute_image_never_reports_plain_ok() -> None:
    case = VariantCase(
        model_id="org/model",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:tag",
        used_image="vllm/vllm-openai@sha256:abc",
        served_model_name="served",
        argv=("org/model", "--port", "8000"),
    )

    def runner(argv: tuple[str, ...], timeout_s: float) -> CommandResult:
        del timeout_s
        is_control = "--pitwall-bogus-flag-7" in " ".join(argv)
        return CommandResult(
            argv=argv,
            returncode=2 if is_control else 0,
            output="unrecognized arguments: --pitwall-bogus-flag-7" if is_control else "",
            elapsed_s=0.1,
        )

    results = run_parse_probes([case], runner=runner)

    assert results[0].classification == "OK_SUBSTITUTED"


def test_invalid_negative_control_marks_engine_rows_unproven() -> None:
    case = VariantCase(
        model_id="org/model",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:tag",
        used_image="vllm/vllm-openai@sha256:abc",
        served_model_name="served",
        argv=("org/model", "--port", "8000"),
    )

    def runner(argv: tuple[str, ...], timeout_s: float) -> CommandResult:
        del timeout_s
        return CommandResult(argv=argv, returncode=0, output="", elapsed_s=0.1)

    results = run_parse_probes([case], runner=runner)

    assert all(result.classification == "PROBE_INVALID" for result in results)


def test_invalid_omni_control_marks_only_the_omni_lane_unproven() -> None:
    omni = VariantCase(
        model_id="org/omni",
        variant_id="bf16",
        engine="vllm",
        declared_image="vllm/vllm-omni:tag",
        used_image="vllm/vllm-omni@sha256:abc",
        served_model_name="omni",
        argv=("vllm-omni", "serve", "org/omni", "--omni"),
    )
    regular = VariantCase(
        model_id="org/model",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:tag",
        used_image="vllm/vllm-openai@sha256:def",
        served_model_name="served",
        argv=("org/model", "--port", "8000"),
    )

    def runner(argv: tuple[str, ...], timeout_s: float) -> CommandResult:
        del timeout_s
        joined = " ".join(argv)
        is_control = "--pitwall-bogus-flag-7" in joined
        omni_control = is_control and "vllm-omni@sha256:abc" in joined
        return CommandResult(
            argv=argv,
            returncode=0 if not is_control or omni_control else 2,
            output=(
                ""
                if not is_control or omni_control
                else "unrecognized arguments: --pitwall-bogus-flag-7"
            ),
            elapsed_s=0.1,
        )

    results = run_parse_probes([omni, regular], runner=runner)

    # vllm-omni is its own parser lane: its failed control must not taint plain vLLM rows.
    assert [result.classification for result in results[:2]] == [
        "PROBE_INVALID",
        "OK_SUBSTITUTED",
    ]


def test_negative_controls_are_scoped_to_each_lane_image() -> None:
    first = VariantCase(
        model_id="org/first",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:first",
        used_image="vllm/vllm-openai@sha256:first",
        served_model_name="first",
        argv=("org/first",),
    )
    second = VariantCase(
        model_id="org/second",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:second",
        used_image="vllm/vllm-openai@sha256:second",
        served_model_name="second",
        argv=("org/second",),
    )

    def runner(argv: tuple[str, ...], timeout_s: float) -> CommandResult:
        del timeout_s
        control = "--pitwall-bogus-flag-7" in " ".join(argv)
        second_image = "vllm/vllm-openai@sha256:second" in argv
        return CommandResult(
            argv=argv,
            returncode=2 if control else 0,
            output=(
                "error: unrecognized arguments: --pitwall-bogus-flag-7"
                if control and second_image
                else ""
            ),
            elapsed_s=0.1,
        )

    results = run_parse_probes([first, second], runner=runner)
    report = render_report(results)
    controls = json.loads(report.json_text)["negative_controls"]

    assert [result.classification for result in results[:2]] == [
        "PROBE_INVALID",
        "OK_SUBSTITUTED",
    ]
    assert len(controls) == 2
    assert {control["used_image"] for control in controls} == {
        first.used_image,
        second.used_image,
    }


def test_timeout_is_unclear_before_llama_model_load_failure() -> None:
    classification, detail = classify_parse_result(
        CommandResult(
            argv=("llama-server",),
            returncode=124,
            output="failed to load model\nengine probe timed out",
            elapsed_s=45.0,
        ),
        engine="llama.cpp",
    )

    assert (classification, detail) == ("UNCLEAR", "engine probe timed out")


def test_bare_exit_two_control_is_probe_invalid() -> None:
    case = VariantCase(
        model_id="org/model",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:tag",
        used_image="vllm/vllm-openai@sha256:abc",
        served_model_name="served",
        argv=("org/model",),
    )

    def runner(argv: tuple[str, ...], timeout_s: float) -> CommandResult:
        del timeout_s
        control = "--pitwall-bogus-flag-7" in " ".join(argv)
        return CommandResult(argv=argv, returncode=2 if control else 0, output="", elapsed_s=0.1)

    results = run_parse_probes([case], runner=runner)

    assert [result.classification for result in results] == ["PROBE_INVALID", "PROBE_INVALID"]
    assert (
        results[-1].detail == "control exited 2 without a parser diagnostic naming the bogus flag"
    )


def test_non_llama_bare_exit_two_is_unclear() -> None:
    classification, detail = classify_parse_result(
        CommandResult(argv=("vllm",), returncode=2, output="", elapsed_s=0.1), engine="vllm"
    )

    assert (classification, detail) == ("UNCLEAR", "engine produced no output")


@pytest.mark.parametrize(
    ("allow_substitution", "expected"),
    [(False, 3), (True, 0)],
)
def test_main_exit_code_for_substitute_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    allow_substitution: bool,
    expected: int,
) -> None:
    result = ProbeResult(
        model_id="org/model",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:tag",
        used_image="vllm/vllm-openai@sha256:abc",
        classification="OK_SUBSTITUTED",
        detail="arguments accepted",
        argv=("org/model",),
        elapsed_s=0.1,
    )
    argv = ["smoke_launch_shape.py", "--parse-only", "--report", str(tmp_path / "r.md")]
    if allow_substitution:
        argv.append("--allow-image-substitution")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr("tools.engines.smoke_launch_shape.load_catalogue", lambda path: object())
    monkeypatch.setattr(
        "tools.engines.smoke_launch_shape.build_parse_cases",
        lambda catalogue, use_declared_images: [],
    )
    monkeypatch.setattr(
        "tools.engines.smoke_launch_shape.run_parse_probes",
        lambda cases, timeout_s: [result],
    )

    assert main() == expected


def test_llama_cpu_image_is_pinned_to_verified_server_tag_digest() -> None:
    assert LLAMA_CPU_IMAGE == (
        "ghcr.io/ggml-org/llama.cpp"
        "@sha256:9f84380be42d6285a827629c809387349c3541aa8986f7536547ca33cc8dd47a"
    )


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("error: invalid argument: --max-model-len", "SHAPE_DEFECT"),
        ("error loading model: failed to load model", "OK_SUBSTITUTED"),
    ],
)
def test_llama_parse_probe_classifies_flags_after_replacing_hf_pair(
    output: str,
    expected: str,
) -> None:
    case = VariantCase(
        model_id="org/model",
        variant_id="gguf",
        engine="llama.cpp",
        declared_image="ghcr.io/ggml-org/llama.cpp:server-cuda",
        used_image=LLAMA_CPU_IMAGE,
        served_model_name="served",
        argv=(
            "--hf-repo",
            "org/model",
            "--hf-file",
            "model.gguf",
            "--max-model-len",
            "32768",
        ),
    )
    commands: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], timeout_s: float) -> CommandResult:
        del timeout_s
        commands.append(argv)
        is_control = "--pitwall-bogus-flag-7" in argv
        return CommandResult(
            argv=argv,
            returncode=1,
            output="error: invalid argument: --pitwall-bogus-flag-7" if is_control else output,
            elapsed_s=0.1,
        )

    result = run_parse_probes([case], runner=runner)

    assert result[0].classification == expected
    assert commands[0] == llama_parse_argv(case)
    assert commands[0] == (
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        LLAMA_CPU_IMAGE,
        "--max-model-len",
        "32768",
        "-m",
        "/models/missing.gguf",
    )


def test_vllm_omni_shape_supplies_missing_image_entrypoint() -> None:
    argv = launch_shape(
        "vllm",
        model="MiniMaxAI/MiniMax-H3",
        served="MiniMax-H3",
        gpu_count=2,
        repo="MiniMaxAI/MiniMax-H3",
        file=None,
        flags=("--omni", "--task-type", "fl2va"),
        companion_flags=(),
        start_args=(),
    )

    assert argv[:3] == ["vllm-omni", "serve", "MiniMaxAI/MiniMax-H3"]
    assert argv[-3:] == ["--omni", "--task-type", "fl2va"]


def test_render_report_has_stable_json_shape_and_classification_table() -> None:
    result = ProbeResult(
        model_id="org/model",
        variant_id="fp8",
        engine="vllm",
        declared_image="vllm/vllm-openai:tag",
        used_image="vllm/vllm-openai@sha256:abc",
        classification="OK",
        detail="expected device failure after parsing",
        argv=("org/model", "--port", "8000"),
        elapsed_s=1.5,
    )

    report = render_report([result])
    payload = json.loads(report.json_text)

    assert payload == {
        "negative_controls": [],
        "schema_version": 2,
        "summary": {
            "OK": 1,
            "OK_SUBSTITUTED": 0,
            "PROBE_INVALID": 0,
            "SHAPE_DEFECT": 0,
            "UNCLEAR": 0,
        },
        "variants": [
            {
                "argv": ["org/model", "--port", "8000"],
                "classification": "OK",
                "comparison_note": "",
                "declared_image": "vllm/vllm-openai:tag",
                "detail": "expected device failure after parsing",
                "elapsed_s": 1.5,
                "engine": "vllm",
                "model_id": "org/model",
                "negative_control": False,
                "used_image": "vllm/vllm-openai@sha256:abc",
                "variant_id": "fp8",
            }
        ],
    }
    assert "| catalogue | org/model | fp8 | vllm |" in report.table
    assert (
        "1 row parser-checked on a substitute image; declared image not executed." in report.table
    )


def test_verify_llama_http_checks_health_alias_and_chat() -> None:
    calls: list[tuple[str, str, object | None]] = []

    def request(method: str, url: str, payload: object | None) -> tuple[int, object]:
        calls.append((method, url, payload))
        if url.endswith("/health"):
            return 200, {"status": "ok"}
        if url.endswith("/v1/models"):
            return 200, {"data": [{"id": "pitwall-smoke"}]}
        return 200, {"choices": [{"message": {"content": "hello"}}]}

    verify_llama_http("http://127.0.0.1:18000", "pitwall-smoke", request=request)

    assert calls == [
        ("GET", "http://127.0.0.1:18000/health", None),
        ("GET", "http://127.0.0.1:18000/v1/models", None),
        (
            "POST",
            "http://127.0.0.1:18000/v1/chat/completions",
            {
                "model": "pitwall-smoke",
                "messages": [{"role": "user", "content": "Reply with hello."}],
                "max_tokens": 8,
                "stream": False,
            },
        ),
    ]


def test_verify_llama_http_rejects_wrong_alias() -> None:
    def request(method: str, url: str, payload: object | None) -> tuple[int, object]:
        del method, payload
        if url.endswith("/v1/models"):
            return 200, {"data": [{"id": "wrong"}]}
        return 200, {"choices": [{}]}

    with pytest.raises(RuntimeError, match="served alias"):
        verify_llama_http("http://127.0.0.1:18000", "pitwall-smoke", request=request)
