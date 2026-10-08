"""Cross-cutting fixture cases: pod-lease readiness, cost order, attach hang, R2 credentials."""

from __future__ import annotations

from types import ModuleType

from pitwall.audit._common import (
    MAX_VOLUME_ATTACH_TIMEOUT_S,
    POD_LEASE_REQUIRED_READINESS_SIGNALS,
    R2_FORBIDDEN_POD_ENV_KEYS,
    R2_TEMP_CREDENTIAL_ROUTE_FRAGMENT,
    R2_TEMP_CREDENTIAL_STRATEGIES,
    AuditConfig,
    CheckFailed,
)
from pitwall.audit._fixtures import (
    _pod_lease_provider_fixtures,
    _provider_attach_timeout_s,
    _provider_data_center_ids,
    _provider_env_mappings,
    _provider_label,
    _provider_order,
    _provider_r2_strategy,
    _provider_readiness_signals,
    _provider_requires_r2,
    _provider_volume_id,
)
from pitwall.audit._introspect import facts_of
from pitwall.runpod_client import pods


def _check_order_has_cost_before_readiness(
    *,
    check_id: int,
    order: list[str],
    label: str,
) -> None:
    cost_pos = None
    ready_pos = None
    for i, step in enumerate(order):
        if "cost" in step and cost_pos is None:
            cost_pos = i
        if ("readi" in step or "wait_for_pod_runtime" in step or "probe" in step) and (
            ready_pos is None
        ):
            ready_pos = i
    if cost_pos is None:
        raise CheckFailed(check_id, f"{label} cost-cap check missing from order")
    if ready_pos is None:
        raise CheckFailed(check_id, f"{label} readiness wait missing from order")
    if cost_pos > ready_pos:
        raise CheckFailed(
            check_id,
            f"{label} cost-cap check fires after readiness wait — must fire before",
        )


def _launch_runpod_units(lease_launch: ModuleType) -> list[object]:
    """The RunPod launch entry point, its failure step, and the cooldown it records."""
    try:
        return [
            lease_launch._run_launch_runpod,
            lease_launch._run_launch_runpod_create_failed,
            lease_launch._set_provider_attach_hang_cooldown,
        ]
    except AttributeError as exc:
        raise CheckFailed(9, "pod lease launch attach-hang recovery path is missing") from exc


def _check_pod_lease_readiness_cases(cfg: AuditConfig) -> None:
    partial = pods._ReadinessSignals(
        runtime_seen_at="2026-05-28T12:00:00Z",
        port_mappings_seen_at="2026-05-28T12:00:01Z",
    )
    if partial.complete:
        raise CheckFailed(3, "runtime+port mappings were treated as active without probe 2xx")

    complete = pods._ReadinessSignals(
        runtime_seen_at="2026-05-28T12:00:00Z",
        port_mappings_seen_at="2026-05-28T12:00:01Z",
        probe_passed_at="2026-05-28T12:00:02Z",
        probe_method=pods.SSH_LOCALHOST_PROBE_METHOD,
    )
    if not complete.complete:
        raise CheckFailed(3, "complete pod readiness signals were not treated as active")

    required = set(POD_LEASE_REQUIRED_READINESS_SIGNALS)
    for provider in _pod_lease_provider_fixtures(cfg):
        provider_label = _provider_label(provider)
        signals = _provider_readiness_signals(provider)
        if signals is None:
            raise CheckFailed(
                3,
                f"pod_lease provider fixture {provider_label!r} missing readiness signals",
            )
        missing = [
            signal for signal in POD_LEASE_REQUIRED_READINESS_SIGNALS if signal not in signals
        ]
        if missing:
            raise CheckFailed(
                3,
                f"pod_lease provider fixture {provider_label!r} readiness missing {missing!r}; "
                f"required {sorted(required)!r}",
            )


def _check_pod_lease_cost_order_cases(cfg: AuditConfig) -> None:
    for provider in _pod_lease_provider_fixtures(cfg):
        provider_label = _provider_label(provider)
        order = _provider_order(
            provider,
            (
                ("cost_check_order",),
                ("check_order",),
                ("launch_order",),
                ("audit", "cost_check_order"),
                ("audit", "launch_order"),
            ),
        )
        if order is None:
            raise CheckFailed(
                4,
                f"pod_lease provider fixture {provider_label!r} missing cost/readiness order",
            )
        _check_order_has_cost_before_readiness(
            check_id=4,
            order=order,
            label=f"pod_lease provider fixture {provider_label!r}",
        )


