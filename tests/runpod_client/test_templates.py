from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
import requests
from runpod.error import AuthenticationError, QueryError

from pitwall.runpod_client import templates
from tests.fakes.runpod import RunPodTemplateFake

pytestmark = pytest.mark.anyio
_SECRET_VALUE_CANARY = "value-that-must-not-appear"


def _config_sha(
    image_ref: str,
    *,
    docker_entrypoint: tuple[str, ...] = (),
    docker_start_cmd: tuple[str, ...] = (),
    ports: str | None = None,
    env_keys: tuple[str, ...] = (),
) -> str:
    return templates.config_sha(
        image_ref,
        docker_entrypoint=docker_entrypoint,
        docker_start_cmd=docker_start_cmd,
        ports=ports,
        env_keys=env_keys,
    )


def _ensure_env_keys(*extra: str) -> tuple[str, ...]:
    return templates.non_secret_env_keys((*templates.TEMPLATE_ENV_KEYS, *extra))


def _ensure_config_sha(image_ref: str, **kwargs: Any) -> str:
    extra_env_keys = kwargs.pop("env_keys", ())
    api_key = kwargs.pop("api_key", None)
    return templates.config_sha(
        image_ref,
        env_keys=_ensure_env_keys(*extra_env_keys),
        account_digest=templates.template_account_digest(api_key),
        **kwargs,
    )


def _ensure_display_name(template_name: str, image_ref: str, **kwargs: Any) -> str:
    extra_env_keys = kwargs.pop("env_keys", ())
    return templates.template_display_name(
        template_name,
        image_ref,
        env_keys=_ensure_env_keys(*extra_env_keys),
        **kwargs,
    )


@pytest.mark.parametrize(
    "image_ref",
    [
        "user:token@ghcr.io/org/img:1",
        "https://ghcr.io/org/img",
        "ghcr.io/org/img:1\nsecret",
    ],
)
def test_image_ref_rejects_credentials_schemes_and_whitespace(image_ref: str) -> None:
    with pytest.raises(ValueError, match="Docker image reference"):
        templates.config_sha(image_ref)


@pytest.mark.parametrize(
    "image_ref",
    [
        "ghcr.io/ggml-org/llama.cpp:server-cuda13",
        "vllm/vllm-openai:v0.28.0",
        f"repo@sha256:{'a' * 64}",
    ],
)
def test_image_ref_accepts_catalogue_references_and_sha256_digest(image_ref: str) -> None:
    assert len(templates.config_sha(image_ref)) == 64


