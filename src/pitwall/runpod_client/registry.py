"""RunPod registry-auth selection helpers and container-registry credential CRUD.

RunPod stores registry credentials as auth IDs. Pitwall selects the auth ID
from the image reference prefix so GHCR, GitLab Registry, and Docker Hub can be
configured independently.

The CRUD surface manages container-registry credentials via RunPod's REST v2 API
at ``https://api.runpod.io/v2/registries``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from pitwall.runpod_credentials import (
    DEFAULT_RUNPOD_REST_URL,
    MISSING_CREDENTIAL_MESSAGE,
    resolve_runpod_api_key,
)
from pitwall.security.redaction import redact_text

LEGACY_REGISTRY_AUTH_ENV = "RUNPOD_REGISTRY_AUTH_ID"
GHCR_REGISTRY_AUTH_ENV = "RUNPOD_REGISTRY_AUTH_ID_GHCR"
GITLAB_REGISTRY_AUTH_ENV = "RUNPOD_REGISTRY_AUTH_ID_GITLAB"
DOCKER_HUB_REGISTRY_AUTH_ENV = "RUNPOD_REGISTRY_AUTH_ID_DOCKER_HUB"

GHCR_PREFIX = "ghcr.io"
GITLAB_REGISTRY_PREFIX = "registry.gitlab.com"
DOCKER_HUB_PREFIX = "docker.io"

_REGISTRY_AUTH_PATH = "registries"


class RegistryAuthError(RuntimeError):
    """Base error for registry-auth CRUD failures."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RegistryAuthCreateInput(BaseModel):
    """Input for creating a container-registry auth credential.

    Attributes:
        name: User-defined name; must be unique across the RunPod account.
        username: Registry username.
        password: Registry password or access token.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ContainerRegistryAuth(BaseModel):
    """A container-registry auth credential as returned by RunPod.

    RunPod never returns the ``username`` or ``password`` after creation;
    only ``id`` and ``name`` are available from the API.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)


def _registry_auth_url(path: str | None = None) -> str:
    base = os.environ.get("RUNPOD_REST_API_URL", DEFAULT_RUNPOD_REST_URL).rstrip("/")
    if path is None:
        return f"{base}/{_REGISTRY_AUTH_PATH}"
    return f"{base}/{_REGISTRY_AUTH_PATH}/{path.lstrip('/')}"


def _require_registry_api_key() -> str:
    api_key, _source = resolve_runpod_api_key(os.environ)
    if not api_key:
        raise RegistryAuthError(MISSING_CREDENTIAL_MESSAGE)
    return api_key


