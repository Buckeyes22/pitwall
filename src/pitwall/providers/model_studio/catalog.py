"""Model Studio catalog rules: the one implementation shared by the broker and Agent Routing."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Any

KIND = "model-studio"
AUTOMATION_ENV = "MODEL_STUDIO_TOKEN_PLAN_AUTOMATION"
AUTOMATION_ACCEPT = "accept"
EXHAUSTED_MESSAGE = "token-plan quota has been exhausted"
PROTOCOLS = ("openai", "anthropic")
ENDPOINT_FIELDS = frozenset(
    {
        "kind",
        "plan",
        "region",
        "workspace",
        "protocol",
        "tier",
        "concurrency",
        "renewsOn",
        "apiKeyEnv",
        "baseUrl",
        "tokenPlanAutomation",
    }
)
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_SETTINGS_KEYS = frozenset(
    {"plan", "tier", "region", "workspace", "model", "protocol", "renews_on", "automation"}
)
_WORKSPACE = re.compile(r"[A-Za-z0-9-]{1,64}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ModelStudioConfigError(ValueError):
    """Named configuration refusal; messages never carry key values."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@lru_cache(maxsize=1)
def load_catalog() -> Mapping[str, Any]:
    raw = (
        resources.files("pitwall.providers.model_studio")
        .joinpath("catalog.json")
        .read_text("utf-8")
    )
    catalog: Mapping[str, Any] = json.loads(raw)
    return catalog


def _plan(plan: str) -> Mapping[str, Any]:
    plans = load_catalog()["plans"]
    if plan not in plans:
        raise ModelStudioConfigError(
            "unknown_plan", f"plan must be one of {', '.join(sorted(plans))}"
        )
    record: Mapping[str, Any] = plans[plan]
    return record


def is_token_plan(plan: str) -> bool:
    return bool(_plan(plan)["family"] == "token-plan")


def model_record(model: str) -> Mapping[str, Any]:
    models = load_catalog()["models"]
    if model not in models:
        raise ModelStudioConfigError(
            "model_not_in_catalog", f"model {model!r} is not in the Model Studio catalog"
        )
    record: Mapping[str, Any] = models[model]
    return record


def check_model(plan: str, model: str) -> Mapping[str, Any]:
    record = model_record(model)
    edition = _plan(plan)["edition"]
    eligible = (
        record["eligibility"]["payAsYouGo"] if edition is None else record["eligibility"][edition]
    )
    if not eligible:
        raise ModelStudioConfigError(
            "model_not_eligible", f"model {model!r} is not included in {plan}"
        )
    if record["kind"] != "text":
        raise ModelStudioConfigError(
            "model_not_text",
            f"model {model!r} is a {record['kind']} model; only text models dispatch",
        )
    return record


def route_limits(model: str) -> dict[str, int] | None:
    record = model_record(model)
    if record.get("maxInputTokens") is None or record.get("maxOutputTokens") is None:
        return None
    return {"context": int(record["maxInputTokens"]), "output": int(record["maxOutputTokens"])}


def effort_values(model: str) -> tuple[str, ...]:
    effort = model_record(model).get("effort")
    return tuple(str(value) for value in effort["values"]) if effort else ()


