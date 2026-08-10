"""Unit tests for src.validation.standardizers (S2)."""

from __future__ import annotations

from src.validation.rules import Rules
from src.validation.standardizers import Standardizer


def _standardizer() -> Standardizer:
    return Standardizer(Rules.load().config)


def _standardize(record: dict) -> tuple[dict, list[str]]:
    return _standardizer().standardize_record(record)


# -- dates ------------------------------------------------------------------


def test_naive_american_datetime_gets_default_timezone():
    record, fixes = _standardize({
        "record_type": "session",
        "start_time": "3/4/24 2pm",
        "end_time": "3/4/24 3pm",
        "provenance": {"source": "cary", "source_file": "f", "ingested_at": "2024-03-05T09:00:00+00:00"},
    })
    assert record["start_time"] == "2024-03-04T14:00:00-05:00"  # America/New_York, EST
    assert record["end_time"] == "2024-03-04T15:00:00-05:00"
    assert any(f.startswith("date:start_time:") for f in fixes)


def test_slashed_and_dotted_formats():
    record, _ = _standardize({
        "record_type": "session",
        "start_time": "2023/01/03 17:58",
        "end_time": "2023-01-03T18:00:00+00:00",
        "provenance": {"source": "cary", "source_file": "f", "ingested_at": "2023-01-04T09:00:00+00:00"},
    })
    assert record["start_time"] == "2023-01-03T17:58:00-05:00"


def test_dotted_date_is_day_first():
    record, _ = _standardize({
        "record_type": "maintenance",
        "event_id": "E1",
        "event_date": "20.12.2022",
        "description": "x",
        "provenance": {"source": "s", "source_file": "f", "ingested_at": "2023-01-20T09:00:00+00:00"},
    })
    assert record["event_date"] == "2022-12-20"


def test_iso_datetime_passthrough_untouched():
    record, fixes = _standardize({
        "record_type": "session",
        "start_time": "2023-01-03T17:58:04+00:00",
        "end_time": "2023-01-03T18:52:54+00:00",
        "provenance": {"source": "cary", "source_file": "f", "ingested_at": "2023-01-04T09:00:00+00:00"},
    })
    assert record["start_time"] == "2023-01-03T17:58:04+00:00"
    assert not any(f.startswith("date:") for f in fixes)


def test_unparseable_date_left_unchanged_for_rules():
    record, fixes = _standardize({
        "record_type": "session",
        "start_time": "yesterday-ish",
        "end_time": "2023-09-01T11:00:00+00:00",
        "provenance": {"source": "cary", "source_file": "f", "ingested_at": "2023-09-02T09:00:00+00:00"},
    })
    assert record["start_time"] == "yesterday-ish"
    assert not any("start_time" in f for f in fixes)


# -- unit repair ------------------------------------------------------------


def test_watts_to_kilowatts():
    record, fixes = _standardize({
        "record_type": "charger",
        "power_kw": 8000,
        "provenance": {"source": "afdc", "source_file": "f", "ingested_at": "2026-07-28T09:00:00+00:00"},
    })
    assert record["power_kw"] == 8.0
    assert any(f == "unit_repair:power_kw:8000→8.0" for f in fixes)


def test_watthours_to_kilowatthours():
    record, _ = _standardize({
        "record_type": "session",
        "energy_kwh": 3500,
        "provenance": {"source": "cary", "source_file": "f", "ingested_at": "2023-05-02T09:00:00+00:00"},
    })
    assert record["energy_kwh"] == 3.5


def test_plausible_values_untouched():
    record, fixes = _standardize({
        "record_type": "charger",
        "power_kw": 7.2,
        "provenance": {"source": "afdc", "source_file": "f", "ingested_at": "2026-07-28T09:00:00+00:00"},
    })
    assert record["power_kw"] == 7.2
    assert not any(f.startswith("unit_repair") for f in fixes)


