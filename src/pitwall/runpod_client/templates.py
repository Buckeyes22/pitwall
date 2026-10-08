"""RunPod template lifecycle — create once per full config, cache locally.

Template helpers create + cache RunPod templates so that repeated launches
reuse the same template rather than recreating it each time.

Also provides get/update/delete for managing existing templates and Hub
(public marketplace) template discovery for capabilities to reuse curated
pod+endpoint templates.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import asyncpg
import httpx
from pydantic import BaseModel, ConfigDict, Field

from pitwall.runpod_client.graphql import (
    RUNPOD_GRAPHQL_URL,
    RunpodGraphQLClient,
    RunpodGraphQLError,
    RunpodGraphQLResponseError,
)
from pitwall.runpod_client.pods import (
    RunPodError,
    RunPodRestError,
    _rest_base_url,
    _rest_headers,
    _sdk,
    argv_to_v2_args,
)
from pitwall.runpod_client.registry import registry_auth_id_from_env
from pitwall.runpod_credentials import resolve_runpod_api_key
from pitwall.security.redaction import redact_text

log = logging.getLogger("pitwall.runpod_client.templates")


TEMPLATE_NAME = "pitwall-cloud-worker"
DEFAULT_CONTAINER_DISK_GB = 50
_ACCOUNT_DIGEST_CONTEXT = b"pitwall-runpod-template-account-v1"
_REST_TIMEOUT_S = 60.0

TEMPLATE_ENV_KEYS = (
    "REDIS_URL",
    "PITWALL_CAPABILITY",
    "PITWALL_CAPABILITY_ID",
    "PITWALL_CAPABILITY_NAME",
    "PITWALL_PROVIDER",
    "PITWALL_PROVIDER_ID",
    "PITWALL_PROVIDER_NAME",
    "PITWALL_PROVIDER_TYPE",
    "PITWALL_REQUEST_ID",
    "VLLM_MODEL",
    "R2_ENDPOINT",
    "R2_BUCKET_STAGING",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION",
    "R2_SESSION_TOKEN",
    "R2_CREDENTIAL_TTL_SECONDS",
    "R2_CREDENTIAL_EXPIRES_AT",
)
_GRAPHQL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SECRET_ENV_KEY_MARKERS = ("API_KEY", "CREDENTIAL", "PASSWORD", "SECRET", "TOKEN")
_IMAGE_REF_RE = re.compile(
    r"^(?:(?:localhost|[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*)(?::[0-9]{1,5})?/)?"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:[0-9a-f]{64})?$"
)


async def _rest_request_async(
    method: str,
    path: str,
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
    json_body: dict[str, Any] | None = None,
    timeout_s: float = _REST_TIMEOUT_S,
) -> Any:
    """Call the RunPod REST API with process defaults or explicit auth overrides."""
    url = f"{_rest_base_url(rest_api_url)}/{path.lstrip('/')}"
    headers = _rest_headers(api_key)
    secret = headers["Authorization"].removeprefix("Bearer ")
    safe_to_repeat = method.upper() in {"GET", "DELETE", "PATCH"}
    response: httpx.Response | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                )
        except httpx.HTTPError:
            if not safe_to_repeat or attempt:
                raise
            continue
        retryable_status = response.status_code == 429 or (
            safe_to_repeat and response.status_code >= 500
        )
        if not retryable_status or attempt:
            break
    if response is None:
        raise RunPodError(f"{method} {path} returned no response")
    if response.status_code == 204:
        return {}
    if response.status_code >= 400:
        raise RunPodRestError(
            method,
            path,
            response.status_code,
            redact_text(response.text[:4096], secrets=(secret,)),
        )
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise RunPodError(f"{method} {path} returned a non-JSON response") from exc


def validate_image_ref(image_ref: str) -> str:
    """Return a credential-free Docker image reference or raise ``ValueError``."""
    if not _IMAGE_REF_RE.fullmatch(image_ref):
        raise ValueError("invalid Docker image reference")
    return image_ref


def image_sha(image_ref: str) -> str:
    """Extract the tag/digest portion of ``repo:tag`` or ``repo@sha256:...``."""
    if "@sha256:" in image_ref:
        return image_ref.split("@sha256:")[-1]
    if ":" in image_ref.rsplit("/", 1)[-1]:
        return image_ref.rsplit(":", 1)[-1]
    return "latest"


def non_secret_env_keys(env: Mapping[str, object] | Iterable[str]) -> tuple[str, ...]:
    """Return sorted environment key names safe for template identity metadata."""
    keys = env.keys() if isinstance(env, Mapping) else env
    return tuple(
        sorted(
            {
                key
                for key in keys
                if not any(marker in key.upper() for marker in _SECRET_ENV_KEY_MARKERS)
            }
        )
    )


def template_account_digest(api_key: str | None = None) -> str:
    """A keyed digest naming the RunPod account that owns a template, never the key.

    HMAC-SHA256 keyed by SHA-256 of a fixed context string plus the API key (the key is
    the explicit one, else the resolved process key), as ``default_launch_fingerprint``
    does for launch credentials, so the digest is stable per account and the key cannot be
    recovered from it. Empty when no key resolves; the create then fails on its own.
    """
    key = api_key or resolve_runpod_api_key(os.environ)[0]
    if not key:
        return ""
    digest_key = hashlib.sha256(_ACCOUNT_DIGEST_CONTEXT + key.encode("utf-8")).digest()
    return hmac.new(digest_key, b"account", hashlib.sha256).hexdigest()


def config_sha(
    image_ref: str,
    *,
    docker_entrypoint: Sequence[str] = (),
    docker_start_cmd: Sequence[str] = (),
    ports: str | None = None,
    env_keys: Iterable[str] = (),
    container_disk_gb: int = DEFAULT_CONTAINER_DISK_GB,
    registry_auth_id: str | None = None,
    account_digest: str = "",
) -> str:
    """Hash generated-template inputs with the complete image reference.

    ``image_ref`` is included verbatim as a canonical JSON field: repository
    plus tag or digest. Docker registry aliases are not normalized, so distinct
    input spellings intentionally have distinct template identities. Only
    environment key names are accepted; environment values cannot enter the
    hash. ``docker_start_cmd`` is template identity because RunPod templates
    reuse it; secrets must never be passed as start arguments.

    A RunPod template belongs to one account and carries its container disk size and
    registry auth id, so those enter the hash too: ``account_digest`` is the keyed digest
    from ``template_account_digest`` (never the API key itself).
    """
    validate_image_ref(image_ref)
    config = {
        "account": account_digest,
        "container_disk_gb": container_disk_gb,
        "docker_entrypoint": list(docker_entrypoint),
        "docker_start_cmd": list(docker_start_cmd),
        "env_keys": sorted(set(env_keys)),
        "image_ref": image_ref,
        "ports": ports or "",
        "registry_auth_id": registry_auth_id or "",
    }
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def template_suffix(
    image_ref: str,
    *,
    docker_entrypoint: Sequence[str] = (),
    docker_start_cmd: Sequence[str] = (),
    ports: str | None = None,
    env_keys: Iterable[str] = (),
    container_disk_gb: int = DEFAULT_CONTAINER_DISK_GB,
    registry_auth_id: str | None = None,
) -> str:
    """Stable short suffix derived from the generated-template config.

    RunPod requires a unique template name within one account, so the suffix covers the
    disk size and registry auth id as well; the account is the namespace and stays out of it.
    """
    return config_sha(
        image_ref,
        docker_entrypoint=docker_entrypoint,
        docker_start_cmd=docker_start_cmd,
        ports=ports,
        env_keys=env_keys,
        container_disk_gb=container_disk_gb,
        registry_auth_id=registry_auth_id,
    )[:12]


def normalize_template_name(name: str) -> str:
    """Return a RunPod-friendly stable template name."""
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name.strip()).strip("-")
    return normalized or TEMPLATE_NAME


def template_display_name(
    template_name: str,
    image_ref: str,
    *,
    docker_entrypoint: Sequence[str] = (),
    docker_start_cmd: Sequence[str] = (),
    ports: str | None = None,
    env_keys: Iterable[str] = (),
    container_disk_gb: int = DEFAULT_CONTAINER_DISK_GB,
    registry_auth_id: str | None = None,
) -> str:
    """Return the visible RunPod "My Templates" name for this config."""
    suffix = template_suffix(
        image_ref,
        docker_entrypoint=docker_entrypoint,
        docker_start_cmd=docker_start_cmd,
        ports=ports,
        env_keys=env_keys,
        container_disk_gb=container_disk_gb,
        registry_auth_id=registry_auth_id,
    )
    return f"{normalize_template_name(template_name)}-{suffix}"


def _is_duplicate_template_name_error(exc: BaseException) -> bool:
    """True if a create_template failure is a RunPod template-name collision.

    RunPod rejects a duplicate display name with a "Template name must be unique"
    GraphQL error (surfaced as runpod.error.QueryError). Matched on the message
    rather than the type so SDK-version phrasing differences still classify.
    """
    message = str(exc).lower()
    return "unique" in message and "name" in message


def _resolve_existing_template_id(
    pod_templates: list[dict[str, Any]], display_name: str
) -> str | None:
    """Return the RunPod template id whose name == display_name, else None."""
    for entry in pod_templates:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == display_name and entry.get("id"):
            return str(entry["id"])
    return None


def _sdk_kwargs(api_key: str | None) -> dict[str, str]:
    return {"api_key": api_key} if api_key is not None else {}


def _custom_graphql_url(graphql_url: str | None) -> str | None:
    if graphql_url is None:
        return None
    normalized = graphql_url.rstrip("/")
    if normalized == RUNPOD_GRAPHQL_URL.rstrip("/"):
        return None
    return normalized


def _list_my_templates(api_key: str | None = None) -> list[dict[str, Any]]:
    """Return the account's RunPod pod templates as ``[{id, name}, ...]``.

    The runpod SDK exposes no list-templates call, so query GraphQL directly.
    ``_sdk()`` sets the module-level ``runpod.api_key`` that ``run_graphql_query``
    reads. Blocking (uses ``requests``); callers should run it in a thread.
    """
    _sdk(**_sdk_kwargs(api_key))
    import runpod.api.graphql as rp_graphql  # type: ignore[import-untyped]  # reason: no stubs

    response = dict(rp_graphql.run_graphql_query("query { myself { podTemplates { id name } } }"))
    myself = _graphql_data(response).get("myself") or {}
    pod_templates = myself.get("podTemplates") or []
    return [entry for entry in pod_templates if isinstance(entry, dict)]


class TemplateEnvVar(BaseModel):
    """Key-value pair for a template environment variable."""

    key: str
    value: str


class V2TemplatePersistentMount(BaseModel):
    """Strict REST v2 persistent mount for a template."""

    model_config = ConfigDict(extra="forbid")

    size: int = Field(ge=10)
    path: str = Field(min_length=1)


class V2TemplateMounts(BaseModel):
    """Strict REST v2 template mounts (persistent only)."""

    model_config = ConfigDict(extra="forbid")

    persistent: V2TemplatePersistentMount | None = None


class V2CreateTemplateRequest(BaseModel):
    """Strict REST v2 template-create request."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    image: str = Field(min_length=1)
    args: str | None = None
    disk: int = Field(ge=10)
    env: dict[str, str] = Field(default_factory=dict)
    mounts: V2TemplateMounts | None = None
    ports: list[str] = Field(default_factory=list)
    registry: str | None = None
    serverless: bool = False
    public: bool = False
    start_jupyter: bool = Field(default=False, alias="startJupyter")
    start_ssh: bool = Field(default=False, alias="startSsh")


