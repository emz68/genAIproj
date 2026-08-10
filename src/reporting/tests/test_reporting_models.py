"""Model tests: §5.0 semantics — extra fields preserved, absent == null,
unknown record_types tolerated, §5.5 report models parse."""

from __future__ import annotations

import json

from src.reporting.models import (
    GoldenCharger,
    GoldenSession,
    PipelineHealth,
    ReconciliationReport,
    ValidationReport,
    parse_golden_line,
)
from src.reporting.tests.conftest import FIXTURES


def test_extra_fields_preserved():
    line = (
        '{"record_type": "charger", "charger_id": "X1", '
        '"future_field": {"nested": 42}, "quality": {"score": 0.5}}'
    )
    rec = parse_golden_line(line)
    assert isinstance(rec, GoldenCharger)
    assert rec.future_field == {"nested": 42}  # never rejected, never dropped


def test_absent_is_null():
    line = '{"record_type": "session", "session_id": "S1"}'
    rec = parse_golden_line(line)
    assert isinstance(rec, GoldenSession)
    assert rec.energy_kwh is None
    assert rec.start_time is None
    assert rec.quality is None  # absent quality block
    assert rec.anomalies == []  # defaulted, not required


def test_unknown_record_type_preserved():
    line = '{"record_type": "future_kind", "thing": 1}'
    rec = parse_golden_line(line)
    assert rec.record_type == "future_kind"
    assert rec.thing == 1


def test_blank_lines_skipped():
    # parse_golden_line on blank input returns an empty dict → generic model
    rec = parse_golden_line("")
    assert rec.record_type is None


def test_report_models_parse_fixtures():
    v = ValidationReport.model_validate(
        json.loads((FIXTURES / "validation_report.json").read_text(encoding="utf-8"))
    )
    assert v.records_in == 100 and v.quarantined == 2
    assert v.per_source["cary_sessions"].issues == {"missing_energy_kwh": 5, "stale_report": 2}

    r = ReconciliationReport.model_validate(
        json.loads((FIXTURES / "reconciliation_report.json").read_text(encoding="utf-8"))
    )
    assert r.golden_records_out == 18
    assert r.per_source_lag_days["cary_sessions"].p95 == 8.0

    p = PipelineHealth.model_validate(
        json.loads((FIXTURES / "pipeline_health.json").read_text(encoding="utf-8"))
    )
    assert p.stages["ingestion"].llm_calls == 24
    assert p.stages["reporting"].exit_code == 0


def test_report_models_allow_extra():
    raw = '{"records_in": 1, "some_future_key": [1, 2, 3]}'
    v = ValidationReport.model_validate(json.loads(raw))
    assert v.some_future_key == [1, 2, 3]
