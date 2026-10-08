"""The pinned OmniRoute tarball is fetched over HTTPS, verified, and read in memory."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest

from pitwall.gateway_catalog import extract, sync

FIXTURE_PACKAGE = Path(__file__).resolve().parents[1] / "fixtures" / "omniroute" / "package"
TARBALL_URL = "https://registry.npmjs.org/omniroute/-/omniroute-9.9.9.tgz"


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _fixture_tarball() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        files = extract.read_package_dir(FIXTURE_PACKAGE)
        files["README.md"] = "ignored\n"
        for rel, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"package/{rel}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")


def _fake_urlopen(tarball: bytes, integrity: str, requested: list[str]) -> Any:
    def urlopen(url: str, *_args: Any, **_kwargs: Any) -> _Response:
        requested.append(url)
        if url.endswith("/omniroute/9.9.9"):
            meta = {"dist": {"tarball": TARBALL_URL, "integrity": integrity}}
            return _Response(json.dumps(meta).encode("utf-8"))
        assert url == TARBALL_URL
        return _Response(tarball)

    return urlopen


def test_tarball_integrity_mismatch_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    tarball = _fixture_tarball()
    wrong = _integrity(tarball + b"tampered")
    monkeypatch.setattr(sync, "urlopen", _fake_urlopen(tarball, wrong, []))
    with pytest.raises(sync.IntegrityError, match="integrity"):
        sync.extract_from_npm("9.9.9")


def test_tarball_is_verified_and_extracted_in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    tarball = _fixture_tarball()
    requested: list[str] = []
    monkeypatch.setattr(sync, "urlopen", _fake_urlopen(tarball, _integrity(tarball), requested))
    payload, sha256 = sync.extract_from_npm("9.9.9")
    assert requested == ["https://registry.npmjs.org/omniroute/9.9.9", TARBALL_URL]
    assert sha256 == hashlib.sha256(tarball).hexdigest()
    assert payload == extract.extract_catalog(extract.read_package_dir(FIXTURE_PACKAGE))


def test_non_https_tarball_url_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(url: str, *_a: Any, **_k: Any) -> _Response:
        meta = {"dist": {"tarball": "http://registry.npmjs.org/x.tgz", "integrity": "sha512-AA=="}}
        return _Response(json.dumps(meta).encode("utf-8"))

    monkeypatch.setattr(sync, "urlopen", urlopen)
    with pytest.raises(sync.IntegrityError, match="https"):
        sync.extract_from_npm("9.9.9")


def test_no_npm_or_node_invoked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[object] = []

    def record(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        raise AssertionError("subprocess must not be used by the catalog sync")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, record)
    tarball = _fixture_tarball()
    monkeypatch.setattr(sync, "urlopen", _fake_urlopen(tarball, _integrity(tarball), []))
    (tmp_path / "seed").mkdir()
    (tmp_path / "config").mkdir()
    assert sync.main(["--version", "9.9.9", "--repo-root", str(tmp_path)]) == 0
    assert calls == []
    lock = json.loads((tmp_path / "config" / "gateway-catalog.lock.json").read_text("utf-8"))
    assert lock["upstream_version"] == "9.9.9"
    assert lock["tarball_sha256"] == hashlib.sha256(tarball).hexdigest()