def validate_endpoint(raw: Mapping[str, Any], field: str) -> dict[str, Any]:
    """Validate and normalize one Agent Routing `model-studio` endpoint object."""
    unknown = set(raw) - ENDPOINT_FIELDS
    if unknown:
        raise ModelStudioConfigError(
            "invalid_endpoint", f"{field} has unknown fields: {', '.join(sorted(unknown))}"
        )
    plan = raw.get("plan")
    if not isinstance(plan, str):
        raise ModelStudioConfigError("unknown_plan", f"{field}.plan is required")
    record = _plan(plan)
    token_plan = record["family"] == "token-plan"
    region = raw.get("region", record["regions"][0] if token_plan else None)
    if region not in record["regions"]:
        if token_plan:
            raise ModelStudioConfigError(
                "region_not_allowed",
                f"{field}: Token Plan is served only from {record['regions'][0]}",
            )
        raise ModelStudioConfigError(
            "unknown_region", f"{field}.region must be one of {', '.join(record['regions'])}"
        )
    protocol = raw.get("protocol", "openai")
    if protocol not in PROTOCOLS:
        raise ModelStudioConfigError(
            "invalid_endpoint", f"{field}.protocol must be openai or anthropic"
        )
    key_env = raw.get("apiKeyEnv")
    if not isinstance(key_env, str) or _ENV_NAME.fullmatch(key_env) is None:
        raise ModelStudioConfigError(
            "invalid_endpoint", f"{field}.apiKeyEnv must name an environment variable"
        )
    normalized: dict[str, Any] = {
        "kind": KIND,
        "plan": plan,
        "region": region,
        "protocol": protocol,
        "apiKeyEnv": key_env,
    }
    workspace = raw.get("workspace")
    if workspace is not None:
        if token_plan:
            raise ModelStudioConfigError(
                "invalid_endpoint", f"{field}.workspace applies only to pay-as-you-go"
            )
        if not isinstance(workspace, str) or _WORKSPACE.fullmatch(workspace) is None:
            raise ModelStudioConfigError(
                "invalid_endpoint", f"{field}.workspace must be a workspace id"
            )
        normalized["workspace"] = workspace
    tiers: Mapping[str, Any] = record.get("tiers") or {}
    tier = raw.get("tier")
    if tier is not None:
        if tier not in tiers:
            allowed = ", ".join(tiers) if tiers else "none (tiers apply only to Token Plan)"
            raise ModelStudioConfigError(
                "invalid_endpoint", f"{field}.tier must be one of {allowed}"
            )
        normalized["tier"] = tier
    elif token_plan:
        raise ModelStudioConfigError("invalid_endpoint", f"{field}.tier is required for {plan}")
    concurrency = raw.get("concurrency")
    if concurrency is None and tier is not None and tiers[tier].get("concurrency"):
        concurrency = tiers[tier]["concurrency"][1]
    if concurrency is not None:
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
            raise ModelStudioConfigError(
                "invalid_endpoint", f"{field}.concurrency must be a positive integer"
            )
        normalized["concurrency"] = concurrency
    renews_on = raw.get("renewsOn")
    if renews_on is not None:
        if not token_plan:
            raise ModelStudioConfigError(
                "invalid_endpoint", f"{field}.renewsOn applies only to Token Plan"
            )
        if not isinstance(renews_on, str) or _DATE.fullmatch(renews_on) is None:
            raise ModelStudioConfigError(
                "invalid_endpoint", f"{field}.renewsOn must be a YYYY-MM-DD date"
            )
        dt.date.fromisoformat(renews_on)
        normalized["renewsOn"] = renews_on
    automation = raw.get("tokenPlanAutomation")
    if automation is not None:
        if not token_plan or automation != AUTOMATION_ACCEPT:
            raise ModelStudioConfigError(
                "invalid_endpoint",
                f"{field}.tokenPlanAutomation must be 'accept' and applies only to Token Plan",
            )
        normalized["tokenPlanAutomation"] = automation
    derived = base_url(normalized)
    if "baseUrl" in raw and raw["baseUrl"] != derived:
        raise ModelStudioConfigError(
            "invalid_endpoint_pairing",
            f"{field}.baseUrl is derived from plan, region, workspace, and protocol; remove it",
        )
    normalized["baseUrl"] = derived
    return normalized


