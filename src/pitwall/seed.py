"""Seed-file loading for Pitwall onboarding.

The loader accepts a deliberately small YAML/JSON shape so new operators can
bootstrap a capability and provider without GPU discovery or optional services.
PyYAML is used when available; otherwise a local YAML subset parser handles the
example seed files and the documented onboarding format.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # reason: PyYAML ships no type stubs
from pydantic import ValidationError

from pitwall.api.provider_schemas import validate_provider_registration_config
from pitwall.core.enums import (
    CapabilityClass,
    CapabilityHint,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import (
    Capability,
    CapabilityDefaults,
    JsonObject,
    Provider,
    is_credential_reference_name,
    validate_provider_storage_payload,
)
from pitwall.db.repository import CapabilityRepository, ProviderRepository
from pitwall.providers.interface import ProviderDeclaration
from pitwall.providers.registry import get_default_registry
from pitwall.runpod_client.gpu import validate_canonical_gpu_name

_SEED_FILE_SUFFIXES = {".yaml", ".yml", ".json"}
_PROVIDER_HEALTH_STATUSES = {"unknown", "healthy", "unhealthy", "hibernated", "disarmed"}


class SeedValidationError(ValueError):
    """Raised when a seed file is syntactically valid but not usable."""


@dataclass(frozen=True)
class SeedDocument:
    """Parsed seed file with the content hash used for audit metadata."""

    path: Path
    payload: Mapping[str, Any]
    content_hash: str


@dataclass(frozen=True)
class SeedApplyResult:
    """Records written by a seed application."""

    capabilities: list[Capability]
    providers: list[Provider]


def seed_files_from_paths(paths: Sequence[str | Path]) -> list[Path]:
    """Return concrete seed files from file or directory arguments."""

    if not paths:
        raise SeedValidationError("at least one seed file or directory is required")

    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(
                sorted(
                    child
                    for child in path.iterdir()
                    if child.is_file() and child.suffix.lower() in _SEED_FILE_SUFFIXES
                )
            )
            continue
        if not path.exists():
            raise SeedValidationError(f"seed path does not exist: {path}")
        if not path.is_file():
            raise SeedValidationError(f"seed path is not a file: {path}")
        files.append(path)

    if not files:
        raise SeedValidationError("no seed files found")
    return files


def load_seed_documents(paths: Sequence[str | Path]) -> list[SeedDocument]:
    """Load and parse seed documents from files or directories."""

    documents: list[SeedDocument] = []
    for path in seed_files_from_paths(paths):
        text = path.read_text(encoding="utf-8")
        payload = _load_seed_payload(path, text)
        documents.append(
            SeedDocument(
                path=path,
                payload=payload,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    return documents


async def apply_capability_seed_files(
    paths: Sequence[str | Path],
    *,
    pool: Any,
    source: CapabilitySource = CapabilitySource.API,
) -> list[Capability]:
    """Apply only capability entries from seed/spec files."""

    documents = load_seed_documents(paths)
    cap_repo = CapabilityRepository(pool)
    capabilities, _capability_by_ref = await _apply_capabilities(
        documents=documents,
        cap_repo=cap_repo,
        source=source,
    )
    return capabilities


async def apply_seed_files(
    paths: Sequence[str | Path],
    *,
    pool: Any,
    source: CapabilitySource = CapabilitySource.YAML,
) -> SeedApplyResult:
    """Apply capability and provider seed files to the registry tables."""

    documents = load_seed_documents(paths)
    return await _apply_seed_documents(documents, pool=pool, source=source)


async def apply_seed_data(
    payload: Mapping[str, Any],
    *,
    pool: Any,
    source: CapabilitySource = CapabilitySource.API,
    content_hash: str | None = None,
) -> SeedApplyResult:
    """Apply an in-memory seed payload, used by the manual init path."""

    document = SeedDocument(
        path=Path("<manual>"),
        payload=payload,
        content_hash=content_hash or _stable_payload_hash(payload),
    )
    return await _apply_seed_documents([document], pool=pool, source=source)


async def _apply_seed_documents(
    documents: Sequence[SeedDocument],
    *,
    pool: Any,
    source: CapabilitySource,
) -> SeedApplyResult:
    for spec, _document in _iter_provider_specs(documents):
        _seed_credential_reference(spec)
        validate_provider_storage_payload(spec)

    cap_repo = CapabilityRepository(pool)
    provider_repo = ProviderRepository(pool)
    capabilities, capability_by_ref = await _apply_capabilities(
        documents=documents,
        cap_repo=cap_repo,
        source=source,
    )

    providers: list[Provider] = []
    for spec, document in _iter_provider_specs(documents):
        provider = await _provider_from_seed(
            spec,
            document=document,
            cap_repo=cap_repo,
            capability_by_ref=capability_by_ref,
            source=source,
        )
        existing = await provider_repo.get_by_name(provider.name)
        if existing is not None and existing.id != provider.id:
            raise SeedValidationError(
                f"provider {provider.name!r} already exists as {existing.id}; "
                "choose a different name or id"
            )
        providers.append(await provider_repo.create(provider))

    return SeedApplyResult(capabilities=capabilities, providers=providers)


async def _apply_capabilities(
    *,
    documents: Sequence[SeedDocument],
    cap_repo: CapabilityRepository,
    source: CapabilitySource,
) -> tuple[list[Capability], dict[str, Capability]]:
    capabilities: list[Capability] = []
    capability_by_ref: dict[str, Capability] = {}

    for spec, document in _iter_capability_specs(documents):
        name = _required_string(spec, "name", "capability.name")
        existing = await cap_repo.get_by_name(name)
        capability = _capability_from_seed(
            spec,
            document=document,
            source=source,
            existing_id=existing.id if existing is not None else None,
        )
        saved = await cap_repo.create(capability)
        capabilities.append(saved)
        capability_by_ref[saved.id] = saved
        capability_by_ref[saved.name] = saved

    return capabilities, capability_by_ref


def _validation_problems(exc: ValidationError) -> str:
    """``loc: msg`` lines; pydantic's own text includes input values, which can quote the file."""
    return "\n".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )


