"""One serve engine: plan, launch, wait, attach, stop, status."""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from pitwall.api.exceptions import PitwallApiError, ServeTemplateInvalid, ServeVerificationFailed
from pitwall.config import PitwallSettings
from pitwall.models.fit import fit_options
from pitwall.models.prices import load_gpu_price_snapshot
from pitwall.personal.deadline import redact_for_display, server_binary, wrap_start_command
from pitwall.personal.gateway import GatewayPortInUse, GatewaySupervisor
from pitwall.personal.keys import ENDPOINT_KEY_ENV
from pitwall.personal.routes import RouteRunner
from pitwall.personal.state import LeaseState, PersonalLease, StateStore
from pitwall.runpod_client.pods import (
    CONTAINER_RESTART_LIMIT,
    LEGACY_V1_ALLOWED_CUDA_VERSIONS,
    RunPodError,
)
from pitwall.runpod_client.workloads import WorkloadConfig
from pitwall.runpod_credentials import MISSING_CREDENTIAL_MESSAGE, resolve_runpod_api_key
from pitwall.security.redaction import redact_text
from pitwall.serve import (
    VERIFY_TOTAL_TIMEOUT_S,
    PlanCatalogue,
    ServePlanRequest,
    ServePlanResult,
    cuda_allow_list,
    plan_catalogue_model,
    verify_served_model,
)

Cloud = Literal["secure", "community"]
_PORT = 8000
_CONTAINER_DISK_GB = 50


def _abort_code(stage: str) -> str:
    """The failure code for the exception being handled: an interruption says so."""
    if isinstance(sys.exc_info()[1], (KeyboardInterrupt, asyncio.CancelledError)):
        return "interrupted"
    return stage


class RunPodCredentialMissing(PitwallApiError):
    """No RunPod API key in the environment or runpodctl's saved credential."""

    status_code = 401
    error_code = "credential_reference_unset"

    def __init__(self) -> None:
        super().__init__(MISSING_CREDENTIAL_MESSAGE)

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "detail": MISSING_CREDENTIAL_MESSAGE}


class ServeRefused(Exception):
    def __init__(
        self, code: str, detail: str = "", *, max_spend_usd: Decimal | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.max_spend_usd = max_spend_usd


# Refusals recorded in the audit log: the budget said no.
_BUDGET_REFUSALS = frozenset({"budget_not_configured", "per_request_cap", "monthly_budget"})
_USD = Decimal("0.000001")
_ACTIVE = frozenset({"launching", "ready"})


# Lines of pod log kept on a readiness failure: enough for a crash's traceback.
FAILURE_LOG_LINES = 40


class ServeFailed(Exception):
    def __init__(
        self, code: str, pod_id: str | None = None, *, log_tail: str | None = None
    ) -> None:
        super().__init__(code if not log_tail else f"{code}\n--- pod log tail ---\n{log_tail}")
        self.code = code
        self.pod_id = pod_id
        # The container's last lines, read before termination: after it the logs are gone.
        self.log_tail = log_tail


class ServeSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    variant: str | None = None
    gpu_class: str
    gpu_count: int = Field(default=1, ge=1)
    cloud: Cloud = "community"
    ttl_minutes: int = Field(default=60, ge=5, le=10_080)
    max_usd_per_hour: Decimal = Field(gt=0)
    rate_per_second: Decimal | None = None
    route: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9._-]*$",
    )


