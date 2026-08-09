"""Golden-record tests (Y2, §5.4): shape, determinism, conflict resolution,
maintenance pass-through, and supersede semantics (late-arriving superset)."""

from __future__ import annotations

import json
from pathlib import Path

from src.reconciliation.anomalies import detect_anomalies
from src.reconciliation.health import compute_health_and_metrics
from src.reconciliation.models import GoldenCharger, GoldenMaintenance, GoldenSession
from src.reconciliation.reconcile import build_golden
from src.reconciliation.resolve import resolve

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(path):
    chargers, sessions, events = [], [], []
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            rt = rec["record_type"]
            (chargers if rt == "charger" else sessions if rt == "session" else events).append(rec)
    return chargers, sessions, events


def _run(path):
    chargers, sessions, events = _load(path)
    res = resolve(chargers, sessions, events)
    rr = build_golden(res)
    detect_anomalies(res, rr)
    compute_health_and_metrics(rr)
    return rr


def test_golden_records_parse_against_schema():
    rr = _run(FIXTURES / "validated.jsonl")
    for c in rr.golden_chargers:
        GoldenCharger.model_validate(c)
    for s in rr.golden_sessions:
        GoldenSession.model_validate(s)
    for e in rr.golden_events:
        GoldenMaintenance.model_validate(e)


def test_golden_id_deterministic_and_unique():
    rr1 = _run(FIXTURES / "validated.jsonl")
    rr2 = _run(FIXTURES / "validated.jsonl")
    for a, b in zip(rr1.golden_chargers, rr2.golden_chargers):
        assert a["golden_id"] == b["golden_id"]
    ids = [c["golden_id"] for c in rr1.golden_chargers]
    assert len(ids) == len(set(ids))  # never duplicate
    for s in rr1.golden_sessions:
        assert s["golden_id"].startswith("ses-")
    for e in rr1.golden_events:
        assert e["golden_id"].startswith("mnt-")


def test_conflict_resolution_freshness_wins():
    """B1 has two registry records: 6.6/ACTIVE (Jan) and 7.2/MAINTENANCE
    (Jun). Freshness must win; both conflicts logged; merged_from shows both."""
    rr = _run(FIXTURES / "validated.jsonl")
    b1 = next(c for c in rr.golden_chargers if c["charger_id"] == "ST-2001#1#J1772#1")
    assert b1["power_kw"] == 7.2
    assert b1["status"] == "MAINTENANCE"
    by_field = {cf["field"]: cf for cf in b1["conflicts_resolved"]}
    assert set(by_field) == {"power_kw", "status"}
    assert by_field["power_kw"]["chosen"] == 7.2
    assert by_field["power_kw"]["rejected"] == [6.6]
    assert by_field["status"]["chosen"] == "MAINTENANCE"
    assert by_field["status"]["rejected"] == ["ACTIVE"]
    assert len(b1["merged_from"]) == 2
    assert all(m["source"] == "afdc-bts" for m in b1["merged_from"])


def test_maintenance_pass_through_with_golden_id():
    rr = _run(FIXTURES / "validated.jsonl")
    by_id = {e["event_id"]: e for e in rr.golden_events}
    assert set(by_id) == {"evt-b1", "evt-c1", "evt-c2", "evt-x1"}  # evt-c1 dup removed
    assert by_id["evt-b1"]["charger_golden_id"] == next(
        c["golden_id"] for c in rr.golden_chargers if c["charger_id"] == "ST-2001#1#J1772#1"
    )
    assert by_id["evt-c1"]["charger_golden_id"] == next(
        c["golden_id"] for c in rr.golden_chargers if c["charger_id"] == "ST-3001#1#J1772#1"
    )
    assert "charger_golden_id" not in by_id["evt-x1"]  # unresolved passes through
    assert by_id["evt-x1"]["event_id"] == "evt-x1"


def test_supersede_semantics():
    """Re-running over a superset (late-arriving records) must keep the same
    golden_id for existing entities and never duplicate them (Y2)."""
    base = _run(FIXTURES / "validated.jsonl")
    late = _run(FIXTURES / "validated_late.jsonl")

    base_chg = {c["golden_id"] for c in base.golden_chargers}
    late_chg = {c["golden_id"] for c in late.golden_chargers}
    assert base_chg <= late_chg  # same entities keep the same golden_id
    assert len(late_chg) == len(base_chg) + 1  # only the new ST-4001 station

    base_ses = {s["golden_id"] for s in base.golden_sessions}
    late_ses = {s["golden_id"] for s in late.golden_sessions}
    assert base_ses <= late_ses

    base_evt = {e["golden_id"] for e in base.golden_events}
    late_evt = {e["golden_id"] for e in late.golden_events}
    assert base_evt <= late_evt

    # No duplicates: golden_ids unique within each type in the superset run.
    assert len(late_chg) == len({c["golden_id"] for c in late.golden_chargers})
    assert len(late_ses) == len({s["golden_id"] for s in late.golden_sessions})


def test_late_session_attaches_to_existing_charger():
    """The late A1 session (Oct 20) must join the existing A1 golden charger
    and clear the utilization cliff (charger came back online)."""
    late = _run(FIXTURES / "validated_late.jsonl")
    a1 = next(c for c in late.golden_chargers if c["charger_id"] == "ST-1001#1#J1772#1")
    a1_sessions = [s for s in late.golden_sessions if s.get("charger_golden_id") == a1["golden_id"]]
    assert len(a1_sessions) == 9  # 8 base + 1 late
    assert a1["health"]["state"] == "HEALTHY"
    assert a1["metrics"]["est_uptime_pct"] == 1.0
    # The late session itself is a golden session with the same entity key.
    late_ses = {s["session_id"]: s for s in late.golden_sessions}
    assert late_ses["cary-a1-301"]["charger_golden_id"] == a1["golden_id"]
