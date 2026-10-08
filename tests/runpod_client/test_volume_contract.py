"""Volume delete must be idempotent, and update must not shrink.

Live: deleting a volume that is already gone answers HTTP 500 ("Tried to delete
nonexistent network volume"), not 404, so the 404-only guard never fired and a
cleanup retry raised. And a 20 GB volume resized to 5 GB was accepted and
applied — a re-read confirmed size 5 — although the method documents that size
must be larger than the current one.
"""

from __future__ import annotations

import pytest

from pitwall.runpod_client.mounts import NetworkVolumeClient
from pitwall.runpod_client.pods import RunPodRestError


class _StubClient(NetworkVolumeClient):
    def __init__(self, error: Exception | None = None, size: int = 20) -> None:
        super().__init__()  # the stub overrides _rest_request, so no credential is used
        self._error = error
        self._size = size
        self.requests: list[tuple[str, str]] = []

    async def _rest_request(  # type: ignore[override]  # reason: intentionally loose test stub
        self, method, path, *, json_body=None
    ):
        self.requests.append((method, path))
        if self._error is not None:
            raise self._error
        return {"id": "vol_1", "name": "n", "size": self._size, "dataCenter": "EU-RO-1"}


@pytest.mark.asyncio
async def test_delete_is_idempotent_when_the_api_answers_500() -> None:
    gone = RunPodRestError(
        "DELETE",
        "/network-volumes/vol_1",
        500,
        '{"error":"delete network volume: Tried to delete nonexistent network volume"}',
    )
    await _StubClient(error=gone).delete("vol_1")


@pytest.mark.asyncio
async def test_delete_still_raises_on_a_real_server_fault() -> None:
    fault = RunPodRestError("DELETE", "/network-volumes/vol_1", 500, '{"error":"boom"}')
    with pytest.raises(RunPodRestError):
        await _StubClient(error=fault).delete("vol_1")


@pytest.mark.asyncio
async def test_update_refuses_to_shrink() -> None:
    client = _StubClient(size=20)
    with pytest.raises(ValueError, match="smaller"):
        await client.update("vol_1", 5)
    assert client.requests[-1][0] == "GET", "the shrink must be refused before any write"
