from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from pitwall.models.schema import ModelDossier
from pitwall.runpod_client.gpu import validate_canonical_gpu_name

UPSTREAM_FOR_REPUBLICATION = {
    "unsloth/Qwen3.8-27B-GGUF": "Qwen/Qwen3.8-27B",
    "unsloth/Qwen3.8-Flash-Next-GGUF": "Qwen/Qwen3.8-Flash-Next",
    "unsloth/gemma-4-31B-it-GGUF": "google/gemma-4-31B-it",
    "unsloth/Muse-Glimmer-30B-GGUF": "meta-models/Muse-Glimmer-30B",
}
UNSUPPORTED_ENGINES = {
    "MiniMaxAI/MiniMax-Music3": "unsupported engine sglang; catalogue engines are vllm and llama.cpp",
}
VARIANT_REPOS = {
    ("Qwen/Qwen3.8-27B", "fp8"): "Qwen/Qwen3.8-27B-FP8",
    ("Qwen/Qwen3.8-27B", "gguf:UD-Q4_K_XL"): "unsloth/Qwen3.8-27B-GGUF",
    ("Qwen/Qwen3.8-Flash-Next", "fp8"): "Qwen/Qwen3.8-Flash-Next-FP8",
    ("deepseek-ai/DeepSeek-V4-Flash-0731", "fp4+fp8"): "nvidia/DeepSeek-V4-Flash-NVFP4",
    ("zai-org/GLM-5.2", "fp8"): "zai-org/GLM-5.2-FP8",
    ("zai-org/GLM-5.2", "nvfp4"): "nvidia/GLM-5.2-NVFP4",
    ("google/gemma-4-31B-it", "gguf:UD-Q5_K_XL"): "unsloth/gemma-4-31B-it-GGUF",
    ("meta-models/Muse-Glimmer-30B", "gguf:UD-Q5_K_XL"): "unsloth/Muse-Glimmer-30B-GGUF",
}
GGUF_FILES = {
    ("Qwen/Qwen3.8-27B", "gguf:UD-Q4_K_XL"): "Qwen3.8-27B-UD-Q4_K_XL.gguf",
    ("google/gemma-4-31B-it", "gguf:UD-Q5_K_XL"): "gemma-4-31B-it-UD-Q5_K_XL.gguf",
    ("meta-models/Muse-Glimmer-30B", "gguf:UD-Q5_K_XL"): "Muse-Glimmer-30B-UD-Q5_K_XL.gguf",
    ("ornith-ai/Ornith-1.5-35B-A3B-GGUF", "gguf:Q4_K_M"): "Ornith-1.5-35B-A3B-Q4_K_M.gguf",
}
FORMAT_FOR_VARIANT = {"bf16": "bf16", "fp8": "fp8", "fp4+fp8": "nvfp4", "nvfp4": "nvfp4"}
_OWNED_WITH_VALUE = {
    "--served-model-name",
    "--host",
    "--port",
    "--tensor-parallel-size",
    "--hf-repo",
    "--hf-file",
    "--alias",
    "--n-gpu-layers",
    "-hf",
}
_OWNED_SWITCHES = {"--jinja"}
_SKIP_ENV_VALUES = {"not needed", "optional", "unverified"}


@dataclass(frozen=True, slots=True)
class ConversionReport:
    written: tuple[str, ...]
    folded: dict[str, str]
    excluded: dict[str, str]


def read_front_matter(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: missing opening YAML front-matter delimiter")
    try:
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"{path}: missing closing YAML front-matter delimiter") from exc
    payload = yaml.safe_load("".join(lines[1:closing]))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: front matter must be a YAML mapping")
    return cast(dict[str, object], payload), "".join(lines[closing + 1 :]).lstrip("\n")


def variant_flags(command: list[str], engine: str) -> list[str]:
    start = 1 if engine == "vllm" and command and not command[0].startswith("-") else 0
    result: list[str] = []
    index = start
    while index < len(command):
        token = command[index]
        if token in _OWNED_WITH_VALUE:
            index += 2
            continue
        if token in _OWNED_SWITCHES:
            index += 1
            continue
        result.append(token)
        index += 1
    return result


def _as_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return cast(dict[str, object], value)


def _as_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _format(variant_id: str) -> str:
    return "gguf" if variant_id.startswith("gguf:") else FORMAT_FOR_VARIANT[variant_id]


def _startup_min(value: object) -> int | str:
    return value // 60 if isinstance(value, int) and value > 0 and value % 60 == 0 else "unverified"


def _hints(old_hardware: dict[str, object], row: dict[str, object]) -> list[str]:
    values = _as_list(old_hardware.get("recommended_gpu_classes", []), "recommended_gpu_classes")
    for option in _as_list(row.get("gpu_options", []), "gpu_options"):
        values.append(_as_mapping(option, "gpu option").get("gpu_class", ""))
    return list(dict.fromkeys(validate_canonical_gpu_name(str(value)) for value in values))


def _env(serving: dict[str, object]) -> dict[str, str]:
    source = _as_mapping(serving.get("env", {}), "serving.env")
    return {
        key: str(value)
        for key, value in source.items()
        if key != "HF_TOKEN" and str(value).casefold() not in _SKIP_ENV_VALUES
    }


