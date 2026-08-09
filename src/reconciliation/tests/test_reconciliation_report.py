"""reconciliation_report.json tests (Y5, §5.5 frozen shape)."""

from __future__ import annotations

import json
from pathlib import Path

from src.reconciliation.anomalies import detect_anomalies
from src.reconciliation.health import compute_health_and_metrics
from src.reconciliation.reconcile import build_golden
from src.reconciliation.report import build_report
from src.reconciliation.resolve import resolve

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _report(path):
    chargers, sessions, events = [], [], []
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            rt = rec["record_type"]
            (chargers if rt == "charger" else sessions if rt == "session" else events).append(rec)
    res = resolve(chargers, sessions, events)
    rr = build_golden(res)
    counts = detect_anomalies(res, rr)
    compute_health_and_metrics(rr)
    out = len(rr.golden_chargers) + len(rr.golden_sessions) + len(rr.golden_events)
    return build_report(len(chargers) + len(sessions) + len(events), rr, counts, out)


def test_report_shape_and_counts():
    rep = _report(FIXTURES / "validated.jsonl")
    assert set(rep) == {
        "records_in", "golden_records_out", "duplicates_removed", "clusters",
        "conflicts_resolved", "anomalies", "per_source_lag_days",
    }
    assert rep["records_in"] == 33
    assert rep["golden_records_out"] == 29
    assert rep["duplicates_removed"] == 4  # 1 charger merge + 1 exact ses dup + 1 fuzzy dup + 1 event dup
    assert rep["clusters"] == 4
    assert rep["conflicts_resolved"] == 5  # 2 charger fields (B1) + 3 session fields (a1-201/202)
    assert rep["anomalies"] == {
        "concurrent_sessions": 1,
        "duplicate_billing": 1,
        "energy_outlier": 1,
        "power_over_rated": 1,
        "repeated_fault_code": 1,
        "unresolved_safety_event": 1,
        "utilization_cliff": 1,
    }
    assert set(rep["per_source_lag_days"]) == {"cary", "contractor"}
    for src, lags in rep["per_source_lag_days"].items():
        assert set(lags) == {"p50", "p95"}
        assert lags["p50"] <= lags["p95"]


def test_report_hand_computable_lags():
    rep = _report(FIXTURES / "validated.jsonl")
    # contractor events ingested 2024-11-20: evt-x1 09-20 (61d), evt-c1 11-01
    # (19d), evt-c2 11-05 (15d), evt-b1 10-01 (50d) → sorted [15,19,50,61]
    assert rep["per_source_lag_days"]["contractor"] == {"p50": 19.0, "p95": 61.0}
    # cary sessions ingested 2024-11-10: p50 = 178.5 (b1-002 05-15), p95 = 292.625
    assert rep["per_source_lag_days"]["cary"]["p50"] == 178.5
    assert rep["per_source_lag_days"]["cary"]["p95"] == 292.625


def test_report_consistent_with_golden():
    rep = _report(FIXTURES / "validated.jsonl")
    # records_in − duplicates_removed == golden_records_out
    assert rep["records_in"] - rep["duplicates_removed"] == rep["golden_records_out"]
    # anomaly counts sum == total anomalies attached to golden records
    total = sum(rep["anomalies"].values())
    assert total == 7
