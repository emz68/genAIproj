"""Dashboard (M2), safety view (M3), CX view (M4) content tests."""

from __future__ import annotations

from src.reporting.kpis import CHRONIC_FAILURE_THRESHOLD
from src.reporting.summaries import generate_summaries
from src.reporting.views import render_cx_view, render_dashboard, render_safety_view

GEN_AT = "2026-08-06T00:00:00"


# ---------------------------------------------------------------------------
# M2 — dashboard
# ---------------------------------------------------------------------------


def test_dashboard_fleet_grid_and_map(records, kpis):
    html = render_dashboard(records, kpis, None, None, GEN_AT)
    assert "Operational Awareness Dashboard" in html
    assert "Fleet status grid" in html
    assert "Fleet map" in html
    assert "<svg" in html and "SAFETY_REVIEW" in html
    # all six chargers are rendered as cards
    for cid in ("C001", "C002", "C003", "C004", "C005", "C006"):
        assert cid in html


def test_dashboard_anomaly_feed_ordering(records, kpis):
    html = render_dashboard(records, kpis, None, None, GEN_AT)
    assert "Anomaly feed by severity" in html
    # SAFETY anomaly (C004) appears before WARN/INFO anomalies
    assert html.index("safety_event") < html.index("reporting_lag")
    assert "M001" in html  # evidence id


def test_dashboard_league_table(records, kpis, validation_report):
    html = render_dashboard(records, kpis, validation_report, None, GEN_AT)
    assert "Per-contractor data-quality league table" in html
    # afdc_stations (0.97) ranks above cary_sessions (0.93)
    assert html.index("afdc_stations") < html.index("cary_sessions")
    assert "0.970" in html and "0.930" in html


def test_dashboard_latency_tracker(records, kpis, reconciliation_report):
    html = render_dashboard(records, kpis, None, reconciliation_report, GEN_AT)
    assert "Reporting-latency tracker" in html
    assert "Reporting lag p50 (days)" in html
    assert "2.5" in html and "8.0" in html  # Yash's cary figures, rendered only


def test_dashboard_data_unavailable(records, kpis):
    html = render_dashboard(records, kpis, None, None, GEN_AT)
    assert "league table data unavailable" in html
    assert "latency tracker data unavailable" in html


# ---------------------------------------------------------------------------
# M3 — safety view
# ---------------------------------------------------------------------------


def test_safety_triage_order(records, kpis):
    html = render_safety_view(records, kpis, GEN_AT)
    assert "Safety &amp; Infrastructure-Health View" in html
    # SAFETY_REVIEW charger (C004) triaged before DEGRADED/HEALTHY chargers
    assert html.index("C004") < html.index("C001")
    assert html.index("C004") < html.index("C002")
    # SUSPECT_OUTAGE (C003) before DEGRADED
    assert html.index("C003") < html.index("C002")


def test_safety_hot_anomalies(records, kpis):
    html = render_safety_view(records, kpis, GEN_AT)
    assert "SAFETY &amp; CRITICAL anomalies" in html
    assert "safety_event" in html
    assert "suspected_outage" in html
    assert "energy_outlier" not in html  # WARN — not hot


def test_safety_maintenance_evidence(records, kpis):
    html = render_safety_view(records, kpis, GEN_AT)
    assert "Maintenance evidence (pass-through)" in html
    for eid in ("M001", "M002", "M003", "M004"):
        assert eid in html
    assert "Connector overheating" in html  # description
    assert "g-m001" in html  # golden_id of the event


# ---------------------------------------------------------------------------
# M4 — CX view
# ---------------------------------------------------------------------------


def _cx_html(records, kpis, summaries):
    from src.reporting.models import GoldenCharger, GoldenSession

    chargers = [r for r in records if isinstance(r, GoldenCharger)]
    sessions = [r for r in records if isinstance(r, GoldenSession)]
    return render_cx_view(chargers, sessions, kpis, summaries, GEN_AT)


def test_cx_location_rollup(records, kpis):
    summaries = generate_summaries(kpis, no_llm=True)
    html = _cx_html(records, kpis, summaries)
    assert "Availability &amp; reliability by location" in html
    # Raleigh: C001+C005 → uptime (0.98+0.99)/2 = 98.5%, 3 sessions, 31.5 kWh
    assert "Raleigh" in html
    assert "98.5%" in html
    assert "31.5" in html
    # Cary: C002+C003 → (0.87+0.55)/2 = 71.0%, 3 sessions, 43.0 kWh
    assert "71.0%" in html
    assert "43.0" in html
    # Durham: 1 charger, 72.0%, 1 session, 7.5 kWh
    assert "72.0%" in html
    assert "7.5" in html


def test_cx_chronic_failure_sites(records, kpis):
    summaries = generate_summaries(kpis, no_llm=True)
    html = _cx_html(records, kpis, summaries)
    assert "Chronic-failure sites" in html
    assert "C004" in html
    assert "3" in html  # fault recurrence count


def test_cx_no_chronic_failure_message(records, kpis):
    from src.reporting.kpis import Kpis

    # drop C004 → no chronic-failure sites
    others = [r for r in records if getattr(r, "charger_id", None) != "C004"]
    slim = Kpis(others)
    summaries = generate_summaries(slim, no_llm=True)
    html = _cx_html(others, slim, summaries)
    assert f"No sites with fault recurrence ≥ {CHRONIC_FAILURE_THRESHOLD}." in html


def test_cx_summaries_are_deterministic_templates(records, kpis):
    a = generate_summaries(kpis, no_llm=True)
    b = generate_summaries(kpis, no_llm=True)
    assert a == b
    assert all(not s["llm_used"] for s in a)
    # template text quotes the hand-computed fleet numbers
    fleet = next(s for s in a if s["title"] == "Fleet summary")
    assert "6 chargers" in fleet["text"]
    assert "83.7%" in fleet["text"]
    assert "91.0%" in fleet["text"]
    assert "100.0 kWh" in fleet["text"]