def _variant(
    row: dict[str, object], upstream: dict[str, object], default: bool
) -> dict[str, object]:
    model_id = str(upstream["model_id"])
    variant_id = str(row["variant"])
    engine = str(row["engine"])
    serving = _as_mapping(upstream["serving"], "serving")
    hardware = _as_mapping(upstream["hardware"], "hardware")
    command = [
        str(item) for item in _as_list(serving.get("docker_start_cmd", []), "docker_start_cmd")
    ]
    result: dict[str, object] = {
        "id": variant_id,
        "default": default,
        "engine": engine,
        "image": row["image"],
        "repo": VARIANT_REPOS.get((model_id, variant_id), model_id),
        "file": None,
        "format": _format(variant_id),
        "min_vram_gb": row["min_vram_gb"],
        "context": row["context_assumed"],
        "container_disk_gb": row["container_disk_gb"],
        "startup_min": _startup_min(row.get("startup_timeout_s_suggested")),
        "flags": variant_flags(command, engine),
        "env": _env(serving),
        "recommended_gpu_classes": _hints(hardware, row),
        "tool_call_parser": serving.get("tool_call_parser"),
        "reasoning_parser": serving.get("reasoning_parser"),
        "confidence": row["confidence"],
        "sources": [],
    }
    if engine == "llama.cpp":
        result["file"] = GGUF_FILES[(model_id, variant_id)]
    return result


def _body_with_research(body: str, additions: list[tuple[str, str]]) -> str:
    result = body.rstrip() + "\n"
    for title, research in additions:
        result += f"\n## {title}\n\n{research.rstrip()}\n"
    return result


def _write_dossier(path: Path, payload: dict[str, object], body: str) -> None:
    ModelDossier.model_validate({**payload, "body": body})
    path.write_text(
        "---\n" + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True) + "---\n\n" + body,
        encoding="utf-8",
    )


def convert_dossiers(
    normalized_dir: Path, gguf_dir: Path, matrix_path: Path, output_dir: Path
) -> ConversionReport:
    sources: dict[str, tuple[dict[str, object], str]] = {}
    for path in sorted(normalized_dir.glob("*.md")):
        if not path.read_text(encoding="utf-8").startswith("---"):
            continue
        payload, body = read_front_matter(path)
        model_id = payload.get("model_id")
        if isinstance(model_id, str) and model_id not in UPSTREAM_FOR_REPUBLICATION:
            sources[model_id] = (payload, body)
    matrix = _as_mapping(yaml.safe_load(matrix_path.read_text(encoding="utf-8")), "matrix")
    rows = [_as_mapping(row, "matrix row") for row in _as_list(matrix["entries"], "entries")]
    for row in rows:
        if row["model_id"] not in sources:
            raise ValueError(f"matrix model has no upstream dossier: {row['model_id']}")
    gguf_bodies: dict[str, str] = {}
    for path in sorted(gguf_dir.glob("unsloth--*.md")):
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            payload, body = read_front_matter(path)
            model_id = payload.get("model_id")
            if isinstance(model_id, str):
                gguf_bodies[model_id] = body
        elif path.name in {"unsloth--GGUF-catalog.md", "unsloth--OVERVIEW.md"}:
            gguf_bodies[path.stem.replace("--", "/", 1)] = text
    common = [
        ("Unsloth GGUF catalogue provenance", gguf_bodies[key])
        for key in ("unsloth/GGUF-catalog", "unsloth/OVERVIEW")
        if key in gguf_bodies
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    folded = dict(UPSTREAM_FOR_REPUBLICATION)
    written: list[str] = []
    for model_id, (upstream, body) in sorted(sources.items()):
        if model_id in UNSUPPORTED_ENGINES:
            continue
        model_rows = [row for row in rows if row["model_id"] == model_id]
        if not model_rows:
            continue
        payload = {
            key: value
            for key, value in upstream.items()
            if key not in {"weights", "serving", "hardware"}
        }
        payload["openai_chat"] = model_rows[0]["openai_chat"]
        payload["pitwall"] = {
            key: value
            for key, value in _as_mapping(payload["pitwall"], "pitwall").items()
            if key in {"capability_name", "served_model_name"}
        }
        payload["variants"] = [
            _variant(row, upstream, index == 0) for index, row in enumerate(model_rows)
        ]
        additions = [
            ("Folded Unsloth GGUF research", gguf_bodies[republished])
            for republished, target in folded.items()
            if target == model_id and republished in gguf_bodies
        ] + common
        path = output_dir / (model_id.replace("/", "--", 1) + ".md")
        _write_dossier(path, payload, _body_with_research(body, additions))
        written.append(path.name)
    return ConversionReport(tuple(written), folded, dict(UNSUPPORTED_ENGINES))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--normalized-dir", type=Path, required=True)
    parser.add_argument("--gguf-dir", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = convert_dossiers(args.normalized_dir, args.gguf_dir, args.matrix, args.output_dir)
    report_path = args.output_dir / "conversion-report.yaml"
    report_path.write_text(
        yaml.safe_dump(
            {
                "source_model_dossiers": 13,
                "shipped_upstream_dossiers": 11,
                "shipped_variants": 15,
                "folded": dict(sorted(report.folded.items())),
                "excluded": dict(sorted(report.excluded.items())),
            },
            sort_keys=False,
            allow_unicode=True,
            width=1000,
        ),
        encoding="utf-8",
    )
    print(
        f"wrote {len(report.written)} dossiers with 15 variants; folded {len(report.folded)} re-publications; excluded {len(report.excluded)} unsupported model"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
