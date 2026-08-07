"""Unit tests for the declarative rule engine (S1, S4) — src/validation/."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.validation.rules import Rules

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _rules(override: str | None = None) -> Rules:
    return Rules.load(str(_FIXTURES / override) if override else None)


def _codes(rules: Rules, record: dict) -> list[str]:
    return sorted(issue.code for issue in rules.evaluate(record))


def _charger(**overrides) -> dict:
    record = {
        "record_type": "charger",
        "lat": 35.7913, "lon": -78.7812,
        "connector_type": "J1772", "level": "L2", "status": "ACTIVE",
        "install_date": "2019-04-15",
        "provenance": {"source": "afdc", "source_file": "f", "ingested_at": "2026-07-28T09:00:00+00:00"},
    }
    record.update(overrides)
    return record


def _session(**overrides) -> dict:
    record = {
        "record_type": "session",
        "start_time": "2023-01-03T10:00:00+00:00",
        "end_time": "2023-01-03T11:00:00+00:00",
        "energy_kwh": 5.0, "peak_kw": 6.0, "duration_min": 60.0,
        "provenance": {"source": "cary", "source_file": "f", "ingested_at": "2023-01-04T09:00:00+00:00"},
    }
    record.update(overrides)
    return record


def _maintenance(**overrides) -> dict:
    record = {
        "record_type": "maintenance",
        "event_id": "EVT-1",
        "event_date": "2023-10-02",
        "event_type": "REPAIR", "severity": "MINOR",
        "description": "Fixed latch",
        "provenance": {"source": "contractor_reports", "source_file": "f", "ingested_at": "2023-10-03T09:00:00+00:00"},
    }
    record.update(overrides)
    return record


# -- territory --------------------------------------------------------------


def test_default_bbox_is_north_carolina():
    box = _rules().bbox()
    assert box == {"min_lat": 33.7, "max_lat": 36.6, "min_lon": -84.4, "max_lon": -75.4}


def test_bbox_override_swaps_territory_by_config_alone():
    rules = _rules(override="rules_override.yaml")
    assert rules.bbox()["min_lat"] == 40.4
    nyc = _charger(lat=40.7033, lon=-74.017)
    nc = _charger()
    assert "outside_territory" not in _codes(rules, nyc)
    assert "outside_territory" in _codes(rules, nc)
    # Default rules: the opposite.
    assert "outside_territory" in _codes(_rules(), nyc)
    assert "outside_territory" not in _codes(_rules(), nc)


# -- required fields --------------------------------------------------------


def test_missing_provenance_source():
    record = _charger()
    del record["provenance"]["source"]
    assert _codes(_rules(), record) == ["missing_provenance_source"]


def test_missing_maintenance_required_fields():
    record = _maintenance()
    del record["event_id"]
    del record["description"]
    assert _codes(_rules(), record) == ["missing_description", "missing_event_id"]


def test_nullable_fields_not_required():
    assert _codes(_rules(), _charger(charger_id=None, network=None)) == []
    assert _codes(_rules(), _session(session_id=None, fault_code=None)) == []


# -- ranges / impossible values ---------------------------------------------


def test_negative_energy_is_impossible():
    assert _codes(_rules(), _session(energy_kwh=-5.0)) == ["impossible_energy_kwh"]


def test_power_above_max_is_out_of_range():
    assert _codes(_rules(), _charger(power_kw=500.0)) == ["power_kw_out_of_range"]


def test_negative_power_is_impossible():
    assert _codes(_rules(), _charger(power_kw=-1.0)) == ["impossible_power_kw"]


def test_negative_duration_is_impossible():
    # No end_time so the duration_matches check cannot fire alongside.
    assert _codes(_rules(), _session(duration_min=-10.0, end_time=None)) == ["impossible_duration"]


def test_zero_energy_is_valid():
    assert _codes(_rules(), _session(energy_kwh=0.0)) == []


def test_nan_and_inf_are_invalid_numerics():
    assert _codes(_rules(), _session(energy_kwh=float("nan"))) == ["invalid_energy_kwh"]
    assert _codes(_rules(), _session(energy_kwh=float("inf"))) == ["invalid_energy_kwh"]


def test_unparseable_numeric_is_invalid():
    assert _codes(_rules(), _session(energy_kwh="lots")) == ["invalid_energy_kwh"]


# -- cross-field (same-record only) -----------------------------------------


def test_end_before_start():
    record = _session(start_time="2023-07-01T12:00:00+00:00", end_time="2023-07-01T11:00:00+00:00")
    assert _codes(_rules(), record) == ["end_before_start"]


def test_duration_mismatch():
    record = _session(start_time="2023-08-01T10:00:00+00:00", end_time="2023-08-01T11:00:00+00:00",
                      duration_min=30.0)
    assert _codes(_rules(), record) == ["duration_mismatch"]


def test_duration_within_tolerance_ok():
    record = _session(duration_min=60.1)  # tolerance 5 min
    assert _codes(_rules(), record) == []


def test_duration_mismatch_not_double_reported_when_end_before_start():
    record = _session(start_time="2023-07-01T12:00:00+00:00", end_time="2023-07-01T11:00:00+00:00")
    assert _codes(_rules(), record) == ["end_before_start"]


# -- staleness (S4) ---------------------------------------------------------


def test_session_stale_after_7_days():
    record = _session(provenance={"source": "cary", "source_file": "f",
                                  "ingested_at": "2023-01-15T09:00:00+00:00"})
    assert _codes(_rules(), record) == ["stale_report"]


def test_session_fresh_within_7_days():
    assert _codes(_rules(), _session()) == []


def test_maintenance_stale():
    record = _maintenance(event_date="2022-12-20",
                          provenance={"source": "s", "source_file": "f",
                                      "ingested_at": "2023-01-20T09:00:00+00:00"})
    assert _codes(_rules(), record) == ["stale_report"]


def test_charger_never_stale():
    # Charger records have no event/session/report date (S4 boundary rule).
    assert _codes(_rules(), _charger()) == []


def test_stale_uses_end_time_fallback_to_start_time():
    record = _session(end_time=None,
                      provenance={"source": "cary", "source_file": "f",
                                  "ingested_at": "2023-01-20T09:00:00+00:00"})
    assert _codes(_rules(), record) == ["stale_report"]


# -- formats ----------------------------------------------------------------


def test_unparseable_datetime_and_date():
    assert _codes(_rules(), _session(start_time="yesterday-ish")) == ["invalid_start_time"]
    assert _codes(_rules(), _charger(install_date="not-a-date")) == ["invalid_install_date"]
    assert _codes(_rules(), _maintenance(event_date="2023/10/02")) == ["invalid_event_date"]


# -- enums (backstop) -------------------------------------------------------


def test_enum_backstop():
    assert _codes(_rules(), _charger(level="LEVEL_TWO")) == ["invalid_level"]


# -- issue metadata ---------------------------------------------------------


def test_issue_severities_are_stable():
    rules = _rules()
    assert rules.get_rule("stale_report")["severity"] == "MINOR"
    assert rules.get_rule("impossible_energy_kwh")["severity"] == "MAJOR"
    assert rules.get_rule("outside_territory")["severity"] == "MAJOR"


def test_unknown_check_type_raises():
    rules = _rules()
    rules.rules.append({"id": "bogus", "check": "teleport", "severity": "MINOR"})
    with pytest.raises(KeyError):
        rules.evaluate(_session())
