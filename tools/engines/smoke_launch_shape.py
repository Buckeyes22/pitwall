"""Run CPU-only launch-shape probes against pinned serving-engine images."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Protocol, cast

from pitwall.models.catalogue import Catalogue, load_catalogue
from pitwall.models.lookup import Engine
from pitwall.serve import launch_shape

SGLANG_IMAGE = (
    "lmsysorg/sglang@sha256:87f476c9c044b5e4e12a00f56599fe74bd9607c6921234d537232676a1669527"
)
# Resolved from ghcr.io/ggml-org/llama.cpp:server on 2026-08-28.
LLAMA_CPU_IMAGE = (
    "ghcr.io/ggml-org/llama.cpp"
    "@sha256:9f84380be42d6285a827629c809387349c3541aa8986f7536547ca33cc8dd47a"
)
PINNED_IMAGES: dict[str, str] = {
    "ghcr.io/ggml-org/llama.cpp:server-cuda": LLAMA_CPU_IMAGE,
    "ghcr.io/ggml-org/llama.cpp:server-cuda13": LLAMA_CPU_IMAGE,
    "lmsysorg/sglang:v0.5.18-runtime": SGLANG_IMAGE,
    # Resolved from Docker Hub on 2026-09-07 (open-weight Go-model batch).
    "lmsysorg/sglang:dev-cu13": (
        "lmsysorg/sglang@sha256:9a352a35c973a2357372e85f3bcb5388b6b3c46c1329165987260f3b089647dc"
    ),
    # Resolved from Docker Hub on 2026-10-07 (MiMo-V2.6 batch).
    "lmsysorg/sglang:v0.5.21-cu130": (
        "lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469"
    ),
    "vllm/vllm-openai:v0.31.0": (
        "vllm/vllm-openai@sha256:c1c9f6fd5c109ba7f0546a59f5b2f15fb87f64c77782e90a27b648b42a8e67c3"
    ),
    "vllm/vllm-omni:minimax-h3": (
        "vllm/vllm-omni@sha256:511ea7f895f4a19f61247795bd30d6bdd3f545491e09cd9a8e28f4e07babe212"
    ),
    "vllm/vllm-openai:latest": (
        "vllm/vllm-openai@sha256:2286e8533ca8b6bc777594bae30524f1426ba46ca21797524e06df6a94b06635"
    ),
    "vllm/vllm-openai:v0.25.0": (
        "vllm/vllm-openai@sha256:2286e8533ca8b6bc777594bae30524f1426ba46ca21797524e06df6a94b06635"
    ),
    "vllm/vllm-openai:gemma4": (
        "vllm/vllm-openai@sha256:2286e8533ca8b6bc777594bae30524f1426ba46ca21797524e06df6a94b06635"
    ),
    "vllm/vllm-openai:glm52": (
        "vllm/vllm-openai@sha256:2286e8533ca8b6bc777594bae30524f1426ba46ca21797524e06df6a94b06635"
    ),
    "vllm/vllm-openai:glm53-flash": (
        "vllm/vllm-openai@sha256:2e771fa615452282cc331eb418b3ef21636fce355bea0491fca89e6d362ab703"
    ),
    "vllm/vllm-openai:kimi-k3": (
        "vllm/vllm-openai@sha256:2286e8533ca8b6bc777594bae30524f1426ba46ca21797524e06df6a94b06635"
    ),
    "vllm/vllm-openai:muse-glimmer": (
        "vllm/vllm-openai@sha256:2286e8533ca8b6bc777594bae30524f1426ba46ca21797524e06df6a94b06635"
    ),
    "vllm/vllm-openai:qwen38-flash-next": (
        "vllm/vllm-openai@sha256:0aea30240f3e3d9ffae8526643950e170eb5fa07fc427016a9dd90892afa2aa3"
    ),
    "vllm/vllm-openai:v0.28.0": (
        "vllm/vllm-openai@sha256:61fc8a896b0a4fbbbdc063bc4b0dbc25ce98e02b5050c24aeb7830ac02039b14"
    ),
    "vllm/vllm-openai:glm51": (
        "vllm/vllm-openai@sha256:0218dab2d008f74410c419f22a9029687a35f8c7673d01227c913cf59900c4c5"
    ),
    "vllm/vllm-openai:minimax-m3": (
        "vllm/vllm-openai@sha256:630c39b136d07d0140da154e5186c1cd339bbd0082d91a2811081468e39beead"
    ),
    "vllm/vllm-openai:minimax27": (
        "vllm/vllm-openai@sha256:f4db9c3c336ec2deb1635b61e5e9cc9bfe8521792e8e180d04bfea0fa5f5eadf"
    ),
    "vllm/vllm-openai:deepseekv4-flash-vision": (
        "vllm/vllm-openai@sha256:0075fd82e3b6d943b0aa91e35da8dbca63d88516c607745131055e9d81f37ebb"
    ),
    "vllm/vllm-openai:hy3": (
        "vllm/vllm-openai@sha256:3fdbfaea0b49b13ec538691a54d4c7da0b46041bd06da93ff749295b72120339"
    ),
    "vllm/vllm-openai:hy4-preview": (
        "vllm/vllm-openai@sha256:dc3f5fbe373b74141605a1e7843ae305faa648e584cac73a201395cc259522f6"
    ),
    "vllm/vllm-openai:mimov25-cu129": (
        "vllm/vllm-openai@sha256:39b1392da77b36187fde12858cbd1f9e2a9a0732b804b82d871a3446b28031e6"
    ),
}
PROPOSAL_REPO = "afrideva/TinyMistral-248M-SFT-v4-GGUF"
PROPOSAL_FILE = "tinymistral-248m-sft-v4.q2_k.gguf"
PROPOSAL_SIZE = 116_198_464
PROPOSAL_URL = f"https://huggingface.co/{PROPOSAL_REPO}/resolve/main/{PROPOSAL_FILE}"
MAX_GGUF_BYTES = 200_000_000
SMOKE_ALIAS = "pitwall-smoke"

Classification = Literal[
    "OK",
    "OK_SUBSTITUTED",
    "SHAPE_DEFECT",
    "UNCLEAR",
    "PROBE_INVALID",
]
Request = Callable[[str, str, object | None], tuple[int, object]]


@dataclass(frozen=True, slots=True)
class VariantCase:
    model_id: str
    variant_id: str
    engine: Engine
    declared_image: str
    used_image: str
    served_model_name: str
    argv: tuple[str, ...]
    comparison_note: str = ""


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    output: str
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class ProbeResult:
    model_id: str
    variant_id: str
    engine: Engine
    declared_image: str
    used_image: str
    classification: Classification
    detail: str
    argv: tuple[str, ...]
    elapsed_s: float
    comparison_note: str = ""
    negative_control: bool = False


@dataclass(frozen=True, slots=True)
class RenderedReport:
    table: str
    json_text: str


@dataclass(frozen=True, slots=True)
class ImageMetric:
    image: str
    pull_s: float
    size_bytes: int


class CommandRunner(Protocol):
    def __call__(self, argv: Sequence[str], timeout_s: float) -> CommandResult: ...


def build_catalogue_cases(
    catalogue: Catalogue,
    *,
    use_declared_images: bool = False,
) -> list[VariantCase]:
    """Render the production launch shape once for every shipped variant."""

    result: list[VariantCase] = []
    for dossier in catalogue.models():
        for variant in dossier.variants:
            used_image = variant.image if use_declared_images else PINNED_IMAGES.get(variant.image)
            if used_image is None:
                raise RuntimeError(f"no immutable smoke image for {variant.image}")
            argv = launch_shape(
                variant.engine,
                model=dossier.model_id,
                served=dossier.pitwall.served_model_name,
                gpu_count=1,
                repo=variant.repo,
                file=variant.file,
                flags=variant.flags,
                companion_flags=tuple(
                    flag for companion in variant.companions for flag in companion.flags
                ),
                start_args=(),
            )
            result.append(
                VariantCase(
                    model_id=dossier.model_id,
                    variant_id=variant.id,
                    engine=variant.engine,
                    declared_image=variant.image,
                    used_image=used_image,
                    served_model_name=dossier.pitwall.served_model_name,
                    argv=tuple(argv),
                )
            )
    return result


def build_sglang_case(*, use_declared_image: bool = False) -> VariantCase:
    """Cover the stable tagged SGLang shape alongside the catalogue's rolling-tag row."""

    declared = "lmsysorg/sglang:v0.5.18-runtime"
    argv = launch_shape(
        "sglang",
        model="smoke/sglang-parser",
        served="pitwall-sglang-smoke",
        gpu_count=1,
        repo=None,
        file=None,
        flags=(),
        companion_flags=(),
        start_args=(),
    )
    return VariantCase(
        model_id="smoke/sglang-parser",
        variant_id="synthetic-parser-probe",
        engine="sglang",
        declared_image=declared,
        used_image=declared if use_declared_image else PINNED_IMAGES[declared],
        served_model_name="pitwall-sglang-smoke",
        argv=tuple(argv),
    )


