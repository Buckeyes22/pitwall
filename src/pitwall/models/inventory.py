from __future__ import annotations

import re
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Annotated, Any, Protocol, cast

from pydantic import Field, ValidationError, field_validator

from pitwall.core.models import PitwallModel


class _YamlModule(Protocol):
    YAMLError: type[Exception]

    @staticmethod
    def safe_load(stream: str) -> object: ...


yaml = cast(_YamlModule, import_module("yaml"))


class LocalGpu(PitwallModel):
    name: str
    count: Annotated[int, Field(ge=1)]
    vram_gb: Annotated[Decimal, Field(gt=0)]
    arch: str
    nvlink: bool

    @field_validator("arch")
    @classmethod
    def valid_arch(cls, value: str) -> str:
        if re.fullmatch(r"sm_[0-9]{2,3}", value) is None:
            raise ValueError("arch must look like sm_86")
        return value


class LocalInventory(PitwallModel):
    gpus: tuple[LocalGpu, ...]
    gpu_memory_utilization: Annotated[
        Decimal,
        Field(gt=0, le=1),
    ] = Decimal("0.90")


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


def load_inventory(path: Path) -> LocalInventory:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (
        yaml.YAMLError,
        ValueError,
    ) as exc:  # reason: PyYAML's timestamp constructor raises a plain ValueError
        raise ValueError(f"{path}: {_yaml_problem(exc)}") from None
    if not isinstance(raw, dict):
        raise ValueError("inventory file must contain a mapping")
    payload = raw.get("inventory", raw)
    try:
        return LocalInventory.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"{path}: {_validation_problems(exc)}") from None


__all__ = ["LocalGpu", "LocalInventory", "load_inventory"]