class ServePreview(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    plan: ServePlanResult
    price_per_hour_usd: Decimal | None
    max_spend_usd: Decimal | None
    deadline_at: dt.datetime
    request_preview: dict[str, Any]


class RunPodPorts(Protocol):
    async def create_pod(self, **kwargs: Any) -> dict[str, Any]: ...

    async def get_pod(self, pod_id: str) -> dict[str, Any] | None: ...

    async def terminate_pod(self, pod_id: str) -> None: ...

    async def read_logs(self, pod_id: str, *, max_lines: int) -> str: ...


class LiveRunPod:
    """The real client functions behind the protocol."""

    def __init__(self, settings: PitwallSettings, api_key: str) -> None:
        self._settings = settings
        self._api_key = api_key

    async def create_pod(self, **kwargs: Any) -> dict[str, Any]:
        from pitwall.runpod_client.pods import create_pod_with_fallback

        cap = kwargs.get("max_cost_per_hr")
        if isinstance(cap, Decimal):
            # The single Decimal-to-float conversion: whole cents, rounded half up.
            kwargs["max_cost_per_hr"] = float(cap.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return await create_pod_with_fallback(**kwargs)

    async def get_pod(self, pod_id: str) -> dict[str, Any] | None:
        from pitwall.runpod_client import pods

        # Only a 404 is an absent pod; an unreachable RunPod raises RunPodError.
        return await pods.get_pod_strict(pod_id)

    async def terminate_pod(self, pod_id: str) -> None:
        from pitwall.runpod_client.pods import terminate_pod

        await terminate_pod(pod_id)

    async def read_logs(self, pod_id: str, *, max_lines: int) -> str:
        from pitwall.runpod_client.pod_logs import BoundedPodLogClient

        client = BoundedPodLogClient(api_key=self._api_key)
        try:
            payload = await client.read(pod_id, max_lines=max_lines, max_bytes=64 * 1024)
        finally:
            await client.aclose()
        return payload.text


class Verifier(Protocol):
    """Polls a pod's ``/v1/models`` until the served model id appears."""

    def __call__(
        self, models_url: str, served_model_id: str, *, headers: Mapping[str, str] | None = None
    ) -> Awaitable[list[str]]: ...


CudaVersions = Callable[[PitwallSettings, str], Awaitable[tuple[str, ...] | None]]


async def _default_cuda_versions(
    settings: PitwallSettings, gpu_class: str
) -> tuple[str, ...] | None:
    """Live driver versions offered for the GPU class; None when the market is unreadable."""
    from pitwall.serve import _live_cuda_versions

    return await _live_cuda_versions(None, settings, gpu_class)


class PersonalServeService:
    def __init__(
        self,
        *,
        store: StateStore,
        settings: PitwallSettings,
        catalogue: PlanCatalogue,
        runpod: RunPodPorts,
        routes: RouteRunner,
        endpoint_key: str,
        clock: Callable[[], dt.datetime] | None = None,
        prices: Callable[..., Awaitable[Any]] = load_gpu_price_snapshot,
        fit: Callable[..., Sequence[Any]] = fit_options,
        verify: Verifier = verify_served_model,
        hf_token: str | None = None,
        gateway: GatewaySupervisor | None = None,
        cuda_versions: CudaVersions | None = None,
        monthly_budget_usd: Decimal | None = None,
        per_request_max_usd: Decimal | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._catalogue = catalogue
        self._runpod = runpod
        self._routes = routes
        self._endpoint_key = endpoint_key
        self._clock = clock or (lambda: dt.datetime.now(dt.UTC))
        self._prices = prices
        self._fit = fit
        self._verify = verify
        self._hf_token = hf_token
        self._gateway = gateway
        self._cuda_versions = cuda_versions or _default_cuda_versions
        # None: no monthly budget configured, so serving is refused.
        self._monthly_budget_usd = monthly_budget_usd
        self._per_request_max_usd = per_request_max_usd

    def with_gateway(self, gateway: GatewaySupervisor) -> None:
        """Attach a gateway supervisor; start it only after planning succeeds."""
        self._gateway = gateway

    async def plan(self, spec: ServeSpec) -> ServePreview:
        existing = self._store.get(spec.route)
        if existing is not None and existing.state in {"launching", "ready"}:
            raise ServeRefused("route_exists", spec.route)
        if not self._routes.available():
            raise ServeRefused("routing_cli_missing", self._settings.pitwall_routing_cli)
        if self._routes.exists(spec.route):
            raise ServeRefused("route_exists", spec.route)
        plan = await plan_catalogue_model(
            ServePlanRequest(
                model=spec.model,
                gpu_class=spec.gpu_class,
                gpu_count=spec.gpu_count,
                variant=spec.variant,
                ttl_minutes=spec.ttl_minutes,
            ),
            settings=self._settings,
            catalogue=self._catalogue,
        )
        if plan.fit == "no":
            raise ServeRefused("does_not_fit", spec.gpu_class)
        if spec.ttl_minutes * 60 <= plan.startup_timeout_s:
            # The in-pod deadline would end the pod before the model can answer.
            startup_min = math.ceil(plan.startup_timeout_s / 60)
            raise ServeRefused(
                "ttl_below_startup",
                f"the model can take {startup_min} min to start; choose --ttl-minutes above it",
            )
        price = await self._price_for(spec)
        if price is None:
            raise ServeRefused("unpriced", "pass --rate-per-second")
        if price > spec.max_usd_per_hour:
            raise ServeRefused("price_over_cap", f"{price} > {spec.max_usd_per_hour}")
        now = self._clock()
        max_spend = (price * Decimal(spec.ttl_minutes) / Decimal(60)).quantize(_USD)
        self._check_budget(max_spend, now)
        # The preview shows argv, image, and ports; CUDA placement is resolved at launch.
        preview = self._request(spec, plan, None)
        preview["docker_start_cmd"] = redact_for_display(preview["docker_start_cmd"])
        preview_env = preview.get("env")
        if isinstance(preview_env, dict):
            preview_env.pop(ENDPOINT_KEY_ENV, None)
        return ServePreview(
            plan=plan,
            price_per_hour_usd=price,
            max_spend_usd=max_spend,
            deadline_at=now + dt.timedelta(minutes=spec.ttl_minutes),
            request_preview=preview,
        )

    async def serve(
        self,
        spec: ServeSpec,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> PersonalLease:
        report = progress or (lambda _message: None)
        try:
            preview = await self.plan(spec)
        except ServeRefused as refused:
            if refused.code in _BUDGET_REFUSALS:
                self._audit(
                    "refused",
                    route=spec.route,
                    pod_id=None,
                    max_spend_usd=refused.max_spend_usd,
                    reason=refused.code,
                )
            raise
        allowed_cuda = await self._allowed_cuda(spec)
        request = self._request(spec, preview.plan, allowed_cuda)
        if self._gateway is not None:
            report("starting gateway")
            try:
                self._gateway.start()
            except (RuntimeError, OSError) as exc:
                raise ServeRefused(
                    "gateway_start_failed",
                    f"{exc}; check PITWALL_GATEWAY_TOKEN and the gateway port"
                    if isinstance(exc, GatewayPortInUse)
                    else "check the gateway launcher and PITWALL_GATEWAY_TOKEN",
                ) from exc
        report("creating pod")
        try:
            created = await self._runpod.create_pod(**request)
        except RunPodError as exc:  # nothing was recorded; RunPod may still report a pod
            raise ServeRefused(
                "create_failed",
                f"{redact_text(str(exc))[:300]}; check `pitwall runpod pods list`",
            ) from exc
        pod_id = str(created["id"])
        endpoint = f"https://{pod_id}-{_PORT}.proxy.runpod.net/v1"
        lease: PersonalLease | None = None
        recorded = False
        try:
            lease = PersonalLease(
                route=spec.route,
                pod_id=pod_id,
                model=spec.model,
                served_model_id=preview.plan.model_id,
                engine=preview.plan.engine,
                variant=preview.plan.variant,
                image=preview.plan.image,
                gpu_class=spec.gpu_class,
                gpu_count=spec.gpu_count,
                cloud=spec.cloud,
                price_per_hour_usd=(
                    str(preview.price_per_hour_usd)
                    if preview.price_per_hour_usd is not None
                    else None
                ),
                endpoint_url=endpoint,
                key_env=ENDPOINT_KEY_ENV,
                launched_at=self._clock(),
                deadline_at=preview.deadline_at,
                state="launching",
            )
            self._store.upsert(lease)
            recorded = True
            self._audit(
                "serve",
                route=spec.route,
                pod_id=pod_id,
                max_spend_usd=preview.max_spend_usd,
                reason=None,
            )
            report("waiting for the model to answer")
        except BaseException:
            if recorded and lease is not None:
                await self._abort(lease, _abort_code("state_write_failed"))
            else:
                await self._terminate_quietly(pod_id)
            raise

        assert lease is not None
        try:
            unready = await self._wait_ready(pod_id, endpoint, preview.plan)
        except BaseException:
            await self._abort(lease, _abort_code("readiness_failed"))
            raise
        if unready is not None:
            await self._fail(lease, unready, log_tail=await self._log_tail(pod_id))

        try:
            report("attaching route")
            attached_ok = self._routes.attach(
                spec.route,
                base_url=endpoint,
                model_id=preview.plan.model_id,
                key_env=ENDPOINT_KEY_ENV,
            ).ok
        except BaseException:
            await self._abort(lease, _abort_code("route_attach_failed"))
            raise
        if not attached_ok:
            await self._fail(lease, "route_attach_failed")

        try:
            probed_ok = self._routes.probe(spec.route).ok
        except BaseException:
            await self._abort(lease, _abort_code("route_attach_failed"), remove_route=True)
            raise
        if not probed_ok:
            await self._fail(lease, "route_attach_failed", remove_route=True)

        try:
            return self._store.update(spec.route, state="ready")
        except BaseException:
            await self._abort(lease, _abort_code("state_write_failed"), remove_route=True)
            raise

    async def status(self) -> list[PersonalLease]:
        now = self._clock()
        result: list[PersonalLease] = []
        for lease in self._store.load():
            if lease.state in {"launching", "ready"}:
                try:
                    pod = await self._runpod.get_pod(lease.pod_id)
                except RunPodError:
                    result.append(lease)  # RunPod unreachable: the last known state stands
                    continue
                if pod is None:
                    # Gone without a stop: the in-pod deadline (or someone) ended it; charge up
                    # to the deadline, the most it could have run.
                    lease = self._settle(
                        lease, state="gone", end=min(now, lease.deadline_at), action="gone"
                    )
                elif now >= lease.deadline_at and await self._terminate_quietly(lease.pod_id):
                    self._remove_quietly(lease.route)
                    lease = self._settle(
                        lease,
                        state="stopped",
                        failure="terminated_late",
                        end=now,
                        action="stop",
                    )
            result.append(lease)
        return result

    async def stop(self, route: str) -> PersonalLease:
        lease = self._store.get(route)
        if lease is None:
            raise KeyError(route)
        await self._terminate_quietly(lease.pod_id)
        self._remove_quietly(route)
        return self._settle(lease, state="stopped", end=self._clock(), action="stop")

    async def logs(self, route: str, *, max_lines: int = 40) -> str:
        lease = self._store.get(route)
        if lease is None:
            raise KeyError(route)
        return await self._runpod.read_logs(lease.pod_id, max_lines=max_lines)

    async def _price_for(self, spec: ServeSpec) -> Decimal | None:
        if spec.rate_per_second is not None:
            return (spec.rate_per_second * Decimal(3600)).quantize(Decimal("0.000001"))
        snapshot = await self._prices(cloud=spec.cloud, settings=self._settings)
        variant = self._catalogue.dossier_variant(spec.model, spec.variant)
        options = self._fit(
            variant,
            gpu_types=snapshot.gpu_types,
            ttl_minutes=spec.ttl_minutes,
            cloud=spec.cloud,
        )
        for option in options:
            if option.gpu_class == spec.gpu_class and option.gpu_count == spec.gpu_count:
                return cast(Decimal | None, option.price_per_hour)
        return None

    async def _allowed_cuda(self, spec: ServeSpec) -> list[str] | None:
        variant = self._catalogue.dossier_variant(spec.model, spec.variant)
        min_cuda = getattr(variant, "min_cuda", None)
        if not isinstance(min_cuda, str):
            return None
        offered = await self._cuda_versions(self._settings, spec.gpu_class)
        if offered is not None:
            # The pod create call can only request the versions in RunPod's REST enum;
            # a class offered only above it (13.1+) cannot be targeted, so refuse here.
            offered = tuple(v for v in offered if v in LEGACY_V1_ALLOWED_CUDA_VERSIONS)
        try:
            return cuda_allow_list(min_cuda, offered)
        except ServeTemplateInvalid as exc:
            raise ServeRefused("cuda_unavailable", str(exc)) from exc

    def _request(
        self, spec: ServeSpec, plan: ServePlanResult, allowed_cuda: list[str] | None
    ) -> dict[str, Any]:
        ttl_seconds = spec.ttl_minutes * 60
        start = wrap_start_command(
            server_binary(plan.engine, plan.image),
            plan.argv,
            ttl_seconds=ttl_seconds,
        )
        variant = self._catalogue.dossier_variant(spec.model, spec.variant)
        env = {ENDPOINT_KEY_ENV: self._endpoint_key}
        if self._hf_token and getattr(variant, "gated", False):
            env["HF_TOKEN"] = self._hf_token
        return {
            "name": f"pitwall-{spec.route}",
            "template_id": None,
            "image_name": plan.image,
            "workload": WorkloadConfig(
                name=f"pitwall-{spec.route}",
                capability=spec.route,
                gpu_types=[spec.gpu_class],
                gpu_count=spec.gpu_count,
                container_disk_gb=_CONTAINER_DISK_GB,
                cloud_type=spec.cloud.upper(),
                allowed_cuda_versions=allowed_cuda,
                ports=f"{_PORT}/http",
            ),
            "env": env,
            "cloud_type_override": spec.cloud.upper(),
            "docker_entrypoint": ["sh", "-c"],
            "docker_start_cmd": start,
            # A Decimal here; LiveRunPod.create_pod is the one place it becomes the SDK's float.
            "max_cost_per_hr": spec.max_usd_per_hour,
            "wait_for_readiness": False,
        }

    async def _wait_ready(self, pod_id: str, endpoint: str, plan: ServePlanResult) -> str | None:
        """Wait for the served model; return a failure code, or None once it answers.

        Each verify attempt is bounded by VERIFY_TOTAL_TIMEOUT_S, so the attempt count
        spans the dossier's startup budget. Between attempts the pod record shows a pod
        that vanished or a container that keeps restarting (RunPod keeps a crash-looping
        pod RUNNING; only the container uptime falling back reveals each restart).
        """
        attempts = max(1, math.ceil(plan.startup_timeout_s / VERIFY_TOTAL_TIMEOUT_S))
        last_uptime: int | None = None
        restarts = 0
        for _attempt in range(attempts):
            try:
                await self._verify(
                    f"{endpoint}/models",
                    plan.model_id,
                    headers={"Authorization": f"Bearer {self._endpoint_key}"},
                )
                return None
            except ServeVerificationFailed:
                pass
            try:
                pod = await self._runpod.get_pod(pod_id)
            except RunPodError:
                continue  # RunPod unreachable between attempts: keep waiting
            if pod is None:
                return "pod_gone"
            runtime = pod.get("runtime")
            uptime = runtime.get("uptime") if isinstance(runtime, dict) else None
            if isinstance(uptime, int):
                if last_uptime is not None and uptime < last_uptime:
                    restarts += 1
                last_uptime = uptime
            if restarts >= CONTAINER_RESTART_LIMIT:
                return "container_restarting"
        return "readiness_timeout"

    async def _fail(
        self,
        lease: PersonalLease,
        code: str,
        *,
        remove_route: bool = False,
        log_tail: str | None = None,
    ) -> None:
        await self._abort(lease, code, remove_route=remove_route)
        raise ServeFailed(code, lease.pod_id, log_tail=log_tail)

    async def _log_tail(self, pod_id: str) -> str | None:
        """The pod's last log lines, redacted; None when RunPod cannot return them."""
        try:
            text = await self._runpod.read_logs(pod_id, max_lines=FAILURE_LOG_LINES)
        except Exception:  # reason: diagnostics must never block terminating the pod
            return None
        return redact_text(text).strip() or None

    async def _abort(
        self,
        lease: PersonalLease,
        code: str,
        *,
        remove_route: bool = False,
    ) -> None:
        await self._terminate_quietly(lease.pod_id)
        if remove_route:
            self._remove_quietly(lease.route)
        try:
            self._settle(lease, state="failed", failure=code, end=self._clock(), action="failed")
        except Exception:  # reason: settling is best effort while aborting; the abort reason stands
            return

    def _check_budget(self, max_spend: Decimal, now: dt.datetime) -> None:
        """Refuse a lease the local budget cannot cover; reservations count like the gate's."""
        monthly = self._monthly_budget_usd
        if monthly is None:
            raise ServeRefused(
                "budget_not_configured",
                "export PITWALL_MONTHLY_BUDGET_USD before serving",
                max_spend_usd=max_spend,
            )
        per_request = self._per_request_max_usd
        if per_request is not None and max_spend > per_request:
            raise ServeRefused(
                "per_request_cap", f"{max_spend} > {per_request}", max_spend_usd=max_spend
            )
        month = now.strftime("%Y-%m")
        committed = self._store.month_spend(month) + sum(
            (
                _lease_cost(lease, lease.deadline_at)
                for lease in self._store.load()
                if lease.state in _ACTIVE
                and lease.accrued_usd is None
                and lease.launched_at.strftime("%Y-%m") == month
            ),
            Decimal("0"),
        )
        if committed + max_spend > monthly:
            raise ServeRefused(
                "monthly_budget",
                f"{committed} spent or reserved this month + {max_spend} > {monthly}",
                max_spend_usd=max_spend,
            )

    def _settle(
        self,
        lease: PersonalLease,
        *,
        state: LeaseState,
        end: dt.datetime,
        action: str,
        failure: str | None = None,
    ) -> PersonalLease:
        """Move a lease to a terminal state, recording its cost in the ledger exactly once."""
        changes: dict[str, object] = {"state": state}
        if failure is not None:
            changes["failure"] = failure
        if lease.accrued_usd is not None:  # already settled: never charge twice
            return self._store.update(lease.route, **changes)
        accrued = _lease_cost(lease, end)
        self._store.record_spend(lease.launched_at.strftime("%Y-%m"), accrued)
        settled = self._store.update(lease.route, accrued_usd=str(accrued), **changes)
        self._audit(
            action, route=lease.route, pod_id=lease.pod_id, accrued_usd=accrued, reason=failure
        )
        return settled

    def _audit(
        self,
        action: str,
        *,
        route: str,
        pod_id: str | None,
        reason: str | None,
        max_spend_usd: Decimal | None = None,
        accrued_usd: Decimal | None = None,
    ) -> None:
        record: dict[str, str | None] = {
            "ts": self._clock().isoformat(),
            "action": action,
            "route": route,
            "pod_id": pod_id,
        }
        if max_spend_usd is not None:
            record["max_spend_usd"] = str(max_spend_usd)
        if accrued_usd is not None:
            record["accrued_usd"] = str(accrued_usd)
        record["reason"] = reason
        self._store.append_audit(record)

    def _remove_quietly(self, route: str) -> None:
        try:
            self._routes.remove(route)
        except (
            Exception
        ):  # reason: cleanup after a failed launch must never mask the original error
            return

    async def _terminate_quietly(self, pod_id: str) -> bool:
        try:
            await self._runpod.terminate_pod(pod_id)
        except (
            Exception
        ):  # reason: an unreachable RunPod reports False and the caller keeps the lease
            return False
        return True


def _lease_cost(lease: PersonalLease, end: dt.datetime) -> Decimal:
    """What a lease costs if it runs from launch until ``end`` at its recorded hourly price."""
    if lease.price_per_hour_usd is None:
        return Decimal("0")
    hours = Decimal(max((end - lease.launched_at).total_seconds(), 0)) / Decimal(3600)
    return (Decimal(lease.price_per_hour_usd) * hours).quantize(_USD)


def build_personal_service(settings: PitwallSettings) -> PersonalServeService:
    """Build the shared personal service from local state and process credentials."""
    from pitwall.models import load_catalogue
    from pitwall.personal.keys import ensure_endpoint_key, read_endpoint_key

    # Resolve the provider key first: a missing credential must not leave personal state behind.
    runpod_key, _source = resolve_runpod_api_key(os.environ)
    if runpod_key is None:
        raise RunPodCredentialMissing
    store = StateStore()
    ensure_endpoint_key(store.root)
    key = read_endpoint_key(store.root) or ""
    env = dict(os.environ)
    env.setdefault(ENDPOINT_KEY_ENV, key)
    return PersonalServeService(
        store=store,
        settings=settings,
        catalogue=load_catalogue(),
        runpod=LiveRunPod(settings, runpod_key),
        routes=RouteRunner(settings.pitwall_routing_cli, env=env),
        endpoint_key=key,
        hf_token=os.environ.get("HF_TOKEN") or None,
        # Personal mode requires an explicit monthly budget; the settings default is not one.
        monthly_budget_usd=(
            settings.pitwall_monthly_budget_usd
            if os.environ.get("PITWALL_MONTHLY_BUDGET_USD", "").strip()
            else None
        ),
        per_request_max_usd=settings.pitwall_per_request_max_usd,
    )