def build_parse_cases(
    catalogue: Catalogue,
    *,
    use_declared_images: bool = False,
) -> list[VariantCase]:
    """Return every shipped variant plus the synthetic SGLang coverage row."""

    return [
        *build_catalogue_cases(catalogue, use_declared_images=use_declared_images),
        build_sglang_case(use_declared_image=use_declared_images),
    ]


def _first_error_line(output: str) -> str | None:
    return next(
        (line.strip() for line in output.splitlines() if "error:" in line.lower()),
        None,
    )


def classify_parse_result(
    result: CommandResult,
    *,
    engine: Engine | None = None,
    negative_control: bool = False,
) -> tuple[Classification, str]:
    if result.returncode == 124:
        return "UNCLEAR", "engine probe timed out"
    lowered = result.output.lower()
    defects = (
        (
            "invalid argument",
            "unknown argument",
            "unrecognized arguments",
            "unrecognized argument",
        )
        if engine == "llama.cpp"
        else (
            "unrecognized arguments",
            "unrecognized argument",
            "invalid choice",
            "expected one argument",
        )
    )
    diagnostic_line: str | None = None
    if any(marker in lowered for marker in defects):
        marker = next(marker for marker in defects if marker in lowered)
        diagnostic_line = next(
            (line.strip() for line in result.output.splitlines() if marker in line.lower()),
            None,
        )
    if negative_control:
        if diagnostic_line is not None and "--pitwall-bogus-flag-7" in diagnostic_line:
            return "SHAPE_DEFECT", diagnostic_line
        if result.returncode == 2:
            return (
                "PROBE_INVALID",
                "control exited 2 without a parser diagnostic naming the bogus flag",
            )
        return "PROBE_INVALID", "control did not emit a parser diagnostic naming the bogus flag"
    if diagnostic_line is not None:
        # Keep the engine's own message: it names the rejected arguments.
        return "SHAPE_DEFECT", _first_error_line(result.output) or diagnostic_line
    if engine != "llama.cpp" and result.returncode == 2:
        error_line = _first_error_line(result.output)
        if error_line is not None:
            return "SHAPE_DEFECT", error_line
    if engine == "llama.cpp":
        model_failures = ("failed to load model", "error loading model")
        if result.returncode != 0 and any(marker in lowered for marker in model_failures):
            return "OK", "expected model-load failure after parsing"
        error_line = _first_error_line(result.output)
        return "UNCLEAR", error_line or "engine produced no error output"
    if result.returncode == 0:
        return "OK", "arguments accepted"
    lines = [line.strip() for line in result.output.splitlines() if line.strip()]
    error_line = _first_error_line(result.output)
    return "UNCLEAR", error_line or (lines[0] if lines else "engine produced no output")