class V2UpdateTemplateRequest(BaseModel):
    """Strict REST v2 template-update request."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str | None = Field(default=None, min_length=1)
    image: str | None = Field(default=None, min_length=1)
    args: str | None = None
    disk: int | None = Field(default=None, ge=10)
    env: dict[str, str] | None = None
    mounts: V2TemplateMounts | None = None
    ports: list[str] | None = None
    registry: str | None = None
    serverless: bool | None = None
    public: bool | None = None


class Template(BaseModel):
    """A RunPod pod template.

    Attributes:
        id: RunPod template ID.
        name: Display name of the template.
        image_name: Docker image reference (e.g. ``ghcr.io/org/worker:tag``).
        docker_args: Command to start the Docker container.
        container_disk_in_gb: Container disk size in GB.
        volume_in_gb: Volume size in GB.
        volume_mount_path: Path where the volume is mounted.
        ports: Port mappings string (e.g. ``"8888/http,666/tcp"``).
        env: Environment variables set on the template.
        is_serverless: Whether this is a serverless template.
        is_public: Whether this template is publicly visible in the Hub.
        readme: Template description/markdown.
    """

    model_config = {"populate_by_name": True}

    id: str
    name: str
    image_name: str = Field(validation_alias="imageName")
    docker_args: str | None = Field(default=None, validation_alias="dockerArgs")
    container_disk_in_gb: int = Field(default=10, validation_alias="containerDiskInGb")
    volume_in_gb: int = Field(default=0, validation_alias="volumeInGb")
    volume_mount_path: str | None = Field(default=None, validation_alias="volumeMountPath")
    ports: str = Field(default="", validation_alias="ports")
    env: list[TemplateEnvVar] | None = Field(default=None, validation_alias="env")
    is_serverless: bool = Field(default=False, validation_alias="isServerless")
    is_public: bool = Field(default=False, validation_alias="isPublic")
    registry_auth_id: str | None = Field(default=None, validation_alias="registry")
    readme: str = Field(default="", validation_alias="readme")


class HubTemplate(BaseModel):
    """A public Hub (marketplace) RunPod template.

    Hub templates are read-only community-curated templates that capabilities
    can reuse when launching pods or endpoints.

    Attributes:
        id: RunPod template ID.
        name: Display name of the template.
        image_name: Docker image reference.
        description: Short description of the template.
        github_url: Link to the template's source repository.
        docker_args: Command to start the Docker container.
        container_disk_in_gb: Container disk size in GB.
        volume_in_gb: Volume size in GB.
        volume_mount_path: Path where the volume is mounted.
        ports: Port mappings string.
        env: Environment variables set on the template.
        is_serverless: Whether this is a serverless template.
        display_name: User-facing name shown in the Hub UI.
        template_description: Longer description or README content.
    """

    model_config = {"populate_by_name": True}

    id: str
    name: str
    image_name: str = Field(validation_alias="imageName")
    description: str | None = Field(default=None, validation_alias="description")
    github_url: str | None = Field(default=None, validation_alias="githubUrl")
    docker_args: str | None = Field(default=None, validation_alias="dockerArgs")
    container_disk_in_gb: int = Field(default=10, validation_alias="containerDiskInGb")
    volume_in_gb: int = Field(default=0, validation_alias="volumeInGb")
    volume_mount_path: str | None = Field(default=None, validation_alias="volumeMountPath")
    ports: str = Field(default="", validation_alias="ports")
    env: list[TemplateEnvVar] | None = Field(default=None, validation_alias="env")
    is_serverless: bool = Field(default=False, validation_alias="isServerless")
    display_name: str | None = Field(default=None, validation_alias="displayName")
    template_description: str | None = Field(default=None, validation_alias="templateDescription")


def _run_graphql(query: str, *, api_key: str | None = None) -> dict[str, Any]:
    """Execute a GraphQL query/mutation and return the response data.

    Uses ``runpod.api_key`` set by ``_sdk()``. Blocking; callers should run
    in a thread.
    """
    _sdk(**_sdk_kwargs(api_key))
    import requests
    import runpod.api.graphql as rp_graphql
    import runpod.error as rp_error  # type: ignore[import-untyped]  # reason: no stubs

    try:
        response: Any = rp_graphql.run_graphql_query(query)
    except (requests.RequestException, rp_error.RunPodError) as exc:
        # The SDK raises its own and ``requests`` exception types; callers map RunPodError.
        raise RunPodError(f"RunPod GraphQL request failed: {type(exc).__name__}") from exc
    return dict(response)


def _graphql_api_key(api_key: str | None) -> str:
    if api_key is not None:
        return api_key
    return resolve_runpod_api_key(os.environ)[0] or ""


async def _run_graphql_client(
    query: str,
    *,
    api_key: str | None,
    graphql_url: str,
) -> dict[str, Any]:
    client = RunpodGraphQLClient(
        api_key=_graphql_api_key(api_key),
        graphql_url=graphql_url,
    )
    try:
        return await client._graphql(query)
    finally:
        await client.aclose()


def _graphql_string(value: str) -> str:
    return json.dumps(value)


def _graphql_id(value: str, *, field_name: str) -> str:
    if not _GRAPHQL_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} contains invalid characters")
    return _graphql_string(value)


def _graphql_data(envelope: dict[str, Any]) -> dict[str, Any]:
    raw_errors = envelope.get("errors")
    if isinstance(raw_errors, list) and raw_errors:
        errors: list[dict[str, Any]] = []
        for raw_error in raw_errors:
            if isinstance(raw_error, dict):
                errors.append(raw_error)
            else:
                errors.append({"message": str(raw_error)})
        raise RunpodGraphQLError(errors)

    data = envelope.get("data")
    if not isinstance(data, dict):
        raise RunpodGraphQLResponseError("RunPod GraphQL response missing data object")
    return data


def _template_selection() -> str:
    """GraphQL fragment for full template fields."""
    return """id name imageName dockerArgs containerDiskInGb volumeInGb
    volumeMountPath ports env { key value } isServerless isPublic readme"""


def _hub_template_selection() -> str:
    """GraphQL fragment for community template fields.

    RunPod removed the ``hubPodTemplates`` surface (and its ``description``,
    ``githubUrl``, ``displayName``, ``templateDescription`` fields) from the
    GraphQL schema in 2026-07; community templates are now served by
    ``podTemplates``/``podTemplate`` with this reduced selection.
    """
    return """id name imageName dockerArgs
    containerDiskInGb volumeInGb volumeMountPath ports env { key value }
    isServerless"""


def _template_path_id(template_id: str) -> str:
    """Return a safe REST path component for a RunPod template ID."""
    normalized = template_id.strip().strip("/")
    if not normalized:
        raise ValueError("template_id must be non-empty")
    if "/" in normalized:
        raise ValueError("template_id must not contain path separators")
    return normalized


def _template_from_rest(data: dict[str, Any]) -> dict[str, Any]:
    """Map a REST template response onto the aliases accepted by ``Template``."""
    template = dict(data)
    if "imageName" not in template and "image" in template:
        template["imageName"] = template.get("image")
    if "dockerArgs" not in template and "args" in template:
        template["dockerArgs"] = template.get("args")
    if "containerDiskInGb" not in template and "disk" in template:
        template["containerDiskInGb"] = template.get("disk")
    if "isServerless" not in template and "serverless" in template:
        template["isServerless"] = template.get("serverless")
    if "isPublic" not in template and "public" in template:
        template["isPublic"] = template.get("public")
    mounts = template.get("mounts")
    if isinstance(mounts, dict):
        persistent = mounts.get("persistent")
        if isinstance(persistent, dict):
            template["volumeInGb"] = persistent.get("size", 0)
            template["volumeMountPath"] = persistent.get("path")
    ports = template.get("ports")
    if isinstance(ports, list):
        template["ports"] = ",".join(str(port) for port in ports)
    env = template.get("env")
    if isinstance(env, dict):
        template["env"] = [{"key": key, "value": str(value)} for key, value in env.items()]
    return template


def _template_create_payload(
    *,
    name: str,
    image_name: str,
    container_disk_in_gb: int,
    volume_in_gb: int,
    volume_mount_path: str | None,
    docker_start_cmd: Sequence[str],
    env: Mapping[str, str] | None,
    is_serverless: bool,
    registry_auth_id: str | None,
    ports: Sequence[str] | None,
) -> dict[str, Any]:
    """Build and validate the strict REST v2 ``POST /templates`` body."""

    mounts: V2TemplateMounts | None = None
    if volume_in_gb:
        mounts = V2TemplateMounts(
            persistent=V2TemplatePersistentMount(
                size=volume_in_gb,
                path=volume_mount_path or "/workspace",
            )
        )
    request = V2CreateTemplateRequest(
        name=name,
        image=image_name,
        args=(argv_to_v2_args(docker_start_cmd) if docker_start_cmd else None),
        disk=container_disk_in_gb,
        env=dict(env or {}),
        mounts=mounts,
        serverless=is_serverless,
        registry=registry_auth_id,
        ports=list(ports or []),
    )
    return request.model_dump(by_alias=True, exclude_none=True)


async def create_template_rest(
    *,
    name: str,
    image_name: str,
    container_disk_in_gb: int,
    volume_in_gb: int = 0,
    volume_mount_path: str | None,
    docker_entrypoint: Sequence[str] = (),
    docker_start_cmd: Sequence[str] = (),
    env: Mapping[str, str] | None,
    is_serverless: bool,
    registry_auth_id: str | None,
    ports: Sequence[str] | None,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> str:
    """Create a RunPod template through the REST API and return its ID."""
    if docker_entrypoint:
        raise RunPodError(
            "RunPod REST v2 templates do not support docker_entrypoint; "
            "remove the override before creating the template"
        )
    data = await _rest_request_async(
        "POST",
        "templates",
        api_key=api_key,
        rest_api_url=rest_api_url,
        json_body=_template_create_payload(
            name=name,
            image_name=image_name,
            container_disk_in_gb=container_disk_in_gb,
            volume_in_gb=volume_in_gb,
            volume_mount_path=volume_mount_path,
            docker_start_cmd=docker_start_cmd,
            env=env,
            is_serverless=is_serverless,
            registry_auth_id=registry_auth_id,
            ports=ports,
        ),
    )
    if not isinstance(data, dict) or not data.get("id"):
        raise RunPodError(f"create template returned unexpected shape: {data!r}")
    return str(data["id"])


async def _list_my_templates_for_auth(
    *,
    api_key: str | None,
    graphql_url: str | None,
    rest_api_url: str | None = None,
) -> list[dict[str, Any]]:
    custom_url = _custom_graphql_url(graphql_url)
    if custom_url is None:
        data = await _rest_request_async(
            "GET",
            "templates",
            api_key=api_key,
            rest_api_url=rest_api_url,
        )
        if not isinstance(data, dict) or not isinstance(data.get("templates"), list):
            raise RunPodError("GET templates returned an invalid REST v2 templates envelope")
        return [entry for entry in data["templates"] if isinstance(entry, dict)]

    data = await _run_graphql_client(
        "query { myself { podTemplates { id name } } }",
        api_key=api_key,
        graphql_url=custom_url,
    )
    myself = data.get("myself") or {}
    pod_templates = myself.get("podTemplates") if isinstance(myself, dict) else []
    return [entry for entry in (pod_templates or []) if isinstance(entry, dict)]


async def list_account_templates(
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> list[Template]:
    """Return the account-owned templates from the strict REST v2 envelope.

    This is deliberately separate from :func:`list_hub_templates`: account
    templates are mutable operator resources, while Hub templates are public,
    read-only catalogue entries.
    """
    data = await _rest_request_async(
        "GET",
        "templates",
        api_key=api_key,
        rest_api_url=rest_api_url,
    )
    if not isinstance(data, dict) or not isinstance(data.get("templates"), list):
        raise RunPodError("GET templates returned an invalid REST v2 templates envelope")
    if not all(isinstance(entry, dict) for entry in data["templates"]):
        raise RunPodError("GET templates returned an invalid REST v2 template item")
    return [Template.model_validate(_template_from_rest(entry)) for entry in data["templates"]]


async def get_template(
    template_id: str,
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> Template:
    """Fetch a single RunPod template by ID.

    Raises:
        TemplateNotFoundError: Only when RunPod answers HTTP 404.
        RunPodError: When RunPod answers 200 with an empty or non-object body.
    """
    try:
        data = await _rest_request_async(
            "GET",
            f"templates/{_template_path_id(template_id)}",
            api_key=api_key,
            rest_api_url=rest_api_url,
        )
    except RunPodRestError as exc:
        if exc.status_code == 404:
            raise TemplateNotFoundError(f"Template {template_id!r} not found") from exc
        raise
    if not isinstance(data, dict) or not data:
        # Only a 404 means absent. An empty or non-object 200 is a provider fault: callers that
        # release an idempotency key on not-found would otherwise recreate a live template.
        raise RunPodError(f"GET template {template_id!r} returned an unusable response body")
    return Template.model_validate(_template_from_rest(data))


class TemplateNotFoundError(RuntimeError):
    """Raised when a RunPod template ID does not exist."""


class TemplateDeleteError(RuntimeError):
    """Raised when a RunPod template cannot be deleted."""


async def update_template(
    template_id: str,
    *,
    name: str | None = None,
    image_name: str | None = None,
    docker_args: str | None = None,
    container_disk_in_gb: int | None = None,
    volume_in_gb: int | None = None,
    volume_mount_path: str | None = None,
    ports: str | None = None,
    env: dict[str, str] | None = None,
    is_serverless: bool | None = None,
    is_public: bool | None = None,
    readme: str | None = None,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> Template:
    """Update a RunPod template's properties.

    Args:
        template_id: The RunPod template ID to update.
        name: New display name for the template.
        image_name: New Docker image reference.
        docker_args: New container start command.
        container_disk_in_gb: New container disk size in GB.
        volume_in_gb: New volume size in GB.
        volume_mount_path: New volume mount path.
        ports: New port mappings string.
        env: New environment variables dict (replaces existing).
        is_serverless: Whether this is a serverless template.
        is_public: Whether this template is publicly visible.
        readme: New template description/markdown.

    Returns:
        Template populated with the updated RunPod template data.

    Raises:
        TemplateNotFoundError: If the template ID does not exist.
    """
    if readme is not None:
        raise RunPodError(
            "RunPod REST v2 does not support template readme updates; remove readme before retrying"
        )

    mounts: V2TemplateMounts | None = None
    if volume_in_gb is not None or volume_mount_path is not None:
        existing = await get_template(
            template_id,
            api_key=api_key,
            rest_api_url=rest_api_url,
        )
        resolved_size = volume_in_gb if volume_in_gb is not None else existing.volume_in_gb
        resolved_path = (
            volume_mount_path
            if volume_mount_path is not None
            else existing.volume_mount_path or "/workspace"
        )
        if resolved_size < 10:
            raise RunPodError(
                "RunPod REST v2 template mounts require volume_in_gb >= 10; "
                "supply a valid size with volume_mount_path"
            )
        mounts = V2TemplateMounts(
            persistent=V2TemplatePersistentMount(size=resolved_size, path=resolved_path)
        )

    request = V2UpdateTemplateRequest(
        name=name,
        image=image_name,
        args=docker_args,
        disk=container_disk_in_gb,
        mounts=mounts,
        ports=(
            [port.strip() for port in ports.split(",") if port.strip()]
            if ports is not None
            else None
        ),
        env=env,
        serverless=is_serverless,
        public=is_public,
    )
    payload = request.model_dump(by_alias=True, exclude_none=True)
    if not payload:
        raise RunPodError(f"update_template({template_id}): at least one field must be supplied")
    try:
        data = await _rest_request_async(
            "PATCH",
            f"templates/{_template_path_id(template_id)}",
            api_key=api_key,
            rest_api_url=rest_api_url,
            json_body=payload,
        )
    except RunPodRestError as exc:
        if exc.status_code == 404:
            raise TemplateNotFoundError(f"Template {template_id!r} not found") from exc
        raise
    if not isinstance(data, dict) or not data:
        raise RunPodError(f"update template returned unexpected shape: {data!r}")
    return Template.model_validate(_template_from_rest(data))


async def delete_template(
    template_id: str,
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> bool:
    """Delete a RunPod template by ID. Returns True when it is gone."""
    try:
        await _rest_request_async(
            "DELETE",
            f"templates/{_template_path_id(template_id)}",
            api_key=api_key,
            rest_api_url=rest_api_url,
        )
    except RunPodRestError as exc:
        if exc.status_code == 404:
            return True
        raise TemplateDeleteError(f"Failed to delete template {template_id!r}: {exc}") from exc
    return True


async def list_hub_templates(
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[HubTemplate]:
    """List public Hub (marketplace) RunPod templates.

    Hub templates are community-curated templates that capabilities can reuse
    for pod or endpoint launches without creating their own.

    Args:
        limit: Maximum number of templates to return (default 50, max 100).
        offset: Number of templates to skip for pagination.

    Returns:
        List of HubTemplate objects.
    """
    clamped_limit = min(max(1, limit), 100)
    # podTemplates takes no pagination arguments; slice client-side.
    query = f"""query {{
        podTemplates {{
            {_hub_template_selection()}
        }}
    }}"""
    result = await asyncio.to_thread(_run_graphql, query)
    data = _graphql_data(result)
    templates_data = data.get("podTemplates") or []
    page = templates_data[offset : offset + clamped_limit]
    return [HubTemplate.model_validate(t) for t in page if isinstance(t, dict)]


async def get_hub_template(template_id: str) -> HubTemplate:
    """Fetch a single public Hub template by ID.

    Args:
        template_id: The RunPod Hub template ID.

    Returns:
        HubTemplate populated with the template data.

    Raises:
        TemplateNotFoundError: If the Hub template does not exist.
    """
    query = f"""query {{
        podTemplate(id: {_graphql_id(template_id, field_name="template_id")}) {{
            {_hub_template_selection()}
        }}
    }}"""
    result = await asyncio.to_thread(_run_graphql, query)
    data = _graphql_data(result)
    template_data = data.get("podTemplate")
    if not template_data:
        raise TemplateNotFoundError(f"Hub template {template_id!r} not found")
    return HubTemplate.model_validate(template_data)


async def _lookup_cached(pool: asyncpg.Pool, name: str, sha: str) -> str | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT runpod_template_id FROM runpod_templates WHERE name=$1 AND config_sha=$2",
            name,
            sha,
        )
    return row["runpod_template_id"] if row else None


async def _insert_cache(
    pool: asyncpg.Pool,
    *,
    template_id: str,
    name: str,
    sha: str,
    image_ref: str,
    env_schema: tuple[str, ...],
    registry_auth_id: str | None,
    container_disk_gb: int = 50,
    volume_mount_path: str = "/workspace",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO runpod_templates
                (id, runpod_template_id, name, image_sha, config_sha, image_ref,
                 registry_auth_id, container_disk_gb, volume_mount_path, env_schema)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               ON CONFLICT (name, config_sha) DO NOTHING""",
            template_id,
            template_id,
            name,
            image_sha(image_ref),
            sha,
            image_ref,
            registry_auth_id,
            container_disk_gb,
            volume_mount_path,
            list(env_schema),
        )


