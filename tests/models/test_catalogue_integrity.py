"""Every packaged dossier's CUDA floor is usable by the serve paths."""

from __future__ import annotations

from pitwall.models import load_catalogue
from pitwall.serve import _allowed_cuda_versions


def test_every_catalogue_variant_has_a_usable_cuda_floor() -> None:
    catalogue = load_catalogue()
    for model in catalogue.models():
        for variant in model.variants:
            if variant.min_cuda is None:
                continue
            allowed = _allowed_cuda_versions(variant.min_cuda, ["12.8", "12.9", "13.0"])
            assert allowed, (model.model_id, variant.id, variant.min_cuda)