def _yaml_problem(exc: Exception, skipped: str = "") -> str:
    """A YAML error's class and position only: PyYAML's own text can quote the file.

    *skipped* is the leading whitespace removed before parsing; it shifts the reported position.
    """
    name = type(exc).__name__
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is not None:
        indent = len(skipped) - (skipped.rfind("\n") + 1) if mark.line == 0 else 0
        line = mark.line + skipped.count("\n") + 1
        return f"invalid YAML ({name}) at line {line}, column {mark.column + indent + 1}"
    position = getattr(exc, "position", None)
    where = f" at character {position + len(skipped) + 1}" if isinstance(position, int) else ""
    return f"invalid YAML ({name}){where}"


def _load_seed_payload(path: Path, text: str) -> Mapping[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}

    if stripped[0] in "[{":
        payload = json.loads(stripped)
    else:
        try:
            payload = yaml.safe_load(stripped)
        except (
            yaml.YAMLError,
            ValueError,
        ) as exc:  # reason: PyYAML's timestamp constructor raises a plain ValueError
            skipped = text[: len(text) - len(text.lstrip())]
            raise SeedValidationError(f"{path}: {_yaml_problem(exc, skipped)}") from None

    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise SeedValidationError(f"{path}: seed root must be an object")
    return payload


def _iter_capability_specs(
    documents: Sequence[SeedDocument],
) -> Iterable[tuple[Mapping[str, Any], SeedDocument]]:
    for document in documents:
        payload = document.payload
        raw_items = payload.get("capabilities")
        if raw_items is None and _looks_like_capability(payload):
            raw_items = [payload]
        if raw_items is None:
            continue
        if not isinstance(raw_items, list):
            raise SeedValidationError(f"{document.path}: capabilities must be a list")
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise SeedValidationError(f"{document.path}: capability entries must be objects")
            yield item, document