async def test_ensure_template_creates_named_template(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    rest_request = AsyncMock(return_value={"id": "template-1"})
    monkeypatch.setattr(templates, "_rest_request_async", rest_request)
    monkeypatch.setattr(templates, "_lookup_cached", runpod_template_fake.lookup_cached)
    monkeypatch.setattr(templates, "_insert_cache", runpod_template_fake.insert_cache)

    image_ref = "gitlab-registry.example.com/org/cloud-worker-embed:smoke-20260507-8e53f70"
    pool: Any = object()
    template_id = await templates.ensure_template(
        pool,
        image_ref,
        template_name="pitwall-book-embed",
        registry_auth_id="registry-1",
        container_disk_gb=80,
    )

    assert template_id == "template-1"
    assert runpod_template_fake.lookups == [
        (
            "pitwall-book-embed",
            _ensure_config_sha(image_ref, container_disk_gb=80, registry_auth_id="registry-1"),
        )
    ]
    assert runpod_template_fake.inserted["name"] == "pitwall-book-embed"
    assert runpod_template_fake.inserted["template_id"] == "template-1"
    assert runpod_template_fake.inserted["image_ref"] == image_ref
    assert runpod_template_fake.inserted["registry_auth_id"] == "registry-1"
    rest_request.assert_awaited_once_with(
        "POST",
        "templates",
        api_key=None,
        rest_api_url=None,
        json_body={
            "name": _ensure_display_name(
                "pitwall-book-embed", image_ref, container_disk_gb=80, registry_auth_id="registry-1"
            ),
            "image": image_ref,
            "disk": 80,
            "env": dict.fromkeys(templates.TEMPLATE_ENV_KEYS, ""),
            "ports": [],
            "serverless": False,
            "public": False,
            "startJupyter": False,
            "startSsh": False,
            "registry": "registry-1",
        },
    )


async def test_ensure_template_uses_custom_rest_url_for_creation(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    image_ref = "ghcr.io/org/cloud-worker:custom-rest"
    rest_request = AsyncMock(return_value={"id": "template-custom", "name": "created"})
    monkeypatch.setattr(templates, "_rest_request_async", rest_request)
    monkeypatch.setattr(templates, "_lookup_cached", runpod_template_fake.lookup_cached)
    monkeypatch.setattr(templates, "_insert_cache", runpod_template_fake.insert_cache)

    template_id = await templates.ensure_template(
        object(),
        image_ref,
        template_name="pitwall-custom",
        registry_auth_id="registry-1",
        container_disk_gb=80,
        api_key="plugin-key",
        rest_api_url="https://rest.runpod.test/v1",
    )

    assert template_id == "template-custom"
    rest_request.assert_awaited_once_with(
        "POST",
        "templates",
        api_key="plugin-key",
        rest_api_url="https://rest.runpod.test/v1",
        json_body={
            "name": _ensure_display_name(
                "pitwall-custom", image_ref, container_disk_gb=80, registry_auth_id="registry-1"
            ),
            "image": image_ref,
            "disk": 80,
            "env": dict.fromkeys(templates.TEMPLATE_ENV_KEYS, ""),
            "ports": [],
            "serverless": False,
            "public": False,
            "startJupyter": False,
            "startSsh": False,
            "registry": "registry-1",
        },
    )
    assert runpod_template_fake.inserted["template_id"] == "template-custom"


def test_template_env_schema_includes_vllm_model() -> None:
    """Verify the template env block includes VLLM_MODEL for vLLM inference.

    The vLLM worker requires VLLM_MODEL to be set in the container env so it
    knows which model to load.
    """
    assert "VLLM_MODEL" in templates.TEMPLATE_ENV_KEYS


def test_config_sha_is_deterministic_and_env_key_order_independent() -> None:
    first = _config_sha(
        f"ghcr.io/org/worker@sha256:{'a' * 64}",
        docker_entrypoint=("/bin/sh", "-lc"),
        docker_start_cmd=("python", "server.py"),
        ports="8000/http,22/tcp",
        env_keys=("MODEL_ID", "CACHE_DIR"),
    )
    second = _config_sha(
        f"ghcr.io/org/worker@sha256:{'a' * 64}",
        docker_entrypoint=("/bin/sh", "-lc"),
        docker_start_cmd=("python", "server.py"),
        ports="8000/http,22/tcp",
        env_keys=("CACHE_DIR", "MODEL_ID"),
    )

    assert first == second
    assert len(first) == 64


@pytest.mark.parametrize(
    ("first_ref", "second_ref"),
    [
        ("ghcr.io/org/a:latest", "ghcr.io/other/b:latest"),
        (
            f"ghcr.io/org/a@sha256:{'a' * 64}",
            f"ghcr.io/other/b@sha256:{'a' * 64}",
        ),
        ("docker.io/library/worker:1", "worker:1"),
    ],
)
def test_config_sha_distinguishes_byte_distinct_image_references(
    first_ref: str, second_ref: str
) -> None:
    assert _config_sha(first_ref) != _config_sha(second_ref)


@pytest.mark.parametrize(
    ("changed", "value"),
    [
        ("image_ref", f"ghcr.io/org/worker@sha256:{'b' * 64}"),
        ("docker_entrypoint", ("/usr/bin/env", "bash")),
        ("docker_start_cmd", ("python", "other.py")),
        ("ports", "8080/http,22/tcp"),
        ("env_keys", ("CACHE_DIR", "MODEL_ID", "SERVED_NAME")),
    ],
)
def test_config_sha_changes_for_every_key_input(changed: str, value: object) -> None:
    inputs: dict[str, object] = {
        "image_ref": f"ghcr.io/org/worker@sha256:{'a' * 64}",
        "docker_entrypoint": ("/bin/sh", "-lc"),
        "docker_start_cmd": ("python", "server.py"),
        "ports": "8000/http,22/tcp",
        "env_keys": ("CACHE_DIR", "MODEL_ID"),
    }
    baseline = templates.config_sha(**inputs)
    inputs[changed] = value

    assert templates.config_sha(**inputs) != baseline


def test_config_sha_ignores_secret_values_and_secret_key_names() -> None:
    public = templates.non_secret_env_keys(
        {
            "MODEL_ID": "org/model",
            "HF_TOKEN": _SECRET_VALUE_CANARY,
        }
    )
    rotated = templates.non_secret_env_keys(
        {
            "MODEL_ID": "changed-value",
            "HF_TOKEN": _SECRET_VALUE_CANARY,
        }
    )

    assert public == rotated == ("MODEL_ID",)
    assert _config_sha("ghcr.io/org/worker:v1", env_keys=public) == _config_sha(
        "ghcr.io/org/worker:v1", env_keys=rotated
    )


async def test_ensure_template_reuses_only_identical_full_config(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    image_ref = f"ghcr.io/org/cloud-worker@sha256:{'a' * 64}"
    kwargs = {
        "docker_entrypoint": ("/bin/sh", "-lc"),
        "docker_start_cmd": ("python", "server.py"),
        "ports": "8000/http",
        "env": {"MODEL_ID": "org/model", "HF_TOKEN": _SECRET_VALUE_CANARY},
    }
    sha = _ensure_config_sha(
        image_ref,
        docker_entrypoint=kwargs["docker_entrypoint"],
        docker_start_cmd=kwargs["docker_start_cmd"],
        ports=kwargs["ports"],
        env_keys=("MODEL_ID",),
    )
    runpod_template_fake.set_cached("pitwall-full-config", sha, "template-cached")
    monkeypatch.setattr(templates, "_lookup_cached", runpod_template_fake.lookup_cached)
    monkeypatch.setattr(templates, "_sdk", lambda: runpod_template_fake.sdk)

    result = await templates.ensure_template(
        object(), image_ref, template_name="pitwall-full-config", **kwargs
    )

    assert result == "template-cached"
    assert runpod_template_fake.lookups == [("pitwall-full-config", sha)]
    assert runpod_template_fake.sdk.create_template_kwargs is None


async def test_ensure_template_does_not_log_or_store_secret_values(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = _SECRET_VALUE_CANARY
    monkeypatch.setattr(
        templates, "_rest_request_async", AsyncMock(return_value={"id": "template-1"})
    )
    monkeypatch.setattr(templates, "_lookup_cached", runpod_template_fake.lookup_cached)
    monkeypatch.setattr(templates, "_insert_cache", runpod_template_fake.insert_cache)

    with caplog.at_level(logging.DEBUG, logger="pitwall.runpod_client.templates"):
        await templates.ensure_template(
            object(),
            "ghcr.io/org/cloud-worker:v1",
            template_name="pitwall-secret-safe",
            env={"MODEL_ID": "org/model", "HF_TOKEN": canary},
        )

    assert canary not in caplog.text
    assert canary not in str(runpod_template_fake.inserted)
    assert "MODEL_ID" in runpod_template_fake.inserted["env_schema"]
    assert "HF_TOKEN" not in runpod_template_fake.inserted["env_schema"]


async def test_ensure_template_redacts_secret_start_arguments_in_logs(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "hf_test_template_log_token"
    monkeypatch.setattr(
        templates, "_rest_request_async", AsyncMock(return_value={"id": "template-1"})
    )
    monkeypatch.setattr(templates, "_lookup_cached", runpod_template_fake.lookup_cached)
    monkeypatch.setattr(templates, "_insert_cache", runpod_template_fake.insert_cache)

    with caplog.at_level(logging.INFO, logger="pitwall.runpod_client.templates"):
        await templates.ensure_template(
            object(),
            "ghcr.io/org/cloud-worker:v1",
            template_name="pitwall-command-redaction",
            docker_start_cmd=("serve", f"--token={canary}"),
        )

    assert canary not in caplog.text


async def test_ensure_template_reuses_cached_named_template(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    image_ref = "gitlab-registry.example.com/org/cloud-worker:abc123"
    runpod_template_fake.set_cached(
        "pitwall-book-parse-ocr",
        _ensure_config_sha(image_ref),
        "template-cached",
    )

    monkeypatch.setattr(templates, "_sdk", lambda: runpod_template_fake.sdk)
    monkeypatch.setattr(templates, "_lookup_cached", runpod_template_fake.lookup_cached)

    pool: Any = object()
    template_id = await templates.ensure_template(
        pool,
        image_ref,
        template_name="pitwall-book-parse-ocr",
    )

    assert template_id == "template-cached"
    assert runpod_template_fake.lookups == [
        ("pitwall-book-parse-ocr", _ensure_config_sha(image_ref))
    ]
    assert runpod_template_fake.sdk.create_template_kwargs is None


def test_image_sha_with_digest() -> None:
    assert templates.image_sha("ghcr.io/org/worker@sha256:abcdef123456") == "abcdef123456"


def test_image_sha_with_tag() -> None:
    assert templates.image_sha("ghcr.io/org/worker:v1.2.3") == "v1.2.3"


def test_image_sha_without_tag() -> None:
    assert templates.image_sha("ghcr.io/org/worker") == "latest"


def test_template_suffix_stable() -> None:
    ref = "ghcr.io/org/worker:abc"
    assert templates.template_suffix(ref) == templates.template_suffix(ref)
    assert len(templates.template_suffix(ref)) == 12


def test_normalize_template_name_strips_special_chars() -> None:
    assert templates.normalize_template_name("my template!!!") == "my-template"
    assert templates.normalize_template_name("  ") == templates.TEMPLATE_NAME


def test_template_display_name() -> None:
    name = templates.template_display_name("my-app", "ghcr.io/org/worker:v1")
    assert name.startswith("my-app-")
    assert name.endswith(templates.template_suffix("ghcr.io/org/worker:v1"))


def test_get_image_ref_from_env_raises_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PITWALL_CLOUD_WORKER_IMAGE", raising=False)
    with pytest.raises(RuntimeError, match="PITWALL_CLOUD_WORKER_IMAGE not set"):
        templates.get_image_ref_from_env()


def test_get_image_ref_from_env_returns_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_CLOUD_WORKER_IMAGE", "ghcr.io/org/worker:v1")
    assert templates.get_image_ref_from_env() == "ghcr.io/org/worker:v1"


def test_get_registry_auth_id_from_env_picks_gitlab_for_glcr_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_REGISTRY_AUTH_ID", "ghcr-auth-id")
    monkeypatch.setenv("RUNPOD_REGISTRY_AUTH_ID_GITLAB", "glcr-auth-id")

    assert (
        templates.get_registry_auth_id_from_env(
            "gitlab-registry.example.test/example/pitwall/cloud-worker:abc"
        )
        == "glcr-auth-id"
    )


def test_get_registry_auth_id_from_env_picks_ghcr_for_ghcr_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_REGISTRY_AUTH_ID", "ghcr-auth-id")
    monkeypatch.setenv("RUNPOD_REGISTRY_AUTH_ID_GITLAB", "glcr-auth-id")

    assert (
        templates.get_registry_auth_id_from_env("ghcr.io/example/pitwall-cloud-worker:abc")
        == "ghcr-auth-id"
    )


def test_get_registry_auth_id_from_env_no_image_falls_back_to_legacy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_REGISTRY_AUTH_ID", "ghcr-auth-id")
    monkeypatch.setenv("RUNPOD_REGISTRY_AUTH_ID_GITLAB", "glcr-auth-id")

    assert templates.get_registry_auth_id_from_env() == "ghcr-auth-id"


def test_get_registry_auth_id_from_env_glcr_image_without_gitlab_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_REGISTRY_AUTH_ID", "ghcr-auth-id")
    monkeypatch.delenv("RUNPOD_REGISTRY_AUTH_ID_GITLAB", raising=False)

    assert (
        templates.get_registry_auth_id_from_env(
            "gitlab-registry.example.test/example/pitwall/cloud-worker:abc"
        )
        == "ghcr-auth-id"
    )


def test_is_duplicate_template_name_error_matches_runpod_message() -> None:
    err = QueryError("Something went wrong. (Template name must be unique)")
    assert templates._is_duplicate_template_name_error(err) is True


def test_is_duplicate_template_name_error_case_insensitive() -> None:
    assert (
        templates._is_duplicate_template_name_error(QueryError("template NAME must be UNIQUE"))
        is True
    )


def test_is_duplicate_template_name_error_ignores_unrelated_errors() -> None:
    assert templates._is_duplicate_template_name_error(QueryError("rate limited")) is False
    assert templates._is_duplicate_template_name_error(ValueError("name")) is False


def test_resolve_existing_template_id_finds_by_name() -> None:
    tmpls = [{"id": "t-1", "name": "alpha"}, {"id": "t-2", "name": "beta"}]
    assert templates._resolve_existing_template_id(tmpls, "beta") == "t-2"


def test_resolve_existing_template_id_returns_none_when_absent() -> None:
    tmpls = [{"id": "t-1", "name": "alpha"}]
    assert templates._resolve_existing_template_id(tmpls, "beta") is None


def test_resolve_existing_template_id_skips_malformed_entries() -> None:
    tmpls = [{"id": "t-1"}, {"name": "beta"}, {"id": "t-2", "name": "beta"}]
    assert templates._resolve_existing_template_id(tmpls, "beta") == "t-2"


async def test_ensure_template_reuses_existing_on_name_collision(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    """DB cache miss + RunPod already has the template (display name collision).

    template creation raises a duplicate-name QueryError; ensure_template must
    resolve the existing template by its display name, cache it under the logical
    name, and return its id instead of propagating the collision.
    """
    image_ref = "ghcr.io/org/cloud-worker:reuse123"
    display_name = _ensure_display_name("pitwall-reuse", image_ref)

    rest_request = AsyncMock(
        side_effect=[
            QueryError("Something went wrong. (Template name must be unique)"),
            {"templates": [{"id": "existing-tmpl", "name": display_name}]},
        ]
    )
    monkeypatch.setattr(templates, "_rest_request_async", rest_request)
    monkeypatch.setattr(templates, "_lookup_cached", runpod_template_fake.lookup_cached)
    monkeypatch.setattr(templates, "_insert_cache", runpod_template_fake.insert_cache)

    pool: Any = object()
    template_id = await templates.ensure_template(pool, image_ref, template_name="pitwall-reuse")

    assert template_id == "existing-tmpl"
    assert runpod_template_fake.inserted["template_id"] == "existing-tmpl"
    assert runpod_template_fake.inserted["name"] == "pitwall-reuse"


async def test_ensure_template_custom_urls_route_rest_creation_and_graphql_collision_lookup(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    image_ref = "ghcr.io/org/cloud-worker:reuse-custom"
    display_name = _ensure_display_name("pitwall-reuse", image_ref)
    clients: list[_FakeTemplateGraphQLClient] = []

    def fake_client_factory(**kwargs: Any) -> _FakeTemplateGraphQLClient:
        client = _FakeTemplateGraphQLClient(
            **kwargs,
            responses=[
                {"myself": {"podTemplates": [{"id": "existing-custom", "name": display_name}]}}
            ],
        )
        clients.append(client)
        return client

    rest_request = AsyncMock(
        side_effect=QueryError("Something went wrong. (Template name must be unique)")
    )
    monkeypatch.setattr(templates, "_rest_request_async", rest_request)
    monkeypatch.setattr(templates, "RunpodGraphQLClient", fake_client_factory)
    monkeypatch.setattr(templates, "_lookup_cached", runpod_template_fake.lookup_cached)
    monkeypatch.setattr(templates, "_insert_cache", runpod_template_fake.insert_cache)

    template_id = await templates.ensure_template(
        object(),
        image_ref,
        template_name="pitwall-reuse",
        api_key="plugin-key",
        graphql_url="https://graphql.runpod.test/graphql",
        rest_api_url="https://rest.runpod.test/v1",
    )

    assert template_id == "existing-custom"
    assert [(client.api_key, client.graphql_url) for client in clients] == [
        ("plugin-key", "https://graphql.runpod.test/graphql"),
    ]
    rest_request.assert_awaited_once()
    assert rest_request.await_args.kwargs["api_key"] == "plugin-key"
    assert rest_request.await_args.kwargs["rest_api_url"] == "https://rest.runpod.test/v1"
    assert "podTemplates" in clients[0].queries[0]
    assert runpod_template_fake.inserted["template_id"] == "existing-custom"


async def test_get_template_returns_template(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    response = {
        "id": "tmpl-abc123",
        "name": "my-template",
        "imageName": "ghcr.io/org/worker:v1",
        "dockerArgs": "python server.py",
        "containerDiskInGb": 50,
        "volumeInGb": 0,
        "volumeMountPath": "/workspace",
        "ports": ["8000/http"],
        "env": {"FOO": "bar"},
        "isServerless": False,
        "isPublic": False,
        "readme": "# My Template",
    }
    rest_request = AsyncMock(return_value=response)
    monkeypatch.setattr(templates, "_rest_request_async", rest_request)

    template = await templates.get_template("tmpl-abc123")

    assert template.id == "tmpl-abc123"
    assert template.name == "my-template"
    assert template.image_name == "ghcr.io/org/worker:v1"
    assert template.docker_args == "python server.py"
    assert template.container_disk_in_gb == 50
    assert template.ports == "8000/http"
    assert template.env[0].key == "FOO"
    assert template.env[0].value == "bar"
    assert template.is_serverless is False
    assert template.is_public is False
    assert template.readme == "# My Template"
    rest_request.assert_awaited_once_with(
        "GET",
        "templates/tmpl-abc123",
        api_key=None,
        rest_api_url=None,
    )


async def test_get_template_raises_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        templates,
        "_rest_request_async",
        AsyncMock(
            side_effect=templates.RunPodRestError("GET", "templates/tmpl-missing", 404, "missing")
        ),
    )

    with pytest.raises(templates.TemplateNotFoundError, match="tmpl-missing"):
        await templates.get_template("tmpl-missing")


@pytest.mark.parametrize("body", [{}, None, [], "ok"])
async def test_get_template_treats_an_unusable_200_body_as_a_provider_error(
    monkeypatch: pytest.MonkeyPatch, body: object
) -> None:
    # Only a 404 is not-found; onboarding resume releases a key on not-found.
    monkeypatch.setattr(templates, "_rest_request_async", AsyncMock(return_value=body))

    with pytest.raises(templates.RunPodError) as exc_info:
        await templates.get_template("tmpl-live")

    assert not isinstance(exc_info.value, templates.TemplateNotFoundError)


async def test_get_template_raises_on_rest_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        templates,
        "_rest_request_async",
        AsyncMock(
            side_effect=templates.RunPodRestError("GET", "templates/tmpl-auth", 401, "auth denied")
        ),
    )

    with pytest.raises(templates.RunPodRestError, match="auth denied"):
        await templates.get_template("tmpl-auth")


async def test_update_template_updates_and_returns_template(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    runpod_template_fake.set_template(
        {
            "id": "tmpl-update-test",
            "name": "original-name",
            "imageName": "ghcr.io/org/worker:v1",
            "dockerArgs": "",
            "containerDiskInGb": 20,
            "volumeInGb": 0,
            "volumeMountPath": "/workspace",
            "ports": "",
            "env": [],
            "isServerless": False,
            "isPublic": False,
            "readme": "",
        }
    )

    rest_request = AsyncMock(
        return_value={
            "id": "tmpl-update-test",
            "name": "new-name",
            "image": "ghcr.io/org/worker:v2",
            "args": "",
            "disk": 80,
            "mounts": {},
            "ports": [],
            "env": {"NEW_ENV": "value"},
            "serverless": False,
            "public": False,
        }
    )
    monkeypatch.setattr(templates, "_rest_request_async", rest_request)

    updated = await templates.update_template(
        "tmpl-update-test",
        name="new-name",
        image_name="ghcr.io/org/worker:v2",
        container_disk_in_gb=80,
        env={"NEW_ENV": "value"},
    )

    assert updated.id == "tmpl-update-test"
    rest_request.assert_awaited_once_with(
        "PATCH",
        "templates/tmpl-update-test",
        api_key=None,
        rest_api_url=None,
        json_body={
            "name": "new-name",
            "image": "ghcr.io/org/worker:v2",
            "disk": 80,
            "env": {"NEW_ENV": "value"},
        },
    )


async def test_update_template_rejects_v2_unsupported_readme_before_write(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    runpod_template_fake.set_template(
        {
            "id": "tmpl-escaped",
            "name": 'safe "name"',
            "imageName": "ghcr.io/org/worker:v2",
            "dockerArgs": 'python -c "print(1)"',
            "containerDiskInGb": 20,
            "volumeInGb": 0,
            "volumeMountPath": "/workspace",
            "ports": "8000/http",
            "env": [{"key": "QUOTE", "value": 'line1\n"value"\\tail'}],
            "isServerless": False,
            "isPublic": False,
            "readme": "line1\nline2",
        }
    )
    rest_request = AsyncMock()
    monkeypatch.setattr(templates, "_rest_request_async", rest_request)

    with pytest.raises(templates.RunPodError, match="does not support template readme"):
        await templates.update_template(
            "tmpl-escaped",
            name='safe "name"',
            docker_args='python -c "print(1)"',
            env={"QUOTE": 'line1\n"value"\\tail'},
            readme="line1\nline2",
        )

    rest_request.assert_not_awaited()


async def test_update_template_raises_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    monkeypatch.setattr(
        templates,
        "_rest_request_async",
        AsyncMock(
            side_effect=templates.RunPodRestError(
                "PATCH",
                "templates/tmpl-does-not-exist",
                404,
                "not found",
            )
        ),
    )

    with pytest.raises(templates.TemplateNotFoundError, match="tmpl-does-not-exist"):
        await templates.update_template("tmpl-does-not-exist", name="new-name")


async def test_delete_template_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rest_request = AsyncMock(return_value={})
    monkeypatch.setattr(templates, "_rest_request_async", rest_request)

    result = await templates.delete_template("tmpl-to-delete")

    assert result is True
    rest_request.assert_awaited_once_with(
        "DELETE",
        "templates/tmpl-to-delete",
        api_key=None,
        rest_api_url=None,
    )


async def test_delete_template_is_idempotent_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        templates,
        "_rest_request_async",
        AsyncMock(
            side_effect=templates.RunPodRestError(
                "DELETE", "templates/tmpl-nonexistent", 404, "missing"
            )
        ),
    )

    assert await templates.delete_template("tmpl-nonexistent") is True


async def test_list_hub_templates_returns_templates(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    runpod_template_fake.set_hub_template(
        {
            "id": "hub-tmpl-1",
            "name": "vllm-worker",
            "imageName": "ghcr.io/runpod/vllm:latest",
            "githubUrl": "https://github.com/runpod/vllm-worker",
            "dockerArgs": "",
            "containerDiskInGb": 50,
            "volumeInGb": 0,
            "volumeMountPath": "/workspace",
            "ports": "8000/http",
            "env": [],
            "isServerless": False,
            "templateDescription": "Production vLLM server",
        }
    )
    runpod_template_fake.set_hub_template(
        {
            "id": "hub-tmpl-2",
            "name": "tensorrt-worker",
            "imageName": "ghcr.io/runpod/tensorrt:latest",
            "dockerArgs": "",
            "containerDiskInGb": 80,
            "volumeInGb": 0,
            "volumeMountPath": "/workspace",
            "ports": "8000/http",
            "env": [],
            "isServerless": False,
        }
    )

    monkeypatch.setattr(templates, "_run_graphql", runpod_template_fake.run_graphql_fake)

    hub_templates = await templates.list_hub_templates(limit=10)

    assert len(hub_templates) == 2
    assert hub_templates[0].id == "hub-tmpl-1"
    assert hub_templates[0].name == "vllm-worker"
    assert hub_templates[0].description is None
    assert hub_templates[1].id == "hub-tmpl-2"
    assert "podTemplates" in runpod_template_fake.graphql_calls[-1]
    assert "hubPodTemplates" not in runpod_template_fake.graphql_calls[-1]


async def test_list_hub_templates_respects_limit_offset(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    for i in range(5):
        runpod_template_fake.set_hub_template(
            {
                "id": f"hub-tmpl-{i}",
                "name": f"template-{i}",
                "imageName": f"ghcr.io/org/worker:{i}",
                "dockerArgs": "",
                "containerDiskInGb": 10,
                "volumeInGb": 0,
                "volumeMountPath": "/workspace",
                "ports": "",
                "env": [],
                "isServerless": False,
            }
        )

    monkeypatch.setattr(templates, "_run_graphql", runpod_template_fake.run_graphql_fake)

    page1 = await templates.list_hub_templates(limit=2, offset=0)
    page2 = await templates.list_hub_templates(limit=2, offset=2)

    assert len(page1) == 2
    assert page1[0].id == "hub-tmpl-0"
    assert page1[1].id == "hub-tmpl-1"
    assert len(page2) == 2
    assert page2[0].id == "hub-tmpl-2"
    assert page2[1].id == "hub-tmpl-3"


async def test_get_hub_template_returns_template(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    runpod_template_fake.set_hub_template(
        {
            "id": "hub-single-123",
            "name": "specific-hub-template",
            "imageName": "ghcr.io/runpod/specific:latest",
            "dockerArgs": "--port 8000",
            "containerDiskInGb": 100,
            "volumeInGb": 0,
            "volumeMountPath": "/workspace",
            "ports": "8000/http",
            "env": [{"key": "MODEL", "value": "mistral-7b"}],
            "isServerless": False,
        }
    )

    monkeypatch.setattr(templates, "_run_graphql", runpod_template_fake.run_graphql_fake)

    hub_template = await templates.get_hub_template("hub-single-123")

    assert hub_template.id == "hub-single-123"
    assert hub_template.name == "specific-hub-template"
    assert hub_template.docker_args == "--port 8000"
    assert hub_template.container_disk_in_gb == 100
    assert hub_template.env[0].key == "MODEL"
    assert hub_template.env[0].value == "mistral-7b"
    assert "podTemplate(id:" in runpod_template_fake.graphql_calls[-1]
    assert "hubPodTemplate(id:" not in runpod_template_fake.graphql_calls[-1]


async def test_get_hub_template_raises_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
    runpod_template_fake: RunPodTemplateFake,
) -> None:
    monkeypatch.setattr(templates, "_run_graphql", runpod_template_fake.run_graphql_fake)

    with pytest.raises(templates.TemplateNotFoundError, match="hub-nonexistent"):
        await templates.get_hub_template("hub-nonexistent")


def test_template_model_validation() -> None:
    tmpl = templates.Template(
        id="t-1",
        name="test",
        image_name="img:latest",
        docker_args=None,
        container_disk_in_gb=50,
        volume_in_gb=0,
        volume_mount_path="/workspace",
        ports="",
        env=None,
        is_serverless=False,
        is_public=False,
        readme="",
    )
    assert tmpl.id == "t-1"
    assert tmpl.docker_args is None
    assert tmpl.env is None


def test_hub_template_model_validation() -> None:
    hub = templates.HubTemplate(
        id="h-1",
        name="hub-test",
        image_name="hub/img:latest",
        description="A hub template",
        github_url=None,
        docker_args="python app.py",
        container_disk_in_gb=20,
        volume_in_gb=10,
        volume_mount_path="/data",
        ports="3000/http",
        env=[templates.TemplateEnvVar(key="KEY", value="VAL")],
        is_serverless=False,
        display_name="Hub Test",
        template_description=None,
    )
    assert hub.id == "h-1"
    assert hub.description == "A hub template"
    assert hub.docker_args == "python app.py"
    assert hub.volume_in_gb == 10
    assert hub.env[0].key == "KEY"


def test_template_not_found_error_is_runtime_error() -> None:
    err = templates.TemplateNotFoundError("template not found")
    assert isinstance(err, RuntimeError)


def test_template_delete_error_is_runtime_error() -> None:
    err = templates.TemplateDeleteError("cannot delete")
    assert isinstance(err, RuntimeError)


class _FakeTemplateGraphQLClient:
    def __init__(
        self,
        *,
        api_key: str,
        graphql_url: str,
        responses: list[dict[str, Any] | BaseException],
        **_: Any,
    ) -> None:
        self.api_key = api_key
        self.graphql_url = graphql_url
        self._responses = responses
        self.queries: list[str] = []
        self.closed = False

    async def _graphql(
        self,
        query: str,
        *,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.queries.append(query)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "failure",
    [
        requests.ConnectionError("proxy refused"),
        QueryError("upstream query failed"),
        AuthenticationError("Unauthorized request"),
    ],
    ids=["transport", "query", "auth"],
)
def test_run_graphql_sdk_failures_surface_as_runpod_errors(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    import runpod.api.graphql as rp_graphql

    def boom(query: str) -> dict[str, Any]:
        raise failure

    monkeypatch.setenv("RUNPOD_API_KEY", "placeholder")
    monkeypatch.setattr(rp_graphql, "run_graphql_query", boom)

    with pytest.raises(templates.RunPodError) as raised:
        templates._run_graphql("query { podTemplates { id } }")
    assert raised.value.__cause__ is failure


class _TemplateAccountCache:
    """A real-enough template cache: rows keyed by (name, config_sha), creates counted."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rows: dict[tuple[str, str], str] = {}
        self.creates: list[dict[str, Any]] = []

        async def lookup(_: object, name: str, sha: str) -> str | None:
            return self.rows.get((name, sha))

        async def insert(_: object, **kwargs: Any) -> None:
            self.rows[(kwargs["name"], kwargs["sha"])] = kwargs["template_id"]

        async def rest_request(method: str, path: str, **kwargs: Any) -> dict[str, str]:
            self.creates.append({"api_key": kwargs["api_key"], **kwargs["json_body"]})
            return {"id": f"template-{len(self.creates)}"}

        monkeypatch.setattr(templates, "_lookup_cached", lookup)
        monkeypatch.setattr(templates, "_insert_cache", insert)
        monkeypatch.setattr(templates, "_rest_request_async", rest_request)

    async def ensure(self, **kwargs: Any) -> str:
        pool: Any = object()
        return await templates.ensure_template(
            pool, "ghcr.io/acme/worker:1", template_name="pitwall-account", **kwargs
        )


async def test_the_template_cache_is_scoped_to_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _TemplateAccountCache(monkeypatch)

    first = await cache.ensure(api_key="account-one-key")
    second = await cache.ensure(api_key="account-two-key")
    again = await cache.ensure(api_key="account-one-key")

    assert len(cache.creates) == 2
    assert [create["api_key"] for create in cache.creates] == [
        "account-one-key",
        "account-two-key",
    ]
    assert first != second
    assert again == first


async def test_a_changed_container_disk_size_is_a_new_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _TemplateAccountCache(monkeypatch)

    await cache.ensure(api_key="key", container_disk_gb=50)
    await cache.ensure(api_key="key", container_disk_gb=120)

    assert [create["disk"] for create in cache.creates] == [50, 120]
    assert len({create["name"] for create in cache.creates}) == 2


async def test_a_changed_registry_auth_id_is_a_new_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _TemplateAccountCache(monkeypatch)

    await cache.ensure(api_key="key", registry_auth_id="auth-old")
    await cache.ensure(api_key="key", registry_auth_id="auth-new")

    assert [create["registry"] for create in cache.creates] == ["auth-old", "auth-new"]
    assert len({create["name"] for create in cache.creates}) == 2


async def test_an_unchanged_request_is_a_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _TemplateAccountCache(monkeypatch)

    first = await cache.ensure(api_key="key", container_disk_gb=80, registry_auth_id="auth-1")
    second = await cache.ensure(api_key="key", container_disk_gb=80, registry_auth_id="auth-1")

    assert first == second
    assert len(cache.creates) == 1


def test_the_account_enters_the_config_digest_only_as_a_keyed_digest() -> None:
    digest = templates.template_account_digest("account-one-key")

    assert digest == templates.template_account_digest("account-one-key")
    assert digest != templates.template_account_digest("account-two-key")
    assert "account-one-key" not in digest
    assert "account-one-key" not in templates.config_sha(
        "ghcr.io/acme/worker:1", account_digest=digest
    )
