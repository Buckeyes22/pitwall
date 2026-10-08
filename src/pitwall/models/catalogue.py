from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from decimal import Decimal
from functools import cache
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import ValidationError

from pitwall.config import load_settings_from_env
from pitwall.models.errors import CatalogueError, UnknownModel, UnknownVariant
from pitwall.models.lookup import CatalogueLookup, CompanionInfo, EvidenceInfo, VariantInfo
from pitwall.models.schema import (
    ModelDossier,
    PositiveIntOrUnverified,
    PositiveNumberOrUnverified,
    Variant,
)


class _YamlModule(Protocol):
    YAMLError: type[Exception]

    @staticmethod
    def safe_load(stream: str) -> object: ...

    @staticmethod
    def safe_dump(data: object, *, sort_keys: bool) -> str: ...


yaml = cast(_YamlModule, import_module("yaml"))


def _verified(value: PositiveIntOrUnverified) -> int | None:
    return value if isinstance(value, int) else None


def _verified_number(value: PositiveNumberOrUnverified) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _yaml_problem(exc: Exception, first_line: int = 1) -> str:
    """A YAML error's class and position only: PyYAML's own text can quote the file."""
    name = type(exc).__name__
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is not None:
        return f"invalid YAML ({name}) at line {mark.line + first_line}, column {mark.column + 1}"
    position = getattr(exc, "position", None)
    where = f" at character {position + 1}" if isinstance(position, int) else ""
    return f"invalid YAML ({name}){where}"


def _validation_problems(exc: ValidationError) -> str:
    """``loc: msg`` lines; pydantic's own text includes input values, which can quote the file."""
    return "\n".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )


def _parse_dossier(path: Path) -> ModelDossier:
    try:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise ValueError("missing opening YAML front-matter delimiter")
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
        try:
            payload = yaml.safe_load("".join(lines[1:closing]))
        except (
            yaml.YAMLError,
            ValueError,
        ) as exc:  # reason: PyYAML's timestamp constructor raises a plain ValueError
            raise CatalogueError(f"{path}: {_yaml_problem(exc, first_line=2)}") from None
        if not isinstance(payload, dict):
            raise ValueError("front matter must be a YAML mapping")
        payload["body"] = "".join(lines[closing + 1 :]).lstrip("\n")
        return ModelDossier.model_validate(payload)
    except ValidationError as exc:
        raise CatalogueError(f"{path}: {_validation_problems(exc)}") from None
    except (
        OSError,
        UnicodeError,
        ValueError,
        StopIteration,
    ) as exc:
        raise CatalogueError(f"{path}: {exc}") from exc