def _check_pod_lease_attach_hang_cases(cfg: AuditConfig) -> None:
    if pods.DEFAULT_VOLUME_ATTACH_TIMEOUT_S > MAX_VOLUME_ATTACH_TIMEOUT_S:
        raise CheckFailed(
            9,
            "default volume attach timeout exceeds 5-minute L7 budget",
        )
    wait_facts = facts_of(pods.wait_for_pod_runtime_sync, pods._wait_for_pod_runtime_sync)
    for required_token in (
        "_pod_has_network_volume",
        "_pod_has_zero_uptime",
        "PodVolumeAttachTimeout",
    ):
        if not wait_facts.references(required_token):
            raise CheckFailed(
                9,
                f"wait_for_pod_runtime_sync missing L7 attach-hang guard {required_token}",
            )

    from pitwall.api.leases import launch as lease_launch

    launch_facts = facts_of(*_launch_runpod_units(lease_launch))
    for required_token in (
        "ProviderAttachHangRecoveryRequested",
        "_set_provider_attach_hang_cooldown",
    ):
        if not launch_facts.references(required_token):
            raise CheckFailed(
                9,
                f"pod lease launch missing attach-hang recovery path {required_token}",
            )

    for provider in _pod_lease_provider_fixtures(cfg):
        if not _provider_volume_id(provider):
            continue
        provider_label = _provider_label(provider)
        dc_ids = _provider_data_center_ids(provider)
        if len(dc_ids) != 1:
            raise CheckFailed(
                9,
                f"pod_lease provider fixture {provider_label!r} has volume with "
                f"{len(dc_ids)} data centers; expected exactly one",
            )
        timeout_s = _provider_attach_timeout_s(9, provider)
        if timeout_s <= 0:
            raise CheckFailed(
                9,
                f"pod_lease provider fixture {provider_label!r} attach timeout must be > 0",
            )
        if timeout_s > MAX_VOLUME_ATTACH_TIMEOUT_S:
            raise CheckFailed(
                9,
                f"pod_lease provider fixture {provider_label!r} attach timeout "
                f"{timeout_s:g}s exceeds {MAX_VOLUME_ATTACH_TIMEOUT_S}s",
            )


def _check_r2_temp_credential_cases(cfg: AuditConfig) -> None:
    from pitwall.api.leases import launch as lease_launch
    from pitwall.r2_temp_credentials import CloudflareR2TempCredentialClient, R2TemporaryCredentials
    from pitwall.staging_store import (
        CloudflareR2StagingStore,
        NoOpStagingStore,
        get_staging_store,
    )

    client_facts = facts_of(CloudflareR2TempCredentialClient.create)
    if not client_facts.has_string_containing(R2_TEMP_CREDENTIAL_ROUTE_FRAGMENT):
        raise CheckFailed(11, "R2 client does not call temp-access-credentials endpoint")
    deprecated_rotation_fragment = "/" + "/".join(("r2", "tokens"))
    env_for_pod_facts = facts_of(lease_launch._env_for_pod)
    if client_facts.has_string_containing(
        deprecated_rotation_fragment
    ) or env_for_pod_facts.has_string_containing(deprecated_rotation_fragment):
        raise CheckFailed(11, "R2 deprecated token-rotation endpoint appears in pod path")
    if not (
        env_for_pod_facts.references("vend_pod_credentials")
        and env_for_pod_facts.references("get_staging_store")
    ):
        raise CheckFailed(11, "pod lease env path does not use the StagingStore seam")
    if env_for_pod_facts.references("vend_r2_temp_credential_pod_env"):
        raise CheckFailed(11, "pod lease env path calls R2 temporary credentials directly")

    default_store = get_staging_store(environ={})
    if not isinstance(default_store, NoOpStagingStore):
        raise CheckFailed(11, "StagingStore default is not no-op when R2 is unconfigured")
    if default_store.vend_pod_credentials() != {}:
        raise CheckFailed(11, "NoOpStagingStore vends pod credentials")
    if default_store.cleanup_pod_artifacts([{"id": "pod-1", "name": "pod"}]) != []:
        raise CheckFailed(11, "NoOpStagingStore cleanup is not empty")

    r2_store_facts = facts_of(
        CloudflareR2StagingStore.vend_pod_credentials,
        CloudflareR2StagingStore.cleanup_pod_artifacts,
    )
    if not r2_store_facts.references("vend_r2_temp_credential_pod_env"):
        raise CheckFailed(11, "Cloudflare R2 staging store does not wrap temp credential vending")
    if not r2_store_facts.references("cleanup_staging_for_pods"):
        raise CheckFailed(11, "Cloudflare R2 staging store does not wrap staging cleanup")

    credential = R2TemporaryCredentials(
        access_key_id="tmp-access",
        secret_access_key="tmp-secret",  # pragma: allowlist secret
        session_token="tmp-session",
        ttl_seconds=900,
        bucket="pitwall-staging",
        permission="object-read-write",
    )
    env = credential.as_pod_env(endpoint="https://r2.example.test")
    if (
        env.get("AWS_SESSION_TOKEN") != "tmp-session"
        or env.get("R2_SESSION_TOKEN") != "tmp-session"
    ):
        raise CheckFailed(11, "R2 temporary credential pod env is missing session tokens")
    if "R2_ACCESS_KEY" in env or "R2_SECRET_KEY" in env:
        raise CheckFailed(11, "R2 pod env exposes long-lived R2 key names")

    for provider in _pod_lease_provider_fixtures(cfg):
        provider_label = _provider_label(provider)
        for env_mapping in _provider_env_mappings(provider):
            forbidden = sorted(R2_FORBIDDEN_POD_ENV_KEYS & set(env_mapping))
            if forbidden:
                raise CheckFailed(
                    11,
                    f"pod_lease provider fixture {provider_label!r} injects "
                    f"Pitwall-managed R2 credential env keys: {forbidden!r}",
                )

        strategy = _provider_r2_strategy(provider)
        if strategy and strategy not in R2_TEMP_CREDENTIAL_STRATEGIES:
            raise CheckFailed(
                11,
                f"pod_lease provider fixture {provider_label!r} uses non-temporary "
                f"R2 credential strategy {strategy!r}",
            )
        if _provider_requires_r2(provider) and not strategy:
            raise CheckFailed(
                11,
                f"pod_lease provider fixture {provider_label!r} requires R2 but "
                "does not declare a temporary credential strategy",
            )
