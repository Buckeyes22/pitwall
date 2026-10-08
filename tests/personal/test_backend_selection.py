"""Personal mode picks its backend from `[personal] backend` in pitwall.toml, nowhere else."""

from __future__ import annotations

from pathlib import Path

import pytest

from pitwall.config import ConfigFileError, PitwallSettings
from pitwall.personal.backend import backend_status_line, select_backend


def _config(tmp_path: Path, body: str) -> dict[str, str]:
    path = tmp_path / "pitwall.toml"
    path.write_text(body, encoding="utf-8")
    return {"PITWALL_CONFIG_FILE": str(path)}


def test_database_url_alone_does_not_switch_backend(tmp_path: Path) -> None:
    assert select_backend({"DATABASE_URL": "postgresql://pitwall@db/pitwall"}) == "personal"
    environ = _config(tmp_path, "")
    assert select_backend({**environ, "DATABASE_URL": "postgresql://x/y"}) == "personal"


def test_default_is_personal_without_a_config_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.toml"
    assert select_backend({"PITWALL_CONFIG_FILE": str(missing)}) == "personal"
    assert select_backend({}) == "personal"


@pytest.mark.parametrize("backend", ["personal", "registry"])
def test_the_personal_table_chooses_the_backend(tmp_path: Path, backend: str) -> None:
    environ = _config(tmp_path, f'[personal]\nbackend = "{backend}"\n')
    assert select_backend(environ) == backend


def test_an_unknown_backend_is_refused_by_setting_name_not_value(tmp_path: Path) -> None:
    environ = _config(tmp_path, '[personal]\nbackend = "sk-hunter2pass-secret"\n')
    with pytest.raises(ConfigFileError) as caught:
        select_backend(environ)
    message = str(caught.value)
    assert "[personal] backend" in message
    assert '"personal" or "registry"' in message
    assert "hunter2pass" not in message


def test_a_non_string_backend_is_refused_without_echoing_it(tmp_path: Path) -> None:
    environ = _config(tmp_path, "[personal]\nbackend = 31337\n")
    with pytest.raises(ConfigFileError) as caught:
        select_backend(environ)
    assert "[personal] backend" in str(caught.value)
    assert "31337" not in str(caught.value)


def test_settings_still_load_with_the_personal_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ = _config(tmp_path, '[personal]\nbackend = "registry"\n')
    monkeypatch.setenv("PITWALL_CONFIG_FILE", environ["PITWALL_CONFIG_FILE"])
    PitwallSettings()  # the table is accepted; the backend is read by pitwall.personal.backend


def test_status_line_names_the_backend_and_where_it_is_set() -> None:
    assert backend_status_line("personal").startswith("Backend: personal")
    line = backend_status_line("registry")
    assert line.startswith("Backend: registry")
    assert "pitwall.toml" in line
