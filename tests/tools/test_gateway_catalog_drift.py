from tools.gateway.check_catalog_drift import compare


def _snapshot(**overrides):
    base = {
        "steady_monthly": 5_000_000,
        "pool_count": 1,
        "avoid_list": ["delta"],
        "row_count": 5,
        "registry_covered": 2,
        "known_unreachable": ["agy"],
    }
    base.update(overrides)
    return base


def test_avoid_list_change_is_a_hard_failure() -> None:
    report = compare(_snapshot(avoid_list=["delta", "beta"]), _snapshot())
    assert report.hard_failures == ["avoid-list changed: +['beta'] -[]"]


def test_count_change_is_a_warning_only() -> None:
    report = compare(_snapshot(row_count=6, steady_monthly=5_500_000), _snapshot())
    assert report.hard_failures == []
    assert report.warnings == ["row_count 5 -> 6", "steady_monthly 5000000 -> 5500000"]


def test_known_unreachable_change_is_a_hard_failure() -> None:
    report = compare(
        _snapshot(known_unreachable=["agy", "arcee-ai"]),
        _snapshot(known_unreachable=["agy"]),
    )
    assert report.hard_failures == ["known_unreachable changed: +['arcee-ai'] -[]"]


def test_registry_covered_change_is_a_warning_only() -> None:
    report = compare(_snapshot(registry_covered=3), _snapshot())
    assert report.hard_failures == []
    assert report.warnings == ["registry_covered 2 -> 3"]