def _iter_provider_specs(
    documents: Sequence[SeedDocument],
) -> Iterable[tuple[Mapping[str, Any], SeedDocument]]:
    for document in documents:
        payload = document.payload
        raw_items = payload.get("providers")
        if raw_items is None and _looks_like_provider(payload):
            raw_items = [payload]
        if raw_items is None:
            continue
        if not isinstance(raw_items, list):
            raise SeedValidationError(f"{document.path}: providers must be a list")
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise SeedValidationError(f"{document.path}: provider entries must be objects")
            yield item, document


def _looks_like_capability(payload: Mapping[str, Any]) -> bool:
    return "name" in payload and ("class" in payload or "capability_class" in payload)


def _looks_like_provider(payload: Mapping[str, Any]) -> bool:
    provider_keys = {"endpoint_id", "runpod_endpoint_id", "provider_type", "type"}
    return "name" in payload and bool(provider_keys.intersection(payload))


def _capability_from_seed(
    spec: Mapping[str, Any],
    *,
    document: SeedDocument,
    source: CapabilitySource,
    existing_id: str | None,
) -> Capability:
    now = dt.datetime.now(dt.UTC)
    name = _required_string(spec, "name", "capability.name")
    class_value = _string_choice(
        _first_present(spec, ("class", "class_", "capability_class"), default="custom"),
        CapabilityClass,
        "capability.class",
    )
    cost_mode = _string_choice(
        _first_present(spec, ("cost_mode", "costMode"), default="per_second"),
        CostMode,
        "capability.cost_mode",
    )
    hints = [
        _string_choice(hint, CapabilityHint, "capability.hints_supported")
        for hint in _list_value(spec.get("hints_supported", []), "capability.hints_supported")
    ]
    try:
        return Capability(
            id=_optional_string(spec.get("id")) or existing_id or _id_from_name("cap", name),
            name=name,
            version=_optional_string(spec.get("version")) or "1.0.0",
            class_=class_value,
            description=_optional_string(spec.get("description")),
            input_schema=_dict_value(spec.get("input_schema", {}), "capability.input_schema"),
            output_schema=_dict_value(spec.get("output_schema", {}), "capability.output_schema"),
            defaults=CapabilityDefaults.model_validate(
                _dict_value(spec.get("defaults", {}), "capability.defaults")
            ),
            cost_mode=cost_mode,
            hints_supported=hints,
            source=source,
            last_applied_yaml_hash=document.content_hash
            if source == CapabilitySource.YAML
            else None,
            served_model_id=_optional_string(
                _first_present(spec, ("served_model_id", "servedModelId"))
            ),
            enabled=True,
            created_at=now,
            updated_at=now,
        )
    except ValidationError as exc:
        problems = _validation_problems(exc)
        raise SeedValidationError(f"{document.path}: capability {name!r}: {problems}") from None


