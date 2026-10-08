"""Model Studio OpenAPI reads (ACS3-HMAC-SHA256); used only for quota and billing evidence."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib import request as urllib_request
from urllib.parse import quote

import httpx

from pitwall.providers.model_studio.catalog import load_catalog

ACCESS_KEY_ID_ENV = "ALIBABA_CLOUD_ACCESS_KEY_ID"
ACCESS_KEY_SECRET_ENV = "ALIBABA_CLOUD_ACCESS_KEY_SECRET"
_ALGORITHM = "ACS3-HMAC-SHA256"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _encode(value: str) -> str:
    return quote(value, safe="-_.~")


def acs3_authorization(
    method: str,
    host: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    body: bytes,
    *,
    access_key_id: str,
    access_key_secret: str,
) -> str:
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_headers = {key.lower(): value.strip() for key, value in headers.items()}
    canonical_headers["host"] = host
    canonical_headers["x-acs-content-sha256"] = payload_hash
    names = sorted(
        name
        for name in canonical_headers
        if name in {"host", "content-type"} or name.startswith("x-acs-")
    )
    header_block = "".join(f"{name}:{canonical_headers[name]}\n" for name in names)
    signed = ";".join(names)
    query_block = "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(query.items()))
    canonical = "\n".join(
        [method, quote(path or "/", safe="/-_.~"), query_block, header_block, signed, payload_hash]
    )
    string_to_sign = f"{_ALGORITHM}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    signature = hmac.new(
        access_key_secret.encode(), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return f"{_ALGORITHM} Credential={access_key_id},SignedHeaders={signed},Signature={signature}"


@dataclass(frozen=True, slots=True)
class SubscriptionStats:
    window_start: dt.datetime
    reset_at: dt.datetime
    total_credits: Decimal
    remaining_credits: Decimal


@dataclass(frozen=True, slots=True)
class SignedRequest:
    host: str
    path: str
    query: Mapping[str, str]
    headers: Mapping[str, str]


def signed_request(
    environ: Mapping[str, str],
    *,
    action: str,
    path: str,
    query: Mapping[str, str],
    now: dt.datetime,
    nonce: str | None,
) -> SignedRequest | None:
    """Build one signed GET, or None when the AccessKey pair is not configured."""
    key_id = environ.get(ACCESS_KEY_ID_ENV, "")
    secret = environ.get(ACCESS_KEY_SECRET_ENV, "")
    if not key_id or not secret:
        return None
    spec = load_catalog()["openApi"]
    host = str(spec["host"])
    headers = {
        "x-acs-action": action,
        "x-acs-version": str(spec["version"]),
        "x-acs-date": now.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "x-acs-signature-nonce": nonce or str(uuid.uuid4()),
    }
    authorization = acs3_authorization(
        "GET", host, path, query, headers, b"", access_key_id=key_id, access_key_secret=secret
    )
    return SignedRequest(
        host,
        path,
        dict(query),
        {
            **headers,
            "x-acs-content-sha256": _EMPTY_SHA256,
            "Authorization": authorization,
            "Accept": "application/json",
        },
    )


_STATS_ACTION = "GetSubscriptionStats"
_STATS_PATH = "/tokenplan/subscription/stats"


def parse_subscription_stats(payload: object) -> SubscriptionStats | None:
    data = payload.get("Data") if isinstance(payload, Mapping) else None
    items = data.get("Items") if isinstance(data, Mapping) else None
    if not isinstance(items, list) or not items:
        return None
    total = sum((Decimal(str(item.get("SeatCredits") or 0)) for item in items), Decimal(0))
    remaining = sum(
        (Decimal(str(item.get("SeatRemainingCredits") or 0)) for item in items), Decimal(0)
    )
    reset_at = dt.datetime.fromtimestamp(
        min(int(item["SeatRefreshTime"]) for item in items) / 1000, tz=dt.UTC
    )
    return SubscriptionStats(reset_at - dt.timedelta(days=30), reset_at, total, remaining)


async def _signed_get(
    environ: Mapping[str, str],
    *,
    action: str,
    path: str,
    query: Mapping[str, str],
    now: dt.datetime,
    transport: httpx.AsyncBaseTransport | None,
    nonce: str | None,
) -> Mapping[str, object] | None:
    signed = signed_request(environ, action=action, path=path, query=query, now=now, nonce=nonce)
    if signed is None:
        return None
    async with httpx.AsyncClient(
        base_url=f"https://{signed.host}", timeout=15.0, transport=transport
    ) as client:
        response = await client.get(
            signed.path, params=dict(signed.query), headers=dict(signed.headers)
        )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, Mapping) else None


async def get_subscription_stats(
    environ: Mapping[str, str],
    *,
    now: dt.datetime,
    transport: httpx.AsyncBaseTransport | None = None,
    nonce: str | None = None,
) -> SubscriptionStats | None:
    payload = await _signed_get(
        environ,
        action=_STATS_ACTION,
        path=_STATS_PATH,
        query={},
        now=now,
        transport=transport,
        nonce=nonce,
    )
    return parse_subscription_stats(payload)


def get_subscription_stats_sync(
    environ: Mapping[str, str],
    *,
    now: dt.datetime,
    nonce: str | None = None,
    opener: Callable[..., Any] = urllib_request.urlopen,
    timeout: float = 10.0,
) -> SubscriptionStats | None:
    """The same read for the synchronous Agent Routing usage reader (urllib transport)."""
    signed = signed_request(
        environ, action=_STATS_ACTION, path=_STATS_PATH, query={}, now=now, nonce=nonce
    )
    if signed is None:
        return None
    outbound = urllib_request.Request(
        f"https://{signed.host}{signed.path}", headers=dict(signed.headers), method="GET"
    )
    with opener(outbound, timeout=timeout) as response:
        payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
    return parse_subscription_stats(payload)


async def get_billing_month_to_date(
    environ: Mapping[str, str],
    *,
    model: str,
    now: dt.datetime,
    transport: httpx.AsyncBaseTransport | None = None,
    nonce: str | None = None,
) -> Decimal | None:
    query = {
        "billMonth": now.strftime("%Y-%m"),
        "groupBy": json.dumps([{"code": "BASE_MODEL"}], separators=(",", ":")),
    }
    payload = await _signed_get(
        environ,
        action="GetBillingOverview",
        path="/modelstudio/billing/overview",
        query=query,
        now=now,
        transport=transport,
        nonce=nonce,
    )
    if payload is None:
        return None
    data = payload.get("data")
    groups = data.get("groups") if isinstance(data, Mapping) else None
    for group in groups if isinstance(groups, list) else []:
        if isinstance(group, Mapping) and group.get("key") == model:
            return Decimal(str(group.get("amount") or "0"))
    return Decimal(0)