def docker_argv(case: VariantCase) -> tuple[str, ...]:
    return "docker", "run", "--rm", "--pull", "never", case.used_image, *case.argv


def llama_parse_argv(case: VariantCase) -> tuple[str, ...]:
    """Use a missing local GGUF to exercise llama.cpp parsing without a download."""

    argv: list[str] = []
    index = 0
    while index < len(case.argv):
        if case.argv[index] in {"--hf-repo", "--hf-file"}:
            index += 2
        else:
            argv.append(case.argv[index])
            index += 1
    argv.extend(("-m", "/models/missing.gguf"))
    return (
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        case.used_image,
        *argv,
    )


VLLM_PARSER_SCRIPT = """\
import json
import sys

from vllm import platforms
from vllm.platforms.cpu import CpuPlatform

platforms.current_platform = CpuPlatform()

from vllm.entrypoints.cli import serve
from vllm.utils.argparse_utils import FlexibleArgumentParser

parser = FlexibleArgumentParser(prog="vllm")
subparsers = parser.add_subparsers(required=True, dest="subparser")
for command in serve.cmd_init():
    command.subparser_init(subparsers)
parser.parse_args(["serve", *json.loads(sys.argv[1])])
"""

SGLANG_PARSER_SCRIPT = """\
import argparse
import json
import sys

from sglang.srt.server_args import ServerArgs

parser = argparse.ArgumentParser(prog="sglang.launch_server")
ServerArgs.add_cli_args(parser)
parser.parse_args(json.loads(sys.argv[1]))
"""

