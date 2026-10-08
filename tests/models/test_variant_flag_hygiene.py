from __future__ import annotations

from pathlib import Path

import pytest

from pitwall.models.catalogue import Catalogue, load_catalogue

VLLM_ONLY = {
    "--max-model-len",
    "--gpu-memory-utilization",
    "--kv-cache-dtype",
    "--reasoning-parser",
    "--tool-call-parser",
    "--enable-auto-tool-choice",
    "--max-num-batched-tokens",
    "--limit-mm-per-prompt",
    "--chat-template",
    "--generation-config",
    "--served-model-name",
}
LLAMA_ONLY = {
    "--ctx-size",
    "--n-gpu-layers",
    "--flash-attn",
    "--hf-repo",
    "--hf-file",
    "--alias",
    "--jinja",
}


def assert_engine_native_flags(catalogue: Catalogue) -> None:
    for dossier in catalogue.models():
        for variant in dossier.variants:
            tokens = set(variant.flags)
            if variant.engine == "llama.cpp":
                invalid = tokens & VLLM_ONLY
                invalid.update(
                    token for token in variant.flags if "/" in token and not token.startswith("-")
                )
            else:
                invalid = tokens & LLAMA_ONLY
            assert not invalid, f"{dossier.model_id}/{variant.id}: {sorted(invalid)}"


def test_shipped_variant_flags_are_engine_native() -> None:
    assert_engine_native_flags(
        load_catalogue(Path(__file__).resolve().parents[2] / "docs" / "models")
    )


def test_hygiene_rejects_bad_llama_flag_in_fixture(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text(
        """---
model_id: example/Bad-GGUF
vendor: Example
family: Bad
release_date: '2026-08-28'
license: {name: MIT, url: https://example.test/license, gated: false}
architecture:
  kind: dense
  params_total_b: 1
  params_active_b: 1
  context_length_max: 4096
  modalities: [text]
  thinking_mode: 'none'
capabilities: {tool_calling: 'no', structured_outputs: 'no', vision: false, languages: English}
pitwall: {capability_name: llm.bad, served_model_name: bad}
openai_chat: true
variants:
- id: gguf
  default: true
  engine: llama.cpp
  image: ghcr.io/ggml-org/llama.cpp:server-cuda
  repo: example/Bad-GGUF
  file: bad.gguf
  format: gguf
  min_vram_gb: 1
  context: 4096
  container_disk_gb: 1
  startup_min: 1
  flags: [--max-model-len, '4096']
  env: {}
  recommended_gpu_classes: []
  tool_call_parser: null
  reasoning_parser: null
  confidence: low
  sources: []
confidence: {overall: low, notes: fixture}
accessed: '2026-08-28'
---
""",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="--max-model-len"):
        assert_engine_native_flags(load_catalogue(tmp_path))