def test_absurd_magnitude_not_repaired():
    # Above max_plausible: leave for the range rules to flag.
    record, _ = _standardize({
        "record_type": "charger",
        "power_kw": 500000,
        "provenance": {"source": "afdc", "source_file": "f", "ingested_at": "2026-07-28T09:00:00+00:00"},
    })
    assert record["power_kw"] == 500000


def test_numeric_string_coerced():
    record, fixes = _standardize({
        "record_type": "charger",
        "power_kw": "7.2",
        "lat": "35.7784",
        "provenance": {"source": "afdc", "source_file": "f", "ingested_at": "2026-07-28T09:00:00+00:00"},
    })
    assert record["power_kw"] == 7.2
    assert record["lat"] == 35.7784
    assert any(f.startswith("coerce:power_kw:") for f in fixes)


# -- enums ------------------------------------------------------------------


def test_messy_connector_and_level():
    record, fixes = _standardize({
        "record_type": "charger",
        "connector_type": "chgpt LVL2 dual",
        "level": "lv2",
        "status": "under maintenance",
        "provenance": {"source": "afdc", "source_file": "f", "ingested_at": "2026-07-28T09:00:00+00:00"},
    })
    assert record["connector_type"] == "CHAdeMO"
    assert record["level"] == "L2"
    assert record["status"] == "MAINTENANCE"
    assert any(f.startswith("enum:connector_type:") for f in fixes)


def test_urgent_safety_beats_urgent():
    record, _ = _standardize({
        "record_type": "maintenance",
        "event_id": "E1",
        "severity": "urgent-safety",
        "description": "x",
        "provenance": {"source": "s", "source_file": "f", "ingested_at": "2023-01-20T09:00:00+00:00"},
    })
    assert record["severity"] == "SAFETY"


def test_unresolvable_enum_with_catchall_falls_back():
    record, fixes = _standardize({
        "record_type": "maintenance",
        "event_id": "E1",
        "event_type": "grinder noise",
        "severity": "fyi",
        "description": "x",
        "provenance": {"source": "s", "source_file": "f", "ingested_at": "2023-01-20T09:00:00+00:00"},
    })
    assert record["event_type"] == "OTHER"
    assert record["severity"] == "INFO"
    assert any(f.startswith("enum_fallback:event_type:") for f in fixes)


def test_unresolvable_severity_left_for_rules():
    # severity has no catch-all member; the value survives so the rule flags it.
    record, _ = _standardize({
        "record_type": "maintenance",
        "event_id": "E1",
        "severity": "catastrophic",
        "description": "x",
        "provenance": {"source": "s", "source_file": "f", "ingested_at": "2023-01-20T09:00:00+00:00"},
    })
    assert record["severity"] == "catastrophic"


def test_status_out_of_service_is_inactive_not_maintenance():
    record, _ = _standardize({
        "record_type": "charger",
        "status": "out of service",
        "provenance": {"source": "afdc", "source_file": "f", "ingested_at": "2026-07-28T09:00:00+00:00"},
    })
    assert record["status"] == "INACTIVE"


# -- address / zip ----------------------------------------------------------


def test_zip_and_state_cleanup():
    record, fixes = _standardize({
        "record_type": "charger",
        "address": {"street": "113 Walnut St", "city": "Cary", "state": "North Carolina", "zip": "27511-1234"},
        "provenance": {"source": "afdc", "source_file": "f", "ingested_at": "2026-07-28T09:00:00+00:00"},
    })
    assert record["address"]["zip"] == "27511"
    assert record["address"]["state"] == "NC"
    assert any(f.startswith("zip:") for f in fixes)
    assert any(f.startswith("case:address.state:") for f in fixes)


def test_whitespace_collapse():
    record, _ = _standardize({
        "record_type": "session",
        "station_id": "  TOWN OF CARY  /  TOWN HALL-PWH ",
        "provenance": {"source": "cary", "source_file": "f", "ingested_at": "2023-01-04T09:00:00+00:00"},
    })
    assert record["station_id"] == "TOWN OF CARY / TOWN HALL-PWH"
