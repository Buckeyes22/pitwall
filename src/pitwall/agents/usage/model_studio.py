"""Model Studio Token Plan usage: the provider package's stats read and the local lockout."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib import error

from pitwall.providers.model_studio import catalog, openapi

from .. import endpoints
from .accounts import Account
from .rows import Opener, ReadError, Reading, Unmeasured, Window, iso_utc, parse_instant, percent


def read(
    account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener
) -> Reading:
    tier = next(
        (
            str(endpoint["tier"]).capitalize()
            for _name, endpoint in account.endpoints
            if endpoint.get("tier")
        ),
        "",
    )
    for name, _endpoint in account.endpoints:
        until = endpoints.read_lockout(env, name, now=now)
        if until is not None:
            return Reading(
                tier=tier,
                windows=(Window("30d", 100, iso_utc(parse_instant(until))),),
                detail="Credits exhausted",
                limit_reached=True,
            )
    try:
        stats = openapi.get_subscription_stats_sync(env, now=now, opener=opener)
    except error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ReadError(f"HTTP {code}") from None
    except error.URLError, OSError:
        raise ReadError("network failure") from None
    except ValueError, KeyError, TypeError:
        raise ReadError("unexpected response") from None
    if stats is None:
        renews = next(
            (
                str(endpoint["renewsOn"])
                for _name, endpoint in account.endpoints
                if endpoint.get("renewsOn")
            ),
            None,
        )
        if renews is None:
            raise Unmeasured("usage needs an AccessKey pair")
        renewal = catalog.credits_window(renews, now)[1].date().isoformat()
        raise Unmeasured(f"usage needs an AccessKey pair; renews {renewal}")
    used = (
        percent(float((stats.total_credits - stats.remaining_credits) / stats.total_credits * 100))
        if stats.total_credits > 0
        else None
    )
    return Reading(
        tier=tier,
        windows=(Window("30d", used, iso_utc(stats.reset_at)),),
        detail=f"{stats.remaining_credits:.0f} of {stats.total_credits:.0f} Credits remaining",
    )