VLLM_OMNI_PARSER_SCRIPT = """\
import json
import sys

from vllm_omni.entrypoints.cli.serve import _ensure_vllm_platform, cmd_init
from vllm_omni.utils.tracking_parser import TrackingArgumentParser

_ensure_vllm_platform()
parser = TrackingArgumentParser(prog="vllm-omni")
subparsers = parser.add_subparsers(required=True, dest="subparser")
for command in cmd_init():
    command.subparser_init(subparsers)
parser.parse_args(json.loads(sys.argv[1]))
"""


def _python_parser_argv(
    case: VariantCase,
    script: str,
    parser_argv: Sequence[str],
) -> tuple[str, ...]:
    return (
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--entrypoint",
        "python3",
        case.used_image,
        "-c",
        script,
        json.dumps(parser_argv),
    )


def _parse_argv(case: VariantCase) -> tuple[str, ...]:
    if case.engine == "llama.cpp":
        return llama_parse_argv(case)
    if case.engine == "sglang":
        return _python_parser_argv(case, SGLANG_PARSER_SCRIPT, case.argv[3:])
    if case.argv[:2] == ("vllm-omni", "serve"):
        return _python_parser_argv(case, VLLM_OMNI_PARSER_SCRIPT, case.argv[1:])
    return _python_parser_argv(case, VLLM_PARSER_SCRIPT, case.argv)


def _probe_lane(case: VariantCase) -> str:
    if case.argv[:2] == ("vllm-omni", "serve"):
        return "vllm-omni"
    return case.engine