def provider_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    raw = config.get("model_studio")
    if not isinstance(raw, Mapping):
        raise ModelStudioConfigError(
            "invalid_config", "provider config needs a model_studio object"
        )
    unknown = set(raw) - _SETTINGS_KEYS
    if unknown:
        raise ModelStudioConfigError(
            "invalid_config", f"model_studio has unknown fields: {', '.join(sorted(unknown))}"
        )
    plan = raw.get("plan")
    if not isinstance(plan, str):
        raise ModelStudioConfigError("unknown_plan", "model_studio.plan is required")
    record = _plan(plan)
    token_plan = record["family"] == "token-plan"
    region = raw.get("region") or (record["regions"][0] if token_plan else None)
    if region not in record["regions"]:
        if token_plan:
            raise ModelStudioConfigError(
                "region_not_allowed", f"Token Plan is served only from {record['regions'][0]}"
            )
        raise ModelStudioConfigError(
            "unknown_region", f"model_studio.region must be one of {', '.join(record['regions'])}"
        )
    settings: dict[str, Any] = {"plan": plan, "region": region, "protocol": "openai"}
    tier = raw.get("tier")
    tiers: Mapping[str, Any] = record.get("tiers") or {}
    if token_plan and tier not in tiers:
        raise ModelStudioConfigError(
            "invalid_config", f"model_studio.tier must be one of {', '.join(tiers)}"
        )
    if tier is not None:
        if tier not in tiers:
            raise ModelStudioConfigError(
                "invalid_config", "model_studio.tier applies only to Token Plan"
            )
        settings["tier"] = tier
    workspace = raw.get("workspace")
    if workspace is not None:
        if token_plan or not isinstance(workspace, str) or _WORKSPACE.fullmatch(workspace) is None:
            raise ModelStudioConfigError(
                "invalid_config",
                "model_studio.workspace must be a workspace id (pay-as-you-go only)",
            )
        settings["workspace"] = workspace
    renews_on = raw.get("renews_on")
    if renews_on is not None:
        if not token_plan or not isinstance(renews_on, str) or _DATE.fullmatch(renews_on) is None:
            raise ModelStudioConfigError(
                "invalid_config",
                "model_studio.renews_on must be a YYYY-MM-DD date (Token Plan only)",
            )
        dt.date.fromisoformat(renews_on)
        settings["renews_on"] = renews_on
    automation = raw.get("automation")
    if automation is not None:
        if automation != AUTOMATION_ACCEPT:
            raise ModelStudioConfigError(
                "invalid_config", "model_studio.automation must be 'accept'"
            )
        settings["automation"] = automation
    model = raw.get("model")
    if not isinstance(model, str) or not model:
        raise ModelStudioConfigError("invalid_config", "model_studio.model is required")
    check_model(plan, model)
    settings["model"] = model
    base_url(settings)
    return settings


def base_url(settings: Mapping[str, Any], protocol: str | None = None) -> str:
    catalog = load_catalog()
    record = _plan(str(settings["plan"]))
    protocol = protocol or str(settings.get("protocol", "openai"))
    if record["family"] == "token-plan":
        return str(record["baseUrls"][protocol])
    region = catalog["regions"][str(settings["region"])]
    workspace = settings.get("workspace")
    if workspace:
        host = str(region["workspaceHost"]).format(workspace=workspace)
    elif region.get("legacyHost"):
        host = str(region["legacyHost"])
    else:
        raise ModelStudioConfigError(
            "workspace_required",
            f"region {settings['region']} has no shared DashScope host; set a workspace id",
        )
    return f"https://{host}{catalog['paths'][protocol]}"


def check_key(plan: str, key: str) -> None:
    prefix = str(load_catalog()["plans"]["token-plan-personal"]["keyPrefix"])
    token_key = key.startswith(prefix)
    if is_token_plan(plan) and not token_key:
        raise ModelStudioConfigError(
            "key_plan_mismatch",
            f"plan {plan} needs a Token Plan key (prefix {prefix}); "
            "another key would be billed pay-as-you-go",
        )
    if token_key and not is_token_plan(plan):
        raise ModelStudioConfigError(
            "key_plan_mismatch",
            f"a Token Plan key (prefix {prefix}) works only with a Token Plan plan",
        )


def automation_accepted(settings: Mapping[str, Any], environ: Mapping[str, str]) -> bool:
    return (
        settings.get("automation") == AUTOMATION_ACCEPT
        or environ.get(AUTOMATION_ENV, "").strip() == AUTOMATION_ACCEPT
    )