def _front_matter(path: Path) -> tuple[dict[str, object], str]:
    """Read a dossier's YAML mapping and preserve its Markdown body verbatim."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogueError(f"{path}: {exc}") from exc
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise CatalogueError(f"{path}: missing opening YAML front-matter delimiter")
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as exc:
        raise CatalogueError(f"{path}: missing closing YAML front-matter delimiter") from exc
    try:
        payload = yaml.safe_load("".join(lines[1:closing]))
    except (
        yaml.YAMLError,
        ValueError,
    ) as exc:  # reason: PyYAML's timestamp constructor raises a plain ValueError
        raise CatalogueError(f"{path}: {_yaml_problem(exc, first_line=2)}") from None
    if not isinstance(payload, dict):
        raise CatalogueError(f"{path}: front matter must be a YAML mapping")
    return payload, "".join(lines[closing + 1 :])


def _atomic_write(path: Path, text: str) -> None:
    """Write text through a sibling file so a failed write preserves the dossier."""
    temp_path: Path | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp_path, path)
    except OSError as exc:
        raise CatalogueError(f"{path}: {exc}") from exc
    finally:
        if temp_path is not None:
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)


def write_evidence(
    root: Path | None = None,
    *,
    model_id: str,
    variant_id: str,
    gpu_class: str,
    observed_vram_gb: float,
    observed_startup_s: float,
    date: str,
) -> Variant:
    """Record a schema-validated measured observation without altering a dossier body."""
    try:
        catalogue_root = _resolved_root(root)
        path = (catalogue_root / f"{model_id.replace('/', '--')}.md").resolve()
    except OSError as exc:
        raise CatalogueError(f"cannot resolve dossier path for {model_id}: {exc}") from exc
    if path.parent != catalogue_root:
        raise CatalogueError(f"invalid model ID for dossier path: {model_id}")
    payload, body = _front_matter(path)
    variants = payload.get("variants")
    if not isinstance(variants, list):
        raise CatalogueError(f"{path}: variants must be a list")
    for variant in variants:
        if isinstance(variant, dict) and variant.get("id") == variant_id:
            variant["evidence"] = {
                "kind": "measured",
                "gpu_class": gpu_class,
                "observed_vram_gb": observed_vram_gb,
                "observed_startup_s": observed_startup_s,
                "date": date,
            }
            break
    else:
        raise UnknownVariant(model_id, variant_id)

    payload["body"] = body.lstrip("\n")
    try:
        dossier = ModelDossier.model_validate(payload)
    except ValidationError as exc:
        raise CatalogueError(f"{path}: {_validation_problems(exc)}") from None
    updated = dossier.resolve_variant(variant_id)
    assert updated is not None
    payload.pop("body", None)
    yaml_text = yaml.safe_dump(payload, sort_keys=False)
    _atomic_write(path, f"---\n{yaml_text}---\n{body}")
    return updated


class Catalogue:
    def __init__(self, dossiers: tuple[ModelDossier, ...]) -> None:
        by_id = {dossier.model_id: dossier for dossier in dossiers}
        if len(by_id) != len(dossiers):
            raise CatalogueError("duplicate model_id in catalogue")
        self._by_id = by_id

    def models(self) -> list[ModelDossier]:
        return sorted(self._by_id.values(), key=lambda dossier: dossier.model_id)

    def get(self, model_id: str) -> ModelDossier | None:
        return self._by_id.get(model_id)

    def dossier_variant(self, model_id: str, variant_id: str | None) -> Variant:
        dossier = self.get(model_id)
        if dossier is None:
            raise UnknownModel(model_id)
        variant = dossier.resolve_variant(variant_id)
        if variant is None:
            raise UnknownVariant(model_id, variant_id)
        params = dossier.architecture.params_total_b
        return variant.model_copy(
            update={
                "params_total_b": (
                    Decimal(str(params)) if isinstance(params, (int, float)) else None
                ),
                "layers": dossier.architecture.layers,
            }
        )

    def variant(self, model_id: str, variant_id: str | None) -> VariantInfo | None:
        dossier = self.get(model_id)
        if dossier is None:
            return None
        variant = dossier.resolve_variant(variant_id)
        if variant is None:
            raise UnknownVariant(model_id, variant_id)
        return VariantInfo(
            variant_id=variant.id,
            engine=variant.engine,
            image=variant.image,
            min_cuda=variant.min_cuda,
            repo=variant.repo,
            file=variant.file,
            flags=variant.flags,
            companions=tuple(
                CompanionInfo(
                    kind=item.kind,
                    repo=item.repo,
                    file=item.file,
                    flags=item.flags,
                )
                for item in variant.companions
            ),
            evidence=(
                EvidenceInfo(
                    kind=variant.evidence.kind,
                    gpu_class=variant.evidence.gpu_class,
                    observed_vram_gb=_verified_number(variant.evidence.observed_vram_gb),
                    observed_startup_s=_verified_number(variant.evidence.observed_startup_s),
                    date=variant.evidence.date,
                )
                if variant.evidence is not None
                else None
            ),
            openai_chat=dossier.openai_chat,
            env=dict(variant.env),
            container_disk_gb=_verified(variant.container_disk_gb),
            startup_min=_verified(variant.startup_min),
            gated=dossier.license.gated,
            served_model_name=dossier.pitwall.served_model_name,
        )


def _default_source_root() -> Path:
    packaged = Path(__file__).with_name("data")
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[3] / "docs" / "models"


def _resolved_root(root: Path | None) -> Path:
    if root is not None:
        return root.resolve()
    configured = load_settings_from_env().pitwall_models_dir
    return configured.resolve() if configured is not None else _default_source_root().resolve()


@cache
def _load_resolved(root: Path) -> Catalogue:
    if not root.is_dir():
        raise CatalogueError(f"catalogue directory does not exist: {root}")
    paths = sorted(path for path in root.glob("*.md") if path.name != "README.md")
    return Catalogue(tuple(_parse_dossier(path) for path in paths))


def load_catalogue(root: Path | None = None) -> Catalogue:
    return _load_resolved(_resolved_root(root))


def reload() -> None:
    _load_resolved.cache_clear()


if TYPE_CHECKING:
    _lookup_check: CatalogueLookup = cast(Catalogue, None)