async def _provider_from_seed(
    spec: Mapping[str, Any],
    *,
    document: SeedDocument,
    cap_repo: CapabilityRepository,
    capability_by_ref: Mapping[str, Capability],
    source: CapabilitySource,
) -> Provider:
    now = dt.datetime.now(dt.UTC)
    name = _required_string(spec, "name", "provider.name")
    provider_type_name, adapter_name, declaration = _resolve_declaration(spec)
    credential_ref = _seed_credential_reference(spec)
    gpu_class = (
        validate_canonical_gpu_name(_required_string(spec, "gpu_class", "provider.gpu_class"))
        if declaration.requires_gpu_class
        else None
    )
    endpoint_id = _optional_string(_first_present(spec, ("endpoint_id", "runpod_endpoint_id")))
    capability = await _resolve_capability(
        spec, cap_repo=cap_repo, capability_by_ref=capability_by_ref
    )
    cloud_type = _optional_string(spec.get("cloud_type"))
    config = _provider_config(
        spec, declaration=declaration, provider_type=provider_type_name, endpoint_id=endpoint_id
    )
    if gpu_class is not None:
        config["gpu_class"] = gpu_class
    provider_type = ProviderType(provider_type_name)
    adapter_id = ProviderAdapterId(adapter_name)
    validate_provider_registration_config(
        provider_type=provider_type,
        endpoint_id=endpoint_id,
        cloud_type=cloud_type,
        config=config,
    )

    health_status = _optional_string(_first_present(spec, ("health_status", "health"))) or "unknown"
    if health_status not in _PROVIDER_HEALTH_STATUSES:
        allowed = ", ".join(sorted(_PROVIDER_HEALTH_STATUSES))
        raise SeedValidationError(f"provider.health_status must be one of: {allowed}")

    # Omit the field when absent so Provider's model validator selects the
    # adapter-specific default reference.
    credential_kwargs: dict[str, str] = {}
    if credential_ref is not None:
        credential_kwargs["credential_ref"] = credential_ref
    try:
        return Provider(
            id=_optional_string(spec.get("id")) or _id_from_name("prov", name),
            capability_id=capability.id,
            name=name,
            adapter_id=adapter_id,
            provider_type=provider_type,
            runpod_endpoint_id=endpoint_id,
            runpod_template_id=_optional_string(spec.get("runpod_template_id")),
            region=_optional_string(spec.get("region")),
            cloud_type=cloud_type,
            config=config,
            priority=_int_value(spec.get("priority", 0), "provider.priority"),
            enabled=bool(spec.get("enabled", True)),
            health_status=health_status,
            consecutive_failures=0,
            cooldown_trips=0,
            cold_start_p50_ms=None,
            cold_start_p95_ms=None,
            recent_error_rate=0.0,
            cooldown_until=None,
            source=source,
            last_applied_yaml_hash=document.content_hash
            if source == CapabilitySource.YAML
            else None,
            updated_at=now,
            **credential_kwargs,
        )
    except ValidationError as exc:
        problems = _validation_problems(exc)
        raise SeedValidationError(f"{document.path}: provider {name!r}: {problems}") from None


def _seed_credential_reference(spec: Mapping[str, Any]) -> str | None:
    """Validate and return an explicit provider credential environment name."""

    raw = _first_present(spec, ("credential_ref", "credentialRef"))
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise SeedValidationError(
            "provider.credential_ref must be a valid environment-variable name"
        )
    value = _optional_string(raw)
    if value is None or not is_credential_reference_name(value):
        raise SeedValidationError(
            "provider.credential_ref must be a valid environment-variable name"
        )
    return value


async def _resolve_capability(
    spec: Mapping[str, Any],
    *,
    cap_repo: CapabilityRepository,
    capability_by_ref: Mapping[str, Capability],
) -> Capability:
    ref = _optional_string(_first_present(spec, ("capability", "capability_name", "capability_id")))
    if ref is None:
        raise SeedValidationError(
            "provider must include capability, capability_name, or capability_id"
        )
    if ref in capability_by_ref:
        return capability_by_ref[ref]

    if ref.startswith("cap_"):
        capability = await cap_repo.get(ref)
    else:
        capability = await cap_repo.get_by_name(ref)
    if capability is None:
        raise SeedValidationError(f"provider references unknown capability: {ref}")
    return capability


