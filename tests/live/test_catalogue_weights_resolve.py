"""Every declared weight file must exist in its HuggingFace repository.

A dossier named Ornith-1.5-35B-A3B-Q4_K_M.gguf for a repo that publishes
Ornith-1.5-35B-Q4_K_M.gguf. Every launch of that variant was guaranteed to fail
*after* paying for a pod. Resolving the pair costs one HTTP call.
"""

from __future__ import annotations

import os

import httpx
import pytest

from pitwall.models.catalogue import load_catalogue

pytestmark = pytest.mark.live
if not os.environ.get("PITWALL_HF_CATALOGUE_CHECK"):
    pytest.skip("set PITWALL_HF_CATALOGUE_CHECK to resolve weights", allow_module_level=True)

_API = "https://huggingface.co/api/models/{repo}"


def _declared_pairs() -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for model in load_catalogue():
        for variant in model.variants:
            if variant.file:
                pairs.append((f"{model.id}:{variant.id}", variant.repo, variant.file))
            for companion in variant.companions:
                if companion.file:
                    pairs.append(
                        (f"{model.id}:{variant.id}:companion", companion.repo, companion.file)
                    )
    return pairs


@pytest.mark.parametrize("label,repo,filename", _declared_pairs())
def test_declared_weight_file_exists(label: str, repo: str, filename: str) -> None:
    response = httpx.get(_API.format(repo=repo), params={"blobs": "true"}, timeout=30.0)
    assert response.status_code == 200, f"{label}: repo {repo} is not reachable"
    names = {entry["rfilename"] for entry in response.json().get("siblings", [])}
    assert filename in names, (
        f"{label}: {repo} does not publish {filename}. Closest: "
        f"{sorted(n for n in names if n.endswith('.gguf'))[:5]}"
    )
