from __future__ import annotations

import json
from pathlib import Path

from pitwall.models.catalogue import load_catalogue

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTING_CATALOGUE = (
    _REPO_ROOT / "src" / "pitwall" / "agents" / "resources" / "config" / "model-catalog.json"
)

_EXACT_OVERLAPS = (
    "MiniMaxAI/MiniMax-M2.7",
    "MiniMaxAI/MiniMax-M3",
    "Qwen/Qwen3.8-Flash-Next",
    "XiaomiMiMo/MiMo-V2.5",
    "XiaomiMiMo/MiMo-V2.5-Pro",
    "deepseek-ai/DeepSeek-V4-Flash-0731",
    "deepseek-ai/DeepSeek-V4-Flash-Vision-Exp",
    "deepseek-ai/DeepSeek-V4-Pro-0813",
    "meituan-longcat/LongCat-2.0",
    "meta-models/Muse-Glimmer-30B",
    "moonshotai/Kimi-K2.6",
    "moonshotai/Kimi-K2.7-Code",
    "moonshotai/Kimi-K3",
    "tencent/Hy3",
    "tencent/Hy4-preview",
    "zai-org/GLM-5.1",
    "zai-org/GLM-5.2",
    "zai-org/GLM-5.3",
    "zai-org/GLM-5.3-Flash",
)
_CURATED_NEAR_MATCHES = (("google/gemma-4-31B", "google/gemma-4-31B-it"),)


def _agent_routing_model_ids() -> set[str]:
    payload = json.loads(_ROUTING_CATALOGUE.read_text(encoding="utf-8"))
    return {entry["modelId"] for entry in payload["models"].values()}


def test_exact_model_id_overlap_report_is_deterministic() -> None:
    pitwall_ids = {dossier.model_id for dossier in load_catalogue().models()}
    routing_ids = _agent_routing_model_ids()

    assert tuple(sorted(pitwall_ids & routing_ids)) == _EXACT_OVERLAPS


def test_curated_near_matches_remain_explicit_and_non_normalizing() -> None:
    pitwall_ids = {dossier.model_id for dossier in load_catalogue().models()}
    routing_ids = _agent_routing_model_ids()

    reported = tuple(
        pair for pair in _CURATED_NEAR_MATCHES if pair[0] in routing_ids and pair[1] in pitwall_ids
    )

    assert reported == _CURATED_NEAR_MATCHES
    assert "google/gemma-4-31B" not in pitwall_ids
    assert "google/gemma-4-31B-it" not in routing_ids
    assert not (pitwall_ids & routing_ids) & {item for pair in reported for item in pair}