async def ensure_template(
    pool: asyncpg.Pool,
    image_ref: str,
    *,
    template_name: str = TEMPLATE_NAME,
    registry_auth_id: str | None = None,
    container_disk_gb: int = DEFAULT_CONTAINER_DISK_GB,
    volume_mount_path: str = "/workspace",
    docker_entrypoint: Sequence[str] = (),
    docker_start_cmd: Sequence[str] = (),
    ports: str | None = None,
    env: Mapping[str, str] | None = None,
    api_key: str | None = None,
    graphql_url: str | None = None,
    rest_api_url: str | None = None,
) -> str:
    """Return the RunPod template_id for this image. Creates + caches if missing.

    ``image_ref``: full image path with tag or digest (e.g.
        ``ghcr.io/org/cloud-worker:abc123``).
    ``registry_auth_id``: RunPod's credential ID for the private-registry pull
        secret. Required for private registries; pass None for public images.
    """
    image = image_sha(image_ref)
    env_schema = non_secret_env_keys((*TEMPLATE_ENV_KEYS, *(env or {})))
    sha = config_sha(
        image_ref,
        docker_entrypoint=docker_entrypoint,
        docker_start_cmd=docker_start_cmd,
        ports=ports,
        env_keys=env_schema,
        container_disk_gb=container_disk_gb,
        registry_auth_id=registry_auth_id,
        account_digest=template_account_digest(api_key),
    )
    logical_name = normalize_template_name(template_name)
    cached = await _lookup_cached(pool, logical_name, sha)
    if cached:
        log.debug(
            "template cache hit: name=%s sha=%s template_id=%s",
            logical_name,
            sha,
            cached,
        )
        return cached

    env_block = dict.fromkeys((*TEMPLATE_ENV_KEYS, *(env or {})), "")

    display_name = template_display_name(
        logical_name,
        image_ref,
        docker_entrypoint=docker_entrypoint,
        docker_start_cmd=docker_start_cmd,
        ports=ports,
        env_keys=env_schema,
        container_disk_gb=container_disk_gb,
        registry_auth_id=registry_auth_id,
    )
    log.info("creating RunPod template: name=%s image=%s", display_name, redact_text(image_ref))
    custom_url = _custom_graphql_url(graphql_url)
    port_list = [port.strip() for port in ports.split(",") if port.strip()] if ports else []
    try:
        result = await create_template_rest(
            name=display_name,
            image_name=image_ref,
            container_disk_in_gb=container_disk_gb,
            volume_in_gb=0,
            volume_mount_path=volume_mount_path,
            docker_entrypoint=docker_entrypoint,
            docker_start_cmd=docker_start_cmd,
            env=env_block,
            is_serverless=False,
            registry_auth_id=registry_auth_id,
            ports=port_list,
            api_key=api_key,
            rest_api_url=rest_api_url,
        )
    except Exception as exc:  # reason: RunPod rejects a duplicate display name; reuse the existing template instead of failing the launch
        if not _is_duplicate_template_name_error(exc):
            raise
        log.info(
            "template name collision on RunPod (DB cache miss); resolving existing: name=%s",
            display_name,
        )
        pod_templates = await _list_my_templates_for_auth(
            api_key=api_key,
            graphql_url=custom_url,
            rest_api_url=rest_api_url,
        )
        existing_id = _resolve_existing_template_id(pod_templates, display_name)
        if not existing_id:
            raise
        await _insert_cache(
            pool,
            template_id=existing_id,
            name=logical_name,
            sha=sha,
            image_ref=image_ref,
            env_schema=env_schema,
            registry_auth_id=registry_auth_id,
            container_disk_gb=container_disk_gb,
            volume_mount_path=volume_mount_path,
        )
        log.info(
            "reused existing RunPod template by name: name=%s id=%s config_sha=%s",
            display_name,
            existing_id,
            sha,
        )
        return existing_id

    template_id = result

    await _insert_cache(
        pool,
        template_id=template_id,
        name=logical_name,
        sha=sha,
        image_ref=image_ref,
        env_schema=env_schema,
        registry_auth_id=registry_auth_id,
        container_disk_gb=container_disk_gb,
        volume_mount_path=volume_mount_path,
    )
    log.info(
        "template created + cached: name=%s id=%s config_sha=%s image_sha=%s",
        display_name,
        template_id,
        sha,
        image,
    )
    return template_id


