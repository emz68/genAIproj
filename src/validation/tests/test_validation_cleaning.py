"""End-to-end per-record pipeline tests (S3, S4, S5, S6) — src/validation/."""

from __future__ import annotations

from src.validation.cleaning import CleaningPipeline
from src.validation.rules import Rules


def _pipeline(llm_enabled: bool = False) -> CleaningPipeline:
    return CleaningPipeline(Rules.load(), llm_enabled=llm_enabled)


def _process(record: dict) -> tuple[dict, bool]:
    out, quarantined = _pipeline().process(record)
    assert out is not None  # fixtures are all canonical record types
    return out, quarantined


def _session(**overrides) -> dict:
    record = {
        "record_type": "session",
        "session_id": "S-1",
        "charger_id": "CHG-1",
        "station_id": "TOWN OF CARY / TOWN HALL-PWH",
        "start_time": "2023-01-03T10:00:00+00:00",
        "end_time": "2023-01-03T11:00:00+00:00",
        "energy_kwh": 5.0,
        "peak_kw": 6.0,
        "duration_min": 60.0,
        "fault_code": None,
        "provenance": {"source": "cary", "source_file": "f.csv.gz", "ingested_at": "2023-01-04T09:00:00+00:00"},
        "quality": {"score": 0.9},
    }
    record.update(overrides)
    return record


def _charger(**overrides) -> dict:
    record = {
        "record_type": "charger",
        "charger_id": "CHG-1",
        "station_id": "TOWN OF CARY / DT DECK P2 (2)",
        "address": {"street": "113 Walnut St", "city": "Cary", "state": "nc", "zip": "27511-1234"},
        "lat": 35.7784,
        "lon": -78.6435,
        "connector_type": "chgpt LVL2 dual",
        "power_kw": 8000,
        "level": None,
        "status": "under maintenance",
        "install_date": "4/15/19",
        "provenance": {"source": "afdc", "source_file": "f.geojson.gz", "ingested_at": "2026-07-28T09:00:00+00:00"},
        "quality": {"score": 0.9},
    }
    record.update(overrides)
    return record


# -- S2/S3/S4 end-to-end ----------------------------------------------------


def test_messy_charger_fully_cleaned():
    out, quarantined = _process(_charger())
    assert quarantined is False
    assert out["connector_type"] == "CHAdeMO"
    assert out["level"] == "L2"          # derived from repaired power_kw
    assert out["status"] == "MAINTENANCE"
    assert out["power_kw"] == 8.0        # W → kW
    assert out["install_date"] == "2019-04-15"
    assert out["address"]["zip"] == "27511"
    assert out["address"]["state"] == "NC"
    assert out["quality"]["score"] == 0.9
    assert out["quality"]["extraction_confidence"] == 0.9
    assert out["quality"]["issues"] == []
    fix_actions = [f.split(":")[0] for f in out["quality"]["fixes_applied"]]
    assert "unit_repair" in fix_actions and "derive" in fix_actions and "enum" in fix_actions


def test_missing_duration_derived_from_timestamps():
    out, _ = _process(_session(duration_min=None))
    assert out["duration_min"] == 60.0
    assert any(f.startswith("derive:duration_min:") for f in out["quality"]["fixes_applied"])
    assert out["quality"]["issues"] == []


def test_duration_contradiction_recomputed():
    out, _ = _process(_session(duration_min=30.0))
    assert out["duration_min"] == 60.0
    assert any(f.startswith("fix:duration_min:") for f in out["quality"]["fixes_applied"])
    assert out["quality"]["issues"] == []  # fixed, so no remaining issue


def test_impossible_value_flagged_not_dropped():
    out, quarantined = _process(_session(energy_kwh=-5.0))
    assert out["energy_kwh"] == -5.0          # value preserved
    assert out["quality"]["issues"] == ["impossible_energy_kwh"]
    assert out["quality"]["score"] == 0.65    # 0.9 − 0.25


def test_unparseable_datetime_stashed_and_dropped():
    out, _ = _process(_session(start_time="yesterday-ish"))
    assert "start_time" not in out
    assert out["raw_start_time"] == "yesterday-ish"
    assert out["quality"]["issues"] == ["invalid_start_time"]


def test_unparseable_numeric_stashed_and_dropped():
    out, _ = _process(_session(energy_kwh="lots"))
    assert "energy_kwh" not in out
    assert out["raw_energy_kwh"] == "lots"
    assert out["quality"]["issues"] == ["invalid_energy_kwh"]


def test_extra_fields_preserved():
    out, _ = _process(_session(raw_payload={"vendor_note": "migrated"}))
    assert out["raw_payload"] == {"vendor_note": "migrated"}


def test_null_fields_omitted_from_output():
    out, _ = _process(_session(session_id=None, fault_code=None))
    assert "session_id" not in out
    assert "fault_code" not in out


def test_quality_block_created_when_absent():
    record = _session()
    del record["quality"]
    out, _ = _process(record)
    assert out["quality"]["score"] == 1.0
    assert "extraction_confidence" not in out  # no P1 score to preserve
    assert out["quality"]["issues"] == []
    assert out["quality"]["fixes_applied"] == []


def test_duplicates_pass_through_untouched_and_unflagged():
    record = _session()
    first, _ = _process(dict(record))
    second, _ = _process(dict(record))
    assert first == second
    for out in (first, second):
        assert not any("duplicate" in code for code in out["quality"]["issues"])


def test_unknown_record_type_returns_none():
    out, quarantined = _pipeline().process({"record_type": "alien", "provenance": {"source": "x"}})
    assert out is None
    assert quarantined is False


def test_missing_required_provenance_field_flagged_and_emitted():
    record = _charger()
    del record["provenance"]["source"]
    out, _ = _process(record)
    assert out is not None  # never dropped
    assert "missing_provenance_source" in out["quality"]["issues"]
    assert out["quality"]["score"] < 0.9


def test_quarantined_record_still_emitted():
    record = _session(energy_kwh=-2.0, peak_kw="n/a",
                      start_time="2023-12-15T14:00:00+00:00",
                      end_time="2023-12-15T13:00:00+00:00",
                      quality={"score": 0.99})
    out, quarantined = _process(record)
    assert quarantined is True
    assert out is not None
    assert out["quality"]["score"] == 0.24  # 0.99 − 3 × 0.25


def test_llm_disabled_never_counts_calls():
    pipeline = _pipeline(llm_enabled=False)
    pipeline.process(_charger())
    assert pipeline.llm_calls == 0
    assert pipeline.llm_tokens == 0


def test_no_llm_repair_without_api_key():
    # No ANTHROPIC_API_KEY in the test environment ⇒ deterministic path even
    # when the LLM is "enabled".
    pipeline = _pipeline(llm_enabled=True)
    out, _ = pipeline.process(_charger())
    assert pipeline.llm_calls == 0
    assert out is not None
    assert out["connector_type"] == "CHAdeMO"
