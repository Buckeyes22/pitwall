"""Account budget tests, translated from packages/pi-workbench/tests/account-budget.test.ts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pitwall.workbench.account_budget import (
    AccountBudgetAdmission,
    AccountBudgetError,
    AccountBudgetPolicy,
    AccountReservation,
    account_budget_policy_from_profile,
    usage_tokens,
)
from pitwall.workbench.admission import RequestCancelled
from tests.hang_guard import HANG_GUARD_SECS, reap


def policy(group: str, *, concurrent: int = 1, budget: int = 100) -> AccountBudgetPolicy:
    return AccountBudgetPolicy(group, concurrent, budget, "hold")


def state_path(directory: Path, group: str) -> Path:
    return directory / f"{hashlib.sha256(group.encode()).hexdigest()}.json"


@pytest.mark.parity
def test_account_budget_reserves_explicit_in_flight_tokens_and_reconciles_reported_usage(
    tmp_path: Path,
) -> None:
    """Source: account-budget.test.ts 'account budget reserves explicit in-flight tokens and reconciles reported usage'."""
    budget = AccountBudgetAdmission(policy("shared-account", concurrent=2), tmp_path)
    first = budget.reserve(60)
    with pytest.raises(AccountBudgetError, match="budget exhausted"):
        budget.reserve(50)
    settled = budget.reconcile(first, {"input": 10, "output": 20})
    assert (settled.held, settled.reserved_tokens) == (False, 60)
    assert (settled.reported_tokens, settled.overrun_tokens) == (30, 0)
    second = budget.reserve(50)
    budget.release(second)


@pytest.mark.parity
def test_unknown_usage_holds_the_conservative_reservation_until_explicit_release(
    tmp_path: Path,
) -> None:
    """Source: account-budget.test.ts 'unknown usage holds the conservative reservation until explicit release'."""
    budget = AccountBudgetAdmission(policy("unknown-account"), tmp_path)
    reservation = budget.reserve(80)
    held = budget.reconcile(reservation, {"input": 10})
    assert (held.held, held.reserved_tokens, held.reported_tokens) == (True, 80, None)
    with pytest.raises(AccountBudgetError, match="concurrency limit"):
        budget.reserve(21)
    budget.release(reservation)
    assert budget.reserve(21) is not None


@pytest.mark.parity
def test_corrupt_persisted_reservations_fail_closed(tmp_path: Path) -> None:
    """Source: account-budget.test.ts 'corrupt persisted reservations fail closed'."""
    group = "corrupt-group"
    state_path(tmp_path, group).write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "accountGroup": group,
                "policy": {"maxConcurrent": 2, "inFlightTokenBudget": 100, "unknownUsage": "hold"},
                "reservations": {
                    "corrupt": {
                        "ownerPid": os.getpid(),
                        "reservedTokens": "NaN",
                        "status": "active",
                    }
                },
            }
        )
    )
    state_path(tmp_path, group).chmod(0o600)
    budget = AccountBudgetAdmission(policy(group, concurrent=2), tmp_path)
    with pytest.raises(AccountBudgetError, match="invalid-state"):
        budget.reserve(1)


@pytest.mark.parity
def test_usage_components_that_overflow_safe_integer_accounting_become_unknown(
    tmp_path: Path,
) -> None:
    """Source: account-budget.test.ts 'usage components that overflow safe integer accounting become unknown'."""
    budget = AccountBudgetAdmission(policy("overflow-group"), tmp_path)
    reservation = budget.reserve(80)
    result = budget.reconcile(reservation, {"input": 2**53 - 1, "output": 1})
    assert (result.held, result.reported_tokens) == (True, None)
    budget.release(reservation)


RESERVE_AND_WAIT = """
import sys
from pitwall.workbench.account_budget import AccountBudgetAdmission, AccountBudgetPolicy
budget = AccountBudgetAdmission(AccountBudgetPolicy("alias-group", 1, 100, "hold"), sys.argv[1])
reservation = budget.reserve(70)
print(reservation.reservation_id, flush=True)
sys.stdin.readline()
budget.release(reservation)
"""

RESERVE_AND_EXIT = """
import sys
from pitwall.workbench.account_budget import AccountBudgetAdmission, AccountBudgetPolicy
AccountBudgetAdmission(AccountBudgetPolicy("crash-group", 1, 100, "hold"), sys.argv[1]).reserve(90)
"""


@pytest.mark.parity
def test_account_aliases_in_separate_processes_share_one_reservation_identity(
    tmp_path: Path,
) -> None:
    """Source: account-budget.test.ts 'account aliases in separate processes share one reservation identity'."""
    child = subprocess.Popen(
        [sys.executable, "-c", RESERVE_AND_WAIT, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().startswith("reservation-")
        other = AccountBudgetAdmission(policy("alias-group"), tmp_path)
        with pytest.raises(AccountBudgetError, match="concurrency limit"):
            other.reserve(20)
        assert child.stdin is not None
        child.stdin.write("release\n")
        child.stdin.close()
        child.wait(timeout=HANG_GUARD_SECS)
        assert other.reserve(20) is not None
    finally:
        reap(child)


@pytest.mark.parity
def test_dead_owner_processes_become_unknown_liability_rather_than_silently_freeing_budget(
    tmp_path: Path,
) -> None:
    """Source: account-budget.test.ts 'dead owner processes become unknown liability rather than silently freeing budget'."""
    subprocess.run(
        [sys.executable, "-c", RESERVE_AND_EXIT, str(tmp_path)], check=True, timeout=HANG_GUARD_SECS
    )
    budget = AccountBudgetAdmission(policy("crash-group"), tmp_path)
    with pytest.raises(AccountBudgetError, match="concurrency limit"):
        budget.reserve(90)
    state = json.loads(state_path(tmp_path, "crash-group").read_text())
    (identity,) = state["reservations"]
    budget.release(AccountReservation(identity, 90))
    assert budget.reserve(90) is not None


@pytest.mark.parity
def test_aliases_cannot_replace_an_existing_account_group_policy_with_a_looser_one(
    tmp_path: Path,
) -> None:
    """Source: account-budget.test.ts 'aliases cannot replace an existing account-group policy with a looser one'."""
    first = AccountBudgetAdmission(policy("policy-group"), tmp_path)
    reservation = first.reserve(50)
    alias = AccountBudgetAdmission(policy("policy-group", concurrent=2, budget=200), tmp_path)
    with pytest.raises(AccountBudgetError, match="policy conflicts"):
        alias.reserve(50)
    first.release(reservation)


@pytest.mark.parity
def test_abort_while_waiting_for_the_account_state_lock_cancels_and_does_not_allocate_later(
    tmp_path: Path,
) -> None:
    """Source: account-budget.test.ts 'abort while waiting for the account state lock cancels and does not allocate later'."""
    group = "abort-group"
    lock_path = tmp_path / f"{hashlib.sha256(group.encode()).hexdigest()}.lock"
    lock_path.write_text("")
    lock_path.chmod(0o600)
    holder = subprocess.Popen(
        ["flock", "--exclusive", "--no-fork", str(lock_path), "sleep", "30"],
    )
    try:
        time.sleep(0.2)
        budget = AccountBudgetAdmission(policy(group), tmp_path)
        cancel = threading.Event()
        outcome: dict[str, BaseException] = {}

        def reserve() -> None:
            try:
                budget.reserve(10, cancel)
            except BaseException as error:  # reason: the test records whatever the reserve raised
                outcome["error"] = error

        thread = threading.Thread(target=reserve, daemon=True)
        thread.start()
        time.sleep(0.2)
        cancel.set()
        thread.join(5)
        assert isinstance(outcome.get("error"), RequestCancelled)
    finally:
        holder.terminate()
        holder.wait()
    assert budget.reserve(10) is not None


def test_reservations_are_bounded_by_the_explicit_policy(tmp_path: Path) -> None:
    budget = AccountBudgetAdmission(policy("bounds-group"), tmp_path)
    for tokens in (0, -1, 101):
        with pytest.raises(AccountBudgetError, match="exceeds explicit budget policy"):
            budget.reserve(tokens)
    assert state_path(tmp_path, "bounds-group").exists() is False


def test_unknown_usage_release_frees_the_reservation_when_usage_is_missing(tmp_path: Path) -> None:
    budget = AccountBudgetAdmission(
        AccountBudgetPolicy("release-group", 1, 100, "release"), tmp_path
    )
    reservation = budget.reserve(40)
    result = budget.reconcile(reservation, None)
    assert (result.held, result.reported_tokens, result.overrun_tokens) == (False, None, None)
    assert budget.reserve(40) is not None


def test_reported_usage_above_the_reservation_is_an_overrun(tmp_path: Path) -> None:
    budget = AccountBudgetAdmission(policy("overrun-group"), tmp_path)
    result = budget.reconcile(budget.reserve(10), {"totalTokens": 25})
    assert (result.reported_tokens, result.overrun_tokens) == (25, 15)


def test_state_and_directory_are_private(tmp_path: Path) -> None:
    directory = tmp_path / "budgets"
    budget = AccountBudgetAdmission(policy("private-group"), directory)
    budget.reserve(10)
    assert directory.stat().st_mode & 0o077 == 0
    assert state_path(directory, "private-group").stat().st_mode & 0o777 == 0o600
    state_path(directory, "private-group").chmod(0o644)
    with pytest.raises(PermissionError, match="private user-owned regular file"):
        budget.reserve(10)


def test_a_missing_reservation_is_reported(tmp_path: Path) -> None:
    budget = AccountBudgetAdmission(policy("missing-group"), tmp_path)
    with pytest.raises(AccountBudgetError, match="reservation-not-found"):
        budget.reconcile(AccountReservation("reservation-x", 1), {"totalTokens": 1})


def test_policy_validation_and_profile_extraction() -> None:
    with pytest.raises(ValueError, match="valid accountGroup"):
        AccountBudgetAdmission(policy("bad group!"))
    with pytest.raises(ValueError, match="maxConcurrent"):
        AccountBudgetAdmission(policy("g", concurrent=0))
    assert account_budget_policy_from_profile({}) is None
    extracted = account_budget_policy_from_profile(
        {
            "accountGroup": "g",
            "accountMaxConcurrent": 2,
            "accountInFlightTokenBudget": 50,
            "accountUnknownUsage": "release",
        }
    )
    assert extracted == AccountBudgetPolicy("g", 2, 50, "release")
    with pytest.raises(ValueError, match="together"):
        account_budget_policy_from_profile({"accountGroup": "g"})


def test_usage_tokens_prefers_the_total_and_requires_exact_components() -> None:
    assert usage_tokens({"totalTokens": 9, "input": 1, "output": 1}) == 9
    assert usage_tokens({"input": 3, "output": 4}) == 7
    assert usage_tokens({"input": 3}) is None
    assert usage_tokens({"input": -1, "output": 1}) is None
    assert usage_tokens(None) is None
