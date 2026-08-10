"""Anomaly detection tests (Y3): every planted anomaly is caught with the
right severity, type, and evidence. Health/metrics tests (Y4)."""

from __future__ import annotations

import json
from pathlib import Path

from src.reconciliation.anomalies import detect_anomalies
from src.reconciliation.health import compute_health_and_metrics
from src.reconciliation.reconcile import build_golden
from src.reconciliation.resolve import resolve

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _run(path):
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
    return rr, counts


def _charger(rr, charger_id):
    return next(c for c in rr.golden_chargers if c["charger_id"] == charger_id)


def _charger_anomalies(rr, charger_id):
    return {a["type"]: a for a in _charger(rr, charger_id)["anomalies"]}


def test_all_plants_caught():
    rr, counts = _run(FIXTURES / "validated.jsonl")
    assert counts == {
        "concurrent_sessions": 1,
        "duplicate_billing": 1,
        "energy_outlier": 1,
        "power_over_rated": 1,
        "repeated_fault_code": 1,
        "unresolved_safety_event": 1,
        "utilization_cliff": 1,
    }


def test_utilization_cliff_on_a1():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    a1 = _charger_anomalies(rr, "ST-1001#1#J1772#1")
    cliff = a1["utilization_cliff"]
    assert cliff["severity"] == "CRITICAL"
    assert "no sessions for" in cliff["detail"]


def test_energy_outlier_on_a1_session():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    outlier = next(
        s for s in rr.golden_sessions
        if s["session_id"] == "cary-a1-101"
    )
    assert len(outlier["anomalies"]) == 1
    a = outlier["anomalies"][0]
    assert a["type"] == "energy_outlier"
    assert a["severity"] == "WARN"
    assert "42.7" in a["detail"]
    assert a["evidence"] == ["cary-a1-101"]


def test_duplicate_billing_on_a1():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    # duplicate_billing is a session-level anomaly: attached to the golden
    # session that absorbed the near-identical pair (a1-201 survives).
    sess = next(s for s in rr.golden_sessions if s["session_id"] == "cary-a1-201")
    assert len(sess["anomalies"]) == 1
    dup = sess["anomalies"][0]
    assert dup["type"] == "duplicate_billing"
    assert dup["severity"] == "WARN"  # energy 5.4 < 30 kWh
    assert sorted(dup["evidence"]) == ["cary-a1-201", "cary-a1-202"]


def test_concurrent_sessions_on_a2():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    a2 = _charger_anomalies(rr, "ST-1001#2#CCS#1")
    conc = a2["concurrent_sessions"]
    assert conc["severity"] == "CRITICAL"
    assert sorted(conc["evidence"]) == ["cary-a2-001", "cary-a2-002"]


def test_repeated_fault_code_on_a2():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    a2 = _charger_anomalies(rr, "ST-1001#2#CCS#1")
    rep = a2["repeated_fault_code"]
    assert rep["severity"] == "WARN"
    assert "E-42" in rep["detail"]
    assert sorted(rep["evidence"]) == ["cary-a2-001", "cary-a2-002"]


def test_power_over_rated_on_b1():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    b1 = _charger_anomalies(rr, "ST-2001#1#J1772#1")
    por = b1["power_over_rated"]
    assert por["severity"] == "SAFETY"
    assert por["evidence"] == ["cary-b1-002"]  # peak 9.2 kW on 7.2 kW rated


def test_unresolved_safety_event_on_b1():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    b1 = _charger_anomalies(rr, "ST-2001#1#J1772#1")
    u = b1["unresolved_safety_event"]
    assert u["severity"] == "SAFETY"
    assert u["evidence"] == ["evt-b1"]


def test_resolved_safety_event_not_flagged():
    """evt-c1 (SAFETY) has a later REPAIR (evt-c2) → no anomaly on C1."""
    rr, _ = _run(FIXTURES / "validated.jsonl")
    c1 = _charger(rr, "ST-3001#1#J1772#1")
    assert c1["anomalies"] == []


def test_health_states():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    h = lambda cid: _charger(rr, cid)["health"]["state"]
    assert h("ST-1001#1#J1772#1") == "SUSPECT_OUTAGE"
    assert h("ST-1001#2#CCS#1") == "DEGRADED"
    assert h("ST-2001#1#J1772#1") == "SAFETY_REVIEW"
    assert h("ST-3001#1#J1772#1") == "HEALTHY"


def test_health_evidence_and_since():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    b1 = _charger(rr, "ST-2001#1#J1772#1")
    assert b1["health"]["state"] == "SAFETY_REVIEW"
    assert b1["health"]["since"] == "2024-05-15"  # power_over_rated session date
    assert len(b1["health"]["evidence"]) == 2  # both SAFETY anomaly details
    c1 = _charger(rr, "ST-3001#1#J1772#1")
    assert c1["health"]["since"] is None
    assert c1["health"]["evidence"] == []


def test_metrics_est_uptime():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    # Global period = first session 2024-01-08T09:00 → last event 2024-11-05
    # = 302 days. A1's last session is 2024-03-28T08:00 → silent 221.6667 d
    # → uptime = 1 − 221.6667/302 = 0.2660 (per §5.4 formula).
    a1 = _charger(rr, "ST-1001#1#J1772#1")
    assert a1["metrics"]["est_uptime_pct"] == round(1 - 221.6666667 / 302, 4)
    c1 = _charger(rr, "ST-3001#1#J1772#1")
    assert c1["metrics"]["est_uptime_pct"] == 1.0


def test_metrics_fault_recurrence_and_lag():
    rr, _ = _run(FIXTURES / "validated.jsonl")
    a2 = _charger(rr, "ST-1001#2#CCS#1")
    assert a2["metrics"]["fault_recurrence_count"] == 2  # both E-42 sessions
    c1 = _charger(rr, "ST-3001#1#J1772#1")
    assert c1["metrics"]["fault_recurrence_count"] == 0
    # A2 sessions 2024-04-10T09:00 / 09:20, ingested 2024-11-10T00:00 →
    # lags 213.625 / 213.6111 → p50 = lower, p95 = higher (nearest-rank).
    assert a2["metrics"]["reporting_lag_p50_days"] == 213.61111111111111
    assert a2["metrics"]["reporting_lag_p95_days"] == 213.625
