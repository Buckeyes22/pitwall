"""Shared hosted-account budget (port of ``account-budget.ts``).

Aliases that share an ``accountGroup`` share one persisted reservation ledger guarded by
``flock``. The state file layout matches the packaged Pi extension, so both sides account against
the same reservations. Reservations whose owner process died become unknown liabilities rather
than silently freeing budget; only an explicit release clears them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pitwall.workbench.admission import (
    lock_exclusive,
    open_private_lock,
    prepare_private_directory,
    require_linux,
)
from pitwall.workbench.runtime_settings import is_safe_integer
from pitwall.workbench.state_dir import workbench_state_dir

_GROUP_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
UnknownUsage = Literal["hold", "release"]


def default_account_budget_dir() -> Path:
    return workbench_state_dir("account-budgets")


class AccountBudgetError(RuntimeError):
    """A reservation was rejected or the persisted state is not trustworthy (``code: reason``)."""


@dataclass(frozen=True)
class AccountBudgetPolicy:
    account_group: str
    max_concurrent: int
    in_flight_token_budget: int
    unknown_usage: UnknownUsage


@dataclass(frozen=True)
class AccountReservation:
    reservation_id: str
    reserved_tokens: int


@dataclass(frozen=True)
class Reconciliation:
    held: bool
    reserved_tokens: int
    reported_tokens: int | None
    overrun_tokens: int | None


def _validate_policy(policy: AccountBudgetPolicy) -> None:
    if not _GROUP_PATTERN.fullmatch(policy.account_group):
        raise ValueError("account budget requires a valid accountGroup")
    if not is_safe_integer(policy.max_concurrent) or policy.max_concurrent < 1:
        raise ValueError("account budget maxConcurrent must be positive")
    if not is_safe_integer(policy.in_flight_token_budget) or policy.in_flight_token_budget < 1:
        raise ValueError("account budget inFlightTokenBudget must be positive")
    if policy.unknown_usage not in ("hold", "release"):
        raise ValueError("account budget unknownUsage must be hold or release")


def usage_tokens(usage: Mapping[str, Any] | None) -> int | None:
    """Total tokens from provider usage, or ``None`` when it is missing or not exact."""
    if not isinstance(usage, Mapping):
        return None

    def token_count(name: str) -> int | None:
        value = usage.get(name)
        if isinstance(value, int | float) and is_safe_integer(value) and value >= 0:
            return int(value)
        return None

    total = token_count("totalTokens")
    if total is not None:
        return total
    prompt, completion = token_count("input"), token_count("output")
    if prompt is None or completion is None:
        return None
    return prompt + completion if is_safe_integer(prompt + completion) else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _invalid(reason: str) -> AccountBudgetError:
    return AccountBudgetError(f"invalid-state: {reason}")


class AccountBudgetAdmission:
    """Reserve and reconcile in-flight tokens for one shared hosted account group."""

    def __init__(self, policy: AccountBudgetPolicy, directory: Path | str | None = None) -> None:
        _validate_policy(policy)
        self.policy = policy
        self.directory = Path(
            os.path.abspath(
                directory
                or os.environ.get("PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR")
                or default_account_budget_dir()
            )
        )
        identity = hashlib.sha256(policy.account_group.encode()).hexdigest()
        self._state_path = self.directory / f"{identity}.json"
        self._lock_path = self.directory / f"{identity}.lock"

    # -- persisted state ----------------------------------------------------------------

    def _check_state_file(self) -> None:
        try:
            info = self._state_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise PermissionError("account budget state must be a private user-owned regular file")

    def _requested_policy(self) -> dict[str, Any]:
        return {
            "maxConcurrent": self.policy.max_concurrent,
            "inFlightTokenBudget": self.policy.in_flight_token_budget,
            "unknownUsage": self.policy.unknown_usage,
        }

    def _load(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "schemaVersion": 1,
            "accountGroup": self.policy.account_group,
            "policy": self._requested_policy(),
            "reservations": {},
        }
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except OSError, ValueError:
            raise _invalid("account budget state could not be read") from None
        if (
            not isinstance(state, dict)
            or state.get("schemaVersion") != 1
            or state.get("accountGroup") != self.policy.account_group
            or not isinstance(state.get("reservations"), dict)
        ):
            raise _invalid("account budget state is invalid")
        requested = self._requested_policy()
        state.setdefault("policy", requested)
        stored = state["policy"]
        if not isinstance(stored, dict):
            raise _invalid("account budget policy is invalid")
        if (
            not is_safe_integer(stored.get("maxConcurrent"))
            or stored["maxConcurrent"] < 1
            or not is_safe_integer(stored.get("inFlightTokenBudget"))
            or stored["inFlightTokenBudget"] < 1
            or stored.get("unknownUsage") not in ("hold", "release")
        ):
            raise _invalid("account budget policy is invalid")
        if stored != requested:
            raise AccountBudgetError(
                "policy-conflict: account budget policy conflicts with existing "
                "account-group policy"
            )
        now = int(time.time() * 1000)
        for identity, reservation in state["reservations"].items():
            if (
                not identity
                or not isinstance(reservation, dict)
                or not is_safe_integer(reservation.get("reservedTokens"))
                or reservation["reservedTokens"] <= 0
                or not is_safe_integer(reservation.get("ownerPid"))
                or reservation["ownerPid"] < 0
                or reservation.get("status") not in ("active", "unknown")
                or (reservation["status"] == "active" and reservation["ownerPid"] == 0)
            ):
                raise _invalid("account budget reservation is invalid")
            if reservation["status"] == "active" and not _pid_alive(reservation["ownerPid"]):
                reservation.update(status="unknown", ownerPid=0, updatedAt=now)
        return state

    def _save(self, state: dict[str, Any]) -> None:
        temporary = self.directory / f".{self._state_path.name}.{uuid.uuid4()}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, indent=2) + "\n")
        os.replace(temporary, self._state_path)

    def _locked(self, cancel: threading.Event | None) -> int:
        require_linux("account budget admission")
        prepare_private_directory(self.directory, "account budget")
        descriptor = open_private_lock(self._lock_path, "account budget")
        try:
            self._check_state_file()
            lock_exclusive(descriptor, cancel)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    # -- operations ---------------------------------------------------------------------

    def reserve(self, tokens: int, cancel: threading.Event | None = None) -> AccountReservation:
        """Reserve ``tokens`` of in-flight budget or raise ``AccountBudgetError``.

        Setting ``cancel`` while waiting for the state lock abandons the request without
        allocating anything.
        """
        if (
            not is_safe_integer(tokens)
            or tokens <= 0
            or tokens > self.policy.in_flight_token_budget
        ):
            raise AccountBudgetError("account reservation exceeds explicit budget policy")
        descriptor = self._locked(cancel)
        try:
            state = self._load()
            reservations = state["reservations"]
            reserved = sum(item["reservedTokens"] for item in reservations.values())
            if not is_safe_integer(reserved):
                raise _invalid("account budget reservation total is invalid")
            if len(reservations) >= self.policy.max_concurrent:
                raise AccountBudgetError("concurrency-limit: account concurrency limit reached")
            if reserved + tokens > self.policy.in_flight_token_budget:
                raise AccountBudgetError("budget-exhausted: account in-flight budget exhausted")
            reservation_id = f"reservation-{uuid.uuid4()}"
            reservations[reservation_id] = {
                "ownerPid": os.getpid(),
                "reservedTokens": tokens,
                "status": "active",
                "createdAt": int(time.time() * 1000),
            }
            self._save(state)
            return AccountReservation(reservation_id, tokens)
        finally:
            os.close(descriptor)

    def reconcile(
        self, reservation: AccountReservation, usage: Mapping[str, Any] | None
    ) -> Reconciliation:
        """Settle a reservation against reported usage, holding it when usage is unknown."""
        reported = usage_tokens(usage)
        descriptor = self._locked(None)
        try:
            state = self._load()
            item = state["reservations"].get(reservation.reservation_id)
            if item is None:
                raise AccountBudgetError("reservation-not-found: reservation not found")
            if reported is None and self.policy.unknown_usage == "hold":
                item["status"] = "unknown"
                item["updatedAt"] = int(time.time() * 1000)
                self._save(state)
                return Reconciliation(True, int(item["reservedTokens"]), None, None)
            reserved = int(item["reservedTokens"])
            del state["reservations"][reservation.reservation_id]
            self._save(state)
            overrun = None if reported is None else max(0, reported - reserved)
            return Reconciliation(False, reserved, reported, overrun)
        finally:
            os.close(descriptor)

    def release(self, reservation: AccountReservation) -> None:
        """Explicitly drop a reservation, including one held for unknown usage."""
        descriptor = self._locked(None)
        try:
            state = self._load()
            state["reservations"].pop(reservation.reservation_id, None)
            self._save(state)
        finally:
            os.close(descriptor)


def account_budget_policy_from_profile(profile: Mapping[str, Any]) -> AccountBudgetPolicy | None:
    """Build the budget policy a hosted profile declares, or ``None`` when it declares none."""
    values = [
        profile.get(name)
        for name in (
            "accountGroup",
            "accountMaxConcurrent",
            "accountInFlightTokenBudget",
            "accountUnknownUsage",
        )
    ]
    if all(value is None for value in values):
        return None
    group, max_concurrent, in_flight, unknown = values
    if (
        not isinstance(group, str)
        or not isinstance(max_concurrent, int)
        or not isinstance(in_flight, int)
        or not unknown
    ):
        raise ValueError(
            "hosted account budget policy must specify accountGroup, maxConcurrent, "
            "inFlightTokenBudget, and unknownUsage together"
        )
    return AccountBudgetPolicy(group, max_concurrent, in_flight, unknown)
