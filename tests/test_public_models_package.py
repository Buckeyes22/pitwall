from __future__ import annotations

from pitwall.models import (
    Catalogue,
    CatalogueError,
    CatalogueLookup,
    ModelDossier,
    UnknownModel,
    UnknownVariant,
    Variant,
    VariantInfo,
    fit_options,
    load_catalogue,
    reload,
)


def test_public_models_package_exports_catalogue_names() -> None:
    public_names = (
        Catalogue,
        CatalogueError,
        CatalogueLookup,
        ModelDossier,
        UnknownModel,
        UnknownVariant,
        Variant,
        VariantInfo,
        load_catalogue,
        fit_options,
        reload,
    )
    assert all(public_name is not None for public_name in public_names)


def test_models_package_does_not_reexport_core_symbols() -> None:
    import pitwall.models as models

    for name in (
        "Capability",
        "CostMode",
        "Lease",
        "LeaseState",
        "Provider",
        "ProviderType",
        "Workload",
        "WorkloadState",
    ):
        assert not hasattr(models, name), name
        assert name not in models.__all__


def test_source_tree_catalogue_data_are_available() -> None:
    catalogue = load_catalogue()
    assert catalogue.get("google/gemma-4-31B-it") is not None
