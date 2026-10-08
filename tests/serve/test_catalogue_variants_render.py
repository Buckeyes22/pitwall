from __future__ import annotations

from pathlib import Path

import pytest

from pitwall import serve
from pitwall.models.catalogue import load_catalogue
from pitwall.models.lookup import CatalogueLookup


def _catalogue_variants() -> list[object]:
    catalogue = load_catalogue(Path("docs/models"))
    return [
        pytest.param(dossier.model_id, variant.id, id=f"{dossier.model_id}/{variant.id}")
        for dossier in catalogue.models()
        for variant in dossier.variants
    ]


@pytest.mark.parametrize(("model_id", "variant_id"), _catalogue_variants())
def test_catalogue_variant_renders_serve_launch_shape(
    model_id: str,
    variant_id: str,
) -> None:
    catalogue: CatalogueLookup = load_catalogue(Path("docs/models"))
    info = catalogue.variant(model_id, variant_id)
    assert info is not None

    companion_flags = serve._companion_flags(info.engine, info.companions)
    argv = serve.launch_shape(
        info.engine,
        model=model_id,
        served=info.served_model_name or model_id,
        gpu_count=1,
        repo=info.repo,
        file=info.file,
        flags=info.flags,
        companion_flags=companion_flags,
        start_args=(),
    )

    assert argv