def endpoint_automation_accepted(endpoint: Mapping[str, Any]) -> bool:
    return endpoint.get("tokenPlanAutomation") == AUTOMATION_ACCEPT


def require_automation(settings: Mapping[str, Any], environ: Mapping[str, str]) -> None:
    if is_token_plan(str(settings["plan"])) and not automation_accepted(settings, environ):
        raise ModelStudioConfigError(
            "automation_not_accepted",
            f"the Token Plan terms allow interactive use only; set {AUTOMATION_ENV}=accept "
            "(or model_studio.automation: accept) to accept the risk of broker use",
        )


def pricing_config(settings: Mapping[str, Any]) -> dict[str, Any]:
    if is_token_plan(str(settings["plan"])):
        return {"kind": "zero"}
    catalog = load_catalog()
    model = catalog["models"][str(settings["model"])]
    prices = model.get("prices")
    if not catalog["regions"][str(settings["region"])]["priced"] or not prices:
        raise ModelStudioConfigError(
            "unpriced",
            f"the catalog has no {settings['region']} price for {settings['model']}; set the provider cost explicitly",
        )
    cost: dict[str, Any] = {
        "kind": "per_token",
        "per_million_input_tokens": prices["input"],
        "per_million_output_tokens": prices["output"],
    }
    if prices.get("cachedInput") is not None:
        cost["per_million_cached_input_tokens"] = prices["cachedInput"]
    if prices.get("tiers"):
        cost["input_tiers"] = [
            {
                "above_input_tokens": tier["aboveInputTokens"],
                "per_million_input_tokens": tier["input"],
                "per_million_output_tokens": tier["output"],
                **(
                    {"per_million_cached_input_tokens": tier["cachedInput"]}
                    if tier.get("cachedInput") is not None
                    else {}
                ),
            }
            for tier in prices["tiers"]
        ]
    if model.get("maxOutputTokens"):
        cost["default_max_output_tokens"] = model["maxOutputTokens"]
    return cost


def _code_and_message(body: str) -> tuple[str | None, str]:
    try:
        payload = json.loads(body)
    except ValueError:
        return None, body
    if not isinstance(payload, Mapping):
        return None, body
    nested = payload.get("error")
    source = nested if isinstance(nested, Mapping) else payload
    code = source.get("code")
    message = source.get("message")
    return (str(code) if code else None), (str(message) if message else body)


def classify_error(status: int, body: str) -> tuple[str, int | None] | None:
    code, message = _code_and_message(body)
    entries = load_catalog()["errors"]
    for entry in entries:
        if "code" in entry and entry["status"] == status and entry["code"] == code:
            return str(entry["classification"]), entry["cooldownSeconds"]
    lowered = message.lower()
    for entry in entries:
        if "message" in entry and entry["status"] == status and entry["message"] in lowered:
            return str(entry["classification"]), entry["cooldownSeconds"]
    return None


def credits_window(renews_on: str, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    anchor = dt.datetime.combine(dt.date.fromisoformat(renews_on), dt.time(), tzinfo=dt.UTC)
    cycle = dt.timedelta(days=30)
    start = anchor + ((now - anchor) // cycle) * cycle
    return start, start + cycle


def next_renewal(settings: Mapping[str, Any], now: dt.datetime) -> dt.datetime | None:
    renews_on = settings.get("renews_on")
    return credits_window(str(renews_on), now)[1] if renews_on else None


def rewrite_proxy_body(body: bytes, settings: Mapping[str, Any]) -> bytes:
    """Pin the model, bound reasoning by the admitted ceiling, and request streaming usage."""
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict):
        return body
    record = load_catalog()["models"][str(settings["model"])]
    payload["model"] = settings["model"]
    if (
        not record.get("maxTokensIncludesReasoning")
        and "max_tokens" in payload
        and "max_completion_tokens" not in payload
    ):
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    if payload.get("stream") is True:
        options = payload.get("stream_options")
        payload["stream_options"] = {
            **(options if isinstance(options, Mapping) else {}),
            "include_usage": True,
        }
    return json.dumps(payload, separators=(",", ":")).encode()