def _provider_config(
    spec: Mapping[str, Any],
    *,
    declaration: ProviderDeclaration,
    provider_type: str,
    endpoint_id: str | None,
) -> JsonObject:
    config = dict(_dict_value(spec.get("config", {}), "provider.config"))
    raw_cost = _first_present(spec, ("cost",), default=config.get("cost", {}))
    cost = dict(_dict_value(raw_cost, "provider.cost"))
    if "mode" not in cost:
        cost["mode"] = "per_second"
    config["cost"] = cost
    config["workers"] = dict(
        _dict_value(
            _first_present(spec, ("workers",), default=config.get("workers", {"workers_min": 0})),
            "provider.workers",
        )
    )
    config["idle_timeout_minutes"] = _int_value(
        _first_present(
            spec,
            ("idle_timeout_minutes", "idleTimeoutMinutes"),
            default=config.get("idle_timeout_minutes", 0),
        ),
        "provider.idle_timeout_minutes",
    )
    config["flash_boot_verified"] = bool(
        _first_present(
            spec,
            ("flash_boot_verified", "flashBootVerified"),
            default=config.get("flash_boot_verified", False),
        )
    )
    config["max_payload_mb"] = _int_value(
        _first_present(spec, ("max_payload_mb",), default=config.get("max_payload_mb", 30)),
        "provider.max_payload_mb",
    )
    config["request_timeout_s"] = _int_value(
        _first_present(
            spec,
            ("request_timeout_s",),
            default=config.get("request_timeout_s", 330),
        ),
        "provider.request_timeout_s",
    )
    if declaration.seed_config is not None:
        try:
            return declaration.seed_config(spec, config, provider_type, endpoint_id)
        except SeedValidationError:
            raise
        except ValueError as exc:
            raise SeedValidationError(str(exc)) from exc
    return config


def _resolve_declaration(spec: Mapping[str, Any]) -> tuple[str, str, ProviderDeclaration]:
    """Resolve the provider type and adapter a seed row names through the adapter registry."""

    registry = get_default_registry()
    known_types = {
        provider_type
        for _, declaration in registry.declarations()
        for provider_type in declaration.provider_types
    }
    provider_type = _optional_string(
        _first_present(spec, ("provider_type", "type"), required=True, field_name="provider.type")
    )
    if provider_type not in known_types:
        raise SeedValidationError(
            f"provider.provider_type must be one of: {', '.join(sorted(known_types))}"
        )
    adapter_id = _optional_string(
        _first_present(spec, ("adapter", "adapter_id"), default=ProviderAdapterId.RUNPOD.value)
    )
    if adapter_id not in registry.ids:
        raise SeedValidationError(
            f"provider.adapter must be one of: {', '.join(sorted(registry.ids))}"
        )
    owner = registry.declaration_for_type(provider_type)
    if owner is None:  # unreachable: the type came from a registered declaration
        raise SeedValidationError(f"provider.provider_type {provider_type!r} has no adapter")
    if owner.dedicated_provider_type and registry.declaration_for_adapter(adapter_id) is not owner:
        owner_id = next(id_ for id_, decl in registry.declarations() if decl is owner)
        raise SeedValidationError(f"{provider_type} providers require adapter: {owner_id}")
    return provider_type, adapter_id, owner


def _first_present(
    spec: Mapping[str, Any],
    names: Sequence[str],
    *,
    default: Any = None,
    required: bool = False,
    field_name: str | None = None,
) -> Any:
    for name in names:
        if name in spec:
            return spec[name]
    if required:
        raise SeedValidationError(f"{field_name or names[0]} is required")
    return default


def _required_string(spec: Mapping[str, Any], key: str, field_name: str) -> str:
    value = _optional_string(spec.get(key))
    if value is None:
        raise SeedValidationError(f"{field_name} is required")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_choice(value: object, enum_type: type[Any], field_name: str) -> Any:
    text = _optional_string(value)
    if text is None:
        raise SeedValidationError(f"{field_name} is required")
    try:
        return enum_type(text)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise SeedValidationError(f"{field_name} must be one of: {allowed}") from exc


def _dict_value(value: object, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SeedValidationError(f"{field_name} must be an object")
    return dict(value)


def _list_value(value: object, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SeedValidationError(f"{field_name} must be a list")
    return list(value)


def _int_value(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise SeedValidationError(f"{field_name} must be an integer")
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise SeedValidationError(f"{field_name} must be an integer") from exc


def _bool_value(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise SeedValidationError(f"{field_name} must be a boolean")
    return value


def _id_from_name(prefix: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        raise SeedValidationError(f"{prefix} id cannot be generated from an empty name")
    return f"{prefix}_{slug}"


def _stable_payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
