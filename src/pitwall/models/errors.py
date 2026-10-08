from __future__ import annotations


class CatalogueError(RuntimeError):
    """A catalogue file is unreadable or invalid."""


class UnknownModel(CatalogueError):
    error_code = "unknown_model"

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        super().__init__(f"unknown model: {model_id}")


class UnknownVariant(CatalogueError):
    error_code = "unknown_variant"

    def __init__(self, model_id: str, variant_id: str | None) -> None:
        self.model_id = model_id
        self.variant_id = variant_id
        super().__init__(f"unknown variant for {model_id}: {variant_id}")