def get_image_ref_from_env() -> str:
    """Return the image ref that the launcher should use.

    Read from env var ``PITWALL_CLOUD_WORKER_IMAGE``. Fail fast if unset — we
    don't want to silently default to ``latest`` which could pick up an unvetted
    image.
    """
    ref = os.environ.get("PITWALL_CLOUD_WORKER_IMAGE")
    if not ref:
        raise RuntimeError("PITWALL_CLOUD_WORKER_IMAGE not set")
    return ref


def get_registry_auth_id_from_env(image_ref: str | None = None) -> str | None:
    """Return the RunPod registry-auth ID for the given image, or None if public.

    Picks credentials by image prefix: GHCR, GitLab Registry, and Docker Hub
    can each use separate RunPod credential IDs. The legacy
    ``RUNPOD_REGISTRY_AUTH_ID`` fallback remains for existing GHCR callers.
    """
    return registry_auth_id_from_env(image_ref)


__all__ = [
    "TEMPLATE_NAME",
    "Template",
    "TemplateEnvVar",
    "HubTemplate",
    "TemplateNotFoundError",
    "TemplateDeleteError",
    "image_sha",
    "config_sha",
    "template_account_digest",
    "non_secret_env_keys",
    "normalize_template_name",
    "template_display_name",
    "template_suffix",
    "validate_image_ref",
    "ensure_template",
    "create_template_rest",
    "get_template",
    "list_account_templates",
    "update_template",
    "delete_template",
    "list_hub_templates",
    "get_hub_template",
    "get_image_ref_from_env",
    "get_registry_auth_id_from_env",
]