def _registry_auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _rest_request_registry_auth(
    method: str,
    path: str | None = None,
    *,
    json_body: dict[str, Any] | None = None,
    timeout_s: float = 60.0,
) -> Any:
    url = _registry_auth_url(path)
    api_key = _require_registry_api_key()
    headers = _registry_auth_headers(api_key)
    normalized_method = method.upper()
    safe_to_repeat = normalized_method in {"GET", "DELETE"}
    response: httpx.Response | None = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.request(
                    normalized_method,
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
        raise RegistryAuthError(f"{normalized_method} {url} returned no response")
    if response.status_code == 204:
        return None
    secrets = (
        api_key,
        *(str(value) for value in (json_body or {}).values()),
    )
    if response.status_code >= 400:
        raise RegistryAuthError(
            f"{normalized_method} {url} failed with HTTP {response.status_code}: "
            f"{redact_text(response.text[:4096], secrets=secrets)}",
            status_code=response.status_code,
        )
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:  # includes JSONDecodeError
        raise RegistryAuthError(
            f"{normalized_method} {url} returned non-JSON body: "
            f"{redact_text(response.text[:4096], secrets=secrets)!r}"
        ) from exc


async def create_container_registry_auth(
    name: str,
    username: str,
    password: str,
    *,
    timeout_s: float = 60.0,
) -> ContainerRegistryAuth:
    """Create a container-registry auth credential.

    Args:
        name: User-defined name; must be unique in the RunPod account.
        username: Registry username.
        password: Registry password or access token.
        timeout_s: Request timeout in seconds.

    Returns:
        ContainerRegistryAuth with the assigned ``id`` and provided ``name``.

    Raises:
        RegistryAuthError: On HTTP 4xx/5xx or missing API key.
    """
    payload = RegistryAuthCreateInput(
        name=name,
        username=username,
        password=password,
    ).model_dump()
    result = await asyncio.to_thread(
        _rest_request_registry_auth,
        "POST",
        json_body=payload,
        timeout_s=timeout_s,
    )
    if not isinstance(result, dict):
        raise RegistryAuthError(
            f"create_container_registry_auth returned unexpected shape: {result!r}"
        )
    return ContainerRegistryAuth.model_validate(result)


async def get_container_registry_auth(
    auth_id: str,
    *,
    timeout_s: float = 60.0,
) -> ContainerRegistryAuth | None:
    """Fetch a single container-registry auth by its RunPod ``auth_id``.

    Args:
        auth_id: RunPod-assigned auth ID.
        timeout_s: Request timeout in seconds.

    Returns:
        ContainerRegistryAuth if found; ``None`` if the server returns 404.

    Raises:
        RegistryAuthError: On HTTP 5xx or missing API key.
    """
    try:
        result = await asyncio.to_thread(
            _rest_request_registry_auth,
            "GET",
            auth_id,
            timeout_s=timeout_s,
        )
    except RegistryAuthError as exc:
        if exc.status_code == 404:
            return None
        raise
    if not isinstance(result, dict):
        raise RegistryAuthError(
            f"get_container_registry_auth returned unexpected shape: {result!r}"
        )
    return ContainerRegistryAuth.model_validate(result)


async def list_container_registry_auths(
    *,
    timeout_s: float = 60.0,
) -> list[ContainerRegistryAuth]:
    """List all container-registry auths for the RunPod account.

    Args:
        timeout_s: Request timeout in seconds.

    Returns:
        List of ContainerRegistryAuth objects (may be empty).

    Raises:
        RegistryAuthError: On HTTP 5xx or missing API key.
    """
    result = await asyncio.to_thread(
        _rest_request_registry_auth,
        "GET",
        timeout_s=timeout_s,
    )
    if not isinstance(result, dict) or not isinstance(result.get("registries"), list):
        raise RegistryAuthError(
            "list_container_registry_auths returned an invalid REST v2 registries envelope"
        )
    return [ContainerRegistryAuth.model_validate(item) for item in result["registries"]]


async def delete_container_registry_auth(
    auth_id: str,
    *,
    timeout_s: float = 60.0,
) -> None:
    """Delete a container-registry auth credential by RunPod ``auth_id``.

    Idempotent: succeeds silently if the credential does not exist.

    Args:
        auth_id: RunPod-assigned auth ID to delete.
        timeout_s: Request timeout in seconds.

    Raises:
        RegistryAuthError: On HTTP 5xx or missing API key.
    """
    try:
        await asyncio.to_thread(
            _rest_request_registry_auth,
            "DELETE",
            auth_id,
            timeout_s=timeout_s,
        )
    except RegistryAuthError as exc:
        if exc.status_code == 404:
            return
        raise


def image_registry_prefix(image_ref: str | None) -> str | None:
    """Return the normalized registry prefix for an image reference."""

    if not image_ref:
        return None

    first = image_ref.split("/", 1)[0].lower()
    if first == GHCR_PREFIX:
        return GHCR_PREFIX
    if first == GITLAB_REGISTRY_PREFIX or first.startswith("gitlab-registry."):
        return GITLAB_REGISTRY_PREFIX
    if first in {DOCKER_HUB_PREFIX, "registry.hub.docker.com"}:
        return DOCKER_HUB_PREFIX

    # Docker Hub shorthand: ``library/python`` or ``vllm/vllm-openai``.
    if "." not in first and ":" not in first and first != "localhost":
        return DOCKER_HUB_PREFIX

    return first


def registry_auth_env_names_for_image_ref(image_ref: str | None) -> tuple[str, ...]:
    """Return auth env vars to try, in priority order, for *image_ref*."""

    prefix = image_registry_prefix(image_ref)
    if prefix == GHCR_PREFIX:
        return (GHCR_REGISTRY_AUTH_ENV, LEGACY_REGISTRY_AUTH_ENV)
    if prefix == GITLAB_REGISTRY_PREFIX:
        return (GITLAB_REGISTRY_AUTH_ENV, LEGACY_REGISTRY_AUTH_ENV)
    if prefix == DOCKER_HUB_PREFIX:
        return (DOCKER_HUB_REGISTRY_AUTH_ENV,)
    return (LEGACY_REGISTRY_AUTH_ENV,)


def registry_auth_id_from_env(
    image_ref: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the configured RunPod registry-auth ID for *image_ref*.

    When no image ref is provided, the legacy GHCR-compatible env var is used
    for backwards compatibility with older callers.
    """

    env = os.environ if environ is None else environ
    if image_ref is None:
        return env.get(LEGACY_REGISTRY_AUTH_ENV) or env.get(GHCR_REGISTRY_AUTH_ENV) or None

    for env_name in registry_auth_env_names_for_image_ref(image_ref):
        auth_id = env.get(env_name)
        if auth_id:
            return auth_id
    return None


__all__ = [
    "ContainerRegistryAuth",
    "DOCKER_HUB_PREFIX",
    "DOCKER_HUB_REGISTRY_AUTH_ENV",
    "GHCR_PREFIX",
    "GHCR_REGISTRY_AUTH_ENV",
    "GITLAB_REGISTRY_AUTH_ENV",
    "GITLAB_REGISTRY_PREFIX",
    "LEGACY_REGISTRY_AUTH_ENV",
    "RegistryAuthCreateInput",
    "RegistryAuthError",
    "create_container_registry_auth",
    "delete_container_registry_auth",
    "get_container_registry_auth",
    "image_registry_prefix",
    "list_container_registry_auths",
    "registry_auth_env_names_for_image_ref",
    "registry_auth_id_from_env",
]
