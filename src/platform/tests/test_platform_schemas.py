import pytest
from pydantic import ValidationError

from src.platform import schemas

_PROV = {"source": "s", "source_file": "f", "ingested_at": "2026-08-01T00:00:00+00:00"}


def _session(**over):
    return {"record_type": "session", "provenance": _PROV, "quality": {}, **over}


def test_p1_raw_models_accept_verbatim_mess():
    raw = {"record_type": "charger", "connector_type": "chgpt LVL2 dual",
           "power_kw": "6600 W", "status": "up", "install_date": "3/4/20",
           "provenance": _PROV, "quality": {"score": 0.5}}
    schemas.parse_canonical_line(raw)  # §5.0: raw at P1, no enum/ISO enforcement


def test_p2_rejects_unnormalized_enum():
    with pytest.raises(ValidationError):
        schemas.parse_validated_line(
            {"record_type": "charger", "connector_type": "chgpt LVL2 dual",
             "provenance": _PROV, "quality": {}})


def test_p2_session_requires_timezone():
    schemas.parse_validated_line(_session(start_time="2023-01-03T17:58:04+00:00"))
    schemas.parse_validated_line(_session(start_time="2023-01-03T17:58:04Z"))
    with pytest.raises(ValidationError, match="timezone|ISO"):
        schemas.parse_validated_line(_session(start_time="2023-01-03T17:58:04"))


def test_health_since_must_be_iso():
    schemas.Health(state="HEALTHY", since="2023-02-01")
    schemas.Health(state="HEALTHY", since="2023-02-01T00:00:00+00:00")
    schemas.Health(state="HEALTHY")  # absent == null
    with pytest.raises(ValidationError):
        schemas.Health(state="HEALTHY", since="not a date")


def test_extra_fields_preserved():
    model = schemas.parse_canonical_line(
        {"record_type": "maintenance", "event_id": "e1", "description": "d",
         "provenance": _PROV, "quality": {}, "raw_payload": {"anything": [1, 2]}})
    assert model.model_dump()["raw_payload"] == {"anything": [1, 2]}  # §5.0


def test_golden_charger_requires_health_and_metrics():
    base = {"record_type": "charger", "golden_id": "g-1", "provenance": _PROV, "quality": {}}
    with pytest.raises(ValidationError):
        schemas.parse_golden_line(base)
    schemas.parse_golden_line({**base,
                               "health": {"state": "HEALTHY"},
                               "metrics": {"fault_recurrence_count": 0}})
