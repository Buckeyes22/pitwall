"""``pitwall doctor`` is one report made of registered sections."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pitwall import cli, doctor
from pitwall.doctor import (
    DoctorCheck,
    DoctorReport,
    DoctorSection,
    register_doctor_section,
    registered_sections,
    run_registered_sections,
)


@pytest.fixture
def isolated_sections(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A private copy of the section registry, so a test can register without leaking."""
    sections = dict(doctor._SECTIONS)
    monkeypatch.setattr(doctor, "_SECTIONS", sections)
    return sections


def _broker_report() -> DoctorReport:
    return DoctorReport(
        "registry", "0.0.0", (DoctorCheck("api.health", "services", "ok", "healthy"),)
    )


def test_unified_report_has_agents_section(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Any
) -> None:
    async def fake_broker(**_kwargs: Any) -> DoctorReport:
        return _broker_report()

    monkeypatch.setattr("pitwall.cli.doctor.run_doctor", fake_broker)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    assert cli.main(["doctor", "--json"]) in (0, 1)
    report = json.loads(capsys.readouterr().out)

    names = [section["name"] for section in report["sections"]]
    assert names[0] == "broker"
    assert "agents" in names
    agents = next(section for section in report["sections"] if section["name"] == "agents")
    assert agents["checks"], "the agents section reports checks"
    assert all(check["id"].startswith("agents.") for check in agents["checks"])
    assert report["checks"][0]["id"] == "api.health"


def test_a_registered_section_joins_the_report(
    isolated_sections: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    register_doctor_section(
        "extras",
        lambda: DoctorSection("extras", (DoctorCheck("extras.status", "extras", "warn", "cold"),)),
    )

    async def fake_broker(**_kwargs: Any) -> DoctorReport:
        return _broker_report()

    monkeypatch.setattr("pitwall.cli.doctor.run_doctor", fake_broker)
    monkeypatch.setattr(doctor, "agents_section", lambda: DoctorSection("agents", ()))
    isolated_sections["agents"] = doctor.agents_section

    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "== extras: warn" in out
    assert "extras.status: cold" in out
    assert "doctor: warn" in out


def test_a_failing_section_fails_the_report_and_names_the_section(
    isolated_sections: dict[str, Any],
) -> None:
    def broken() -> DoctorSection:
        raise RuntimeError("boom password=SYNTHETIC_TEST_MARKER")

    isolated_sections.clear()
    register_doctor_section("workbench", broken)

    (section,) = run_registered_sections()
    assert section.status == "fail"
    assert section.checks[0].id == "workbench.section"
    # The detail names the section and the exception class, never the exception text.
    assert "workbench" in section.checks[0].detail
    assert "RuntimeError" in section.checks[0].detail
    assert "boom" not in section.checks[0].detail
    assert "SYNTHETIC_TEST_MARKER" not in json.dumps(section.to_dict())
    report = DoctorReport("registry", "0.0.0", (), (section,))
    assert report.status == "fail"
    assert report.exit_code() == 1


def test_registering_a_name_twice_is_refused(isolated_sections: dict[str, Any]) -> None:
    register_doctor_section("extras", lambda: DoctorSection("extras", ()))
    with pytest.raises(ValueError, match="already registered"):
        register_doctor_section("extras", lambda: DoctorSection("extras", ()))


def test_agents_is_registered_by_default() -> None:
    assert "agents" in registered_sections()


def test_gateway_section_present() -> None:
    assert "gateway" in registered_sections()
    section = next(s for s in run_registered_sections() if s.name == "gateway")
    assert {check.id for check in section.checks} >= {
        "gateway.catalog_lock",
        "gateway.gateway_health",
        "gateway.front_door_loopback",
    }


def test_workbench_section_present() -> None:
    assert "workbench" in registered_sections()
    section = next(s for s in run_registered_sections() if s.name == "workbench")
    assert {check.id for check in section.checks} >= {
        "workbench.pi",
        "workbench.pi_subagents",
        "workbench.node",
        "workbench.flock",
        "workbench.restricted",
    }
    assert all(check.status != "fail" for check in section.checks), "optional tooling never fails"
