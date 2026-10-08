"""Operator-console sources for J38 in three states: loaded, empty, and failing.

Every view gets a real static source (reusing the snapshot builders the TUI suite already
maintains), wrapped in ``Counting`` so a journey can prove a key binding reloaded it. The
failing state raises one recognizable error from the view's load method; every other view
stays loaded, so a failure in one view never leaks into another.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from pitwall.cost.read_models import RecentWorkloadsRead
from pitwall.personal.service import ServeRefused
from pitwall.tui.cost import StaticCostSource
from pitwall.tui.leases import StaticLeasesSource
from pitwall.tui.models import ModelsSnapshot, StaticModelCatalogueSource
from pitwall.tui.operations import StaticOperationsSource
from pitwall.tui.overview import StaticOverviewSource
from pitwall.tui.personal import StaticPersonalSource
from pitwall.tui.providers import ProvidersSnapshot, StaticProvidersSource
from pitwall.tui.resources import StaticResourcesSource
from tests.tui.test_cost_screen import _cost_snapshot
from tests.tui.test_leases_screen import _row, _snapshot
from tests.tui.test_operations_screen import _operations_snapshot
from tests.tui.test_overview import _snapshot as _overview_snapshot
from tests.tui.test_personal_screens import _lease, _models, _preview
from tests.tui.test_providers_screen import _providers_snapshot
from tests.tui.test_resources_screen import _resources_snapshot

DOWN_MESSAGE = "j38 source down"
STATES = ("loaded", "empty", "failing")


class Counting:
    """Delegate to a source and count every method call by name."""

    def __init__(self, inner: object, failing: tuple[str, ...] = ()) -> None:
        self._inner = inner
        self._failing = failing
        self.calls: Counter[str] = Counter()

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute
        fails = name in self._failing
        if inspect.iscoroutinefunction(attribute):

            async def call_async(*args: Any, **kwargs: Any) -> Any:
                self.calls[name] += 1
                if fails:
                    raise RuntimeError(DOWN_MESSAGE)
                return await attribute(*args, **kwargs)

            return call_async

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            if fails:
                raise RuntimeError(DOWN_MESSAGE)
            return attribute(*args, **kwargs)

        return call


def _loaded() -> dict[str, Callable[[], object]]:
    return {
        "overview_source": lambda: StaticOverviewSource(_overview_snapshot()),
        "providers_source": lambda: StaticProvidersSource(_providers_snapshot()),
        "leases_source": lambda: StaticLeasesSource(_snapshot(_row())),
        "models_source": lambda: StaticModelCatalogueSource(_models()),
        "personal_source": lambda: StaticPersonalSource(leases=[_lease()], preview=_preview()),
        "cost_source": lambda: StaticCostSource(_cost_snapshot()),
        "resources_source": lambda: StaticResourcesSource(_resources_snapshot()),
        "operations_source": lambda: StaticOperationsSource(_operations_snapshot()),
    }


def _empty(view: str) -> tuple[str, object]:
    if view == "overview":
        snapshot = dataclasses.replace(
            _overview_snapshot(),
            provider_total=0,
            provider_enabled=0,
            provider_health_counts={},
            lease_state_counts={},
            active_leases=0,
            total_cost_usd=Decimal("0"),
            cost_entry_count=0,
            recent_workload_count=0,
        )
        return "overview_source", StaticOverviewSource(snapshot)
    if view == "providers":
        return "providers_source", StaticProvidersSource(ProvidersSnapshot(entries=()))
    if view == "leases":
        return "leases_source", StaticLeasesSource(dataclasses.replace(_snapshot(_row()), rows=()))
    if view == "models":
        return "models_source", StaticModelCatalogueSource(ModelsSnapshot(rows=()))
    if view in {"pods", "routes"}:
        return "personal_source", StaticPersonalSource(leases=[])
    if view == "serve":  # a form has no empty list: its empty state is a refused preview
        refusal = ServeRefused("price_over_cap", "0.22 > 0.10")
        return "personal_source", StaticPersonalSource(leases=[], preview_error=refusal)
    if view == "cost":
        snapshot = dataclasses.replace(
            _cost_snapshot(),
            free_burn_down=(),
            recent_workloads=RecentWorkloadsRead(workloads=[]),
        )
        return "cost_source", StaticCostSource(snapshot)
    if view == "resources":
        snapshot = dataclasses.replace(
            _resources_snapshot(),
            endpoints=(),
            templates=(),
            volumes=(),
            registry_auths=(),
            pods=(),
            hub_templates=(),
        )
        return "resources_source", StaticResourcesSource(snapshot)
    if view == "operations":
        snapshot = dataclasses.replace(
            _operations_snapshot(), catalog=(), recent_jobs=(), resilience=()
        )
        return "operations_source", StaticOperationsSource(snapshot)
    raise AssertionError(view)


_LOAD_METHOD = {
    "overview": ("overview_source", ("load_overview",)),
    "providers": ("providers_source", ("load_providers",)),
    "leases": ("leases_source", ("load_leases",)),
    "models": ("models_source", ("load_models", "refresh")),
    "serve": ("personal_source", ("preview",)),
    "pods": ("personal_source", ("status",)),
    "routes": ("personal_source", ("status",)),
    "cost": ("cost_source", ("load_cost",)),
    "resources": ("resources_source", ("load_resources",)),
    "operations": ("operations_source", ("load_operations",)),
}


def console_sources(view: str, state: str) -> dict[str, Counting]:
    """PitwallApp keyword sources with *view*'s source in *state* and every other loaded."""
    sources = {name: Counting(build()) for name, build in _loaded().items()}
    name, methods = _LOAD_METHOD[view]
    if state == "empty":
        sources[name] = Counting(_empty(view)[1])
    elif state == "failing":
        sources[name] = Counting(_loaded()[name](), failing=methods)
    return sources


__all__ = ["DOWN_MESSAGE", "STATES", "Counting", "console_sources"]