def _run_command(argv: Sequence[str], timeout_s: float) -> CommandResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s,
        )
        return CommandResult(
            argv=tuple(argv),
            returncode=completed.returncode,
            output=completed.stdout,
            elapsed_s=round(time.monotonic() - started, 3),
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return CommandResult(
            argv=tuple(argv),
            returncode=124,
            output=output + "\nengine probe timed out",
            elapsed_s=round(time.monotonic() - started, 3),
        )


def run_parse_probes(
    cases: Sequence[VariantCase],
    *,
    runner: CommandRunner = _run_command,
    timeout_s: float = 45.0,
) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    control_indexes: dict[tuple[str, str], list[int]] = defaultdict(list)
    control_cases: dict[tuple[str, str], VariantCase] = {}
    for case in cases:
        command = runner(_parse_argv(case), timeout_s)
        classification, detail = classify_parse_result(command, engine=case.engine)
        if classification == "OK" and case.used_image != case.declared_image:
            classification = "OK_SUBSTITUTED"
        lane = _probe_lane(case)
        control_key = lane, case.used_image
        control_indexes[control_key].append(len(results))
        control_cases.setdefault(control_key, case)
        results.append(
            ProbeResult(
                model_id=case.model_id,
                variant_id=case.variant_id,
                engine=case.engine,
                declared_image=case.declared_image,
                used_image=case.used_image,
                classification=classification,
                detail=detail,
                argv=case.argv,
                elapsed_s=command.elapsed_s,
                comparison_note=case.comparison_note,
            )
        )
    for (lane, _used_image), case in control_cases.items():
        control_case = replace(
            case,
            model_id=f"negative-control/{lane}",
            variant_id="pitwall-bogus-flag-7",
            argv=(*case.argv, "--pitwall-bogus-flag-7"),
            comparison_note="must reject injected bogus flag",
        )
        command = runner(_parse_argv(control_case), timeout_s)
        classification, detail = classify_parse_result(
            command,
            engine=case.engine,
            negative_control=True,
        )
        if classification != "SHAPE_DEFECT":
            for index in control_indexes[lane, case.used_image]:
                results[index] = replace(
                    results[index],
                    classification="PROBE_INVALID",
                    detail=detail,
                )
            classification = "PROBE_INVALID"
        results.append(
            ProbeResult(
                model_id=control_case.model_id,
                variant_id=control_case.variant_id,
                engine=control_case.engine,
                declared_image=control_case.declared_image,
                used_image=control_case.used_image,
                classification=classification,
                detail=detail,
                argv=control_case.argv,
                elapsed_s=command.elapsed_s,
                comparison_note=control_case.comparison_note,
                negative_control=True,
            )
        )
    return results


def render_report(results: Sequence[ProbeResult]) -> RenderedReport:
    production_results = [item for item in results if not item.negative_control]
    control_results = [item for item in results if item.negative_control]
    summary = {
        name: sum(item.classification == name for item in production_results)
        for name in (
            "SHAPE_DEFECT",
            "UNCLEAR",
            "PROBE_INVALID",
            "OK_SUBSTITUTED",
            "OK",
        )
    }
    substituted = sum(item.used_image != item.declared_image for item in production_results)
    payload = {
        "schema_version": 2,
        "summary": summary,
        "negative_controls": [asdict(result) for result in control_results],
        "variants": [asdict(result) for result in results],
    }
    rows = [
        "| Kind | Model | Variant | Engine | Declared image | Used image | Classification | Note | Detail |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows.extend(
        f"| {'negative control' if item.negative_control else 'catalogue'} | "
        f"{item.model_id} | {item.variant_id} | {item.engine} | "
        f"{item.declared_image} | {item.used_image} | {item.classification} | "
        f"{item.comparison_note} | {item.detail} |"
        for item in results
    )
    substitution_summary = (
        f"{substituted} row{'s' if substituted != 1 else ''} parser-checked on a "
        "substitute image; declared image not executed."
    )
    return RenderedReport(
        table="\n".join([*rows, "", substitution_summary]),
        json_text=json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def _pull_declared_images(cases: Sequence[VariantCase]) -> None:
    for image in dict.fromkeys(case.declared_image for case in cases):
        subprocess.run(["docker", "pull", image], check=True)


def _http_request(method: str, url: str, payload: object | None) -> tuple[int, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read()
        return response.status, json.loads(body) if body else {}


def verify_llama_http(base_url: str, alias: str, *, request: Request = _http_request) -> None:
    health_status, _ = request("GET", f"{base_url}/health", None)
    if health_status != 200:
        raise RuntimeError(f"llama.cpp health returned HTTP {health_status}")
    models_status, raw_models = request("GET", f"{base_url}/v1/models", None)
    models = cast(dict[str, object], raw_models)
    data = models.get("data", [])
    ids = (
        {
            item.get("id")
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if isinstance(data, list)
        else set()
    )
    if models_status != 200 or alias not in ids:
        raise RuntimeError(f"llama.cpp did not publish served alias {alias!r}")
    chat_status, raw_chat = request(
        "POST",
        f"{base_url}/v1/chat/completions",
        {
            "model": alias,
            "messages": [{"role": "user", "content": "Reply with hello."}],
            "max_tokens": 8,
            "stream": False,
        },
    )
    chat = cast(dict[str, object], raw_chat)
    if chat_status != 200 or not isinstance(chat.get("choices"), list):
        raise RuntimeError(f"llama.cpp chat returned HTTP {chat_status}")


def download_proposal(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / PROPOSAL_FILE
    if not target.exists():
        with urllib.request.urlopen(PROPOSAL_URL, timeout=120) as response:
            target.write_bytes(response.read(MAX_GGUF_BYTES + 1))
    size = target.stat().st_size
    if size != PROPOSAL_SIZE or size > MAX_GGUF_BYTES:
        raise RuntimeError(f"proposal size mismatch: expected {PROPOSAL_SIZE}, found {size} bytes")
    return target


def _pull_cpu_image() -> ImageMetric:
    started = time.monotonic()
    subprocess.run(
        ["docker", "pull", LLAMA_CPU_IMAGE],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    pull_s = round(time.monotonic() - started, 3)
    inspected = subprocess.run(
        ["docker", "image", "inspect", LLAMA_CPU_IMAGE, "--format", "{{.Size}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return ImageMetric(
        image=LLAMA_CPU_IMAGE,
        pull_s=pull_s,
        size_bytes=int(inspected.stdout),
    )


def run_llama_server(model_path: Path, *, port: int = 8000) -> None:
    container = "pitwall-engine-smoke-llama"
    subprocess.run(
        ["docker", "rm", "--force", container],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shape = launch_shape(
        "llama.cpp",
        model="smoke/local-gguf",
        served=SMOKE_ALIAS,
        gpu_count=1,
        repo=PROPOSAL_REPO,
        file=PROPOSAL_FILE,
        flags=(),
        companion_flags=(),
        start_args=(),
    )
    shape[:4] = ["-m", f"/models/{PROPOSAL_FILE}"]
    command = [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        container,
        "--publish",
        f"127.0.0.1:{port}:8000",
        "--volume",
        f"{model_path.resolve()}:/models/{PROPOSAL_FILE}:ro",
        LLAMA_CPU_IMAGE,
        *shape,
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    deadline = time.monotonic() + 120
    try:
        while True:
            try:
                verify_llama_http(f"http://127.0.0.1:{port}", SMOKE_ALIAS)
                return
            except (
                RuntimeError,
                urllib.error.URLError,
                json.JSONDecodeError,
                OSError,
            ):
                if time.monotonic() >= deadline:
                    logs = subprocess.run(
                        ["docker", "logs", container],
                        check=False,
                        capture_output=True,
                        text=True,
                    ).stdout
                    raise RuntimeError(f"llama.cpp HTTP smoke timed out\n{logs}") from None
                time.sleep(1)
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, default=Path("docs/models"))
    parser.add_argument("--work-dir", type=Path, default=Path("artifacts/engine-smoke"))
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--allow-image-substitution",
        action="store_true",
        help="allow a local development run to pass when declared images were substituted",
    )
    parser.add_argument(
        "--pull-declared",
        action="store_true",
        help="pull and parser-check every declared image instead of local substitutes",
    )
    lane = parser.add_mutually_exclusive_group()
    lane.add_argument("--parse-only", action="store_true")
    lane.add_argument("--llama-cpu", action="store_true")
    args = parser.parse_args()
    args.work_dir = args.work_dir.resolve()  # Docker bind mounts need an absolute host path
    for output in (args.json_output, args.report):
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)

    run_parse = not args.llama_cpu
    run_llama = not args.parse_only
    cases = (
        build_parse_cases(
            load_catalogue(args.catalogue),
            use_declared_images=args.pull_declared,
        )
        if run_parse
        else []
    )
    if run_parse and args.pull_declared:
        _pull_declared_images(cases)
    results = run_parse_probes(cases, timeout_s=120.0) if run_parse else []
    metric = None
    if run_llama:
        metric = _pull_cpu_image()
        proposal = download_proposal(args.work_dir)
        run_llama_server(proposal)
    report = render_report(results)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(report.json_text, encoding="utf-8")
    sections = ["# Engine launch-shape smoke"]
    if run_parse:
        sections.extend(["## Parse probes", report.table])
    if run_llama and metric is not None:
        sections.extend(
            [
                "## llama.cpp CPU full cycle",
                f"GGUF: `{PROPOSAL_REPO}/{PROPOSAL_FILE}` ({PROPOSAL_SIZE} bytes).",
                f"CPU image: `{metric.image}` ({metric.size_bytes} bytes).",
                "`/health`, `/v1/models` alias, and `/v1/chat/completions` passed.",
            ]
        )
    markdown = "\n\n".join(sections) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(markdown, encoding="utf-8")
    print(markdown)
    invalid = any(
        item.classification in {"SHAPE_DEFECT", "UNCLEAR", "PROBE_INVALID"}
        for item in results
        if not (item.negative_control and item.classification == "SHAPE_DEFECT")
    )
    if invalid:
        return 1
    substituted = any(
        item.used_image != item.declared_image for item in results if not item.negative_control
    )
    if substituted and not args.allow_image_substitution:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
