"""Atomic, owner-only personal lease state."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from pitwall.personal.state import PersonalLease, StateStore


def _lease(route: str = "ornith", state: str = "launching") -> PersonalLease:
    now = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.UTC)
    return PersonalLease(
        route=route,
        pod_id="pod123",
        model="ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        served_model_id="Ornith-1.5-35B-A3B",
        engine="llama.cpp",
        variant="gguf:Q4_K_M",
        image="ghcr.io/ggml-org/llama.cpp:server-cuda",
        gpu_class="NVIDIA GeForce RTX 3090",
        gpu_count=1,
        cloud="community",
        price_per_hour_usd="0.220000",
        endpoint_url="https://pod123-8000.proxy.runpod.net/v1",
        key_env="PITWALL_ENDPOINT_KEY",
        launched_at=now,
        deadline_at=now + dt.timedelta(minutes=45),
        state=state,  # type: ignore[arg-type]  # reason: helper takes a plain str for the Literal field
    )


def test_default_root_honours_xdg_state_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert StateStore().root == tmp_path / "state" / "pitwall"


def test_upsert_is_atomic_and_owner_only(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "pitwall")
    store.upsert(_lease())

    path = store.root / "leases.json"
    assert oct(store.root.stat().st_mode & 0o777) == "0o700"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert sorted(p.name for p in store.root.iterdir()) == [".lock", "leases.json"]  # no temp file
    assert json.loads(path.read_text())[0]["route"] == "ornith"


def test_update_changes_only_named_fields(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.upsert(_lease())

    updated = store.update("ornith", state="ready")

    assert updated.state == "ready"
    assert updated.pod_id == "pod123"
    assert store.get("ornith") == updated
    with pytest.raises(KeyError):
        store.update("missing", state="gone")


def test_load_tolerates_missing_file(tmp_path: Path) -> None:
    assert StateStore(tmp_path).load() == []
