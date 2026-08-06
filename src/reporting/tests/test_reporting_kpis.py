"""Hand-computed KPI tests (M5 correctness requirement).

All expected numbers below are computed by hand from the committed fixture
``src/reporting/fixtures/golden.jsonl`` (6 chargers, 8 sessions, 4 maintenance
events). See also the module README for the fixture truth table.
"""

from __future__ import annotations

from src.reporting.models import GoldenCharger, GoldenMaintenance, GoldenSession


def test_record_type_split(records):
    assert len(records) == 18
    chargers = [r for r in records if isinstance(r, GoldenCharger)]
    sessions = [r for r in records if isinstance(r, GoldenSession)]
    maintenance = [r for r in records if isinstance(r, GoldenMaintenance)]
    assert len(chargers) == 6
    assert len(sessions) == 8
    assert len(maintenance) == 4


def test_deployment_kpis(kpis):
    assert kpis.chargers_deployed == 6
    assert kpis.active_chargers == 4  # C001, C002, C005, C006 = ACTIVE
    assert kpis.inactive_chargers == 2  # C003 MAINTENANCE, C004 INACTIVE


def test_energy_and_sessions(kpis):
    # 10.0 + 12.5 + 8.0 + 15.0 + 20.0 + 7.5 + 9.0 + 18.0 = 100.0
    assert kpis.energy_delivered_kwh == 100.0
    assert kpis.sessions_count == 8
    assert kpis.sessions_missing_energy == 0


def test_uptime_mean(kpis):
    # (0.98 + 0.87 + 0.55 + 0.72 + 0.99 + 0.91) / 6 = 5.02 / 6 = 0.83666…
    assert kpis.est_uptime_pct is not None
    assert abs(kpis.est_uptime_pct - 5.02 / 6) < 1e-9
    assert kpis.chargers_with_metrics == 6


def test_data_completeness_mean(kpis):
    # mean quality.score over charger + session records:
    # chargers: 0.95+0.90+0.80+0.85+0.92+0.88 = 5.30
    # sessions: 0.97+0.97+0.90+0.96+0.91+0.85+0.95+0.93 = 7.44
    # (5.30 + 7.44) / 14 = 12.74 / 14 = 0.91
    assert kpis.data_completeness_pct is not None
    assert abs(kpis.data_completeness_pct - 12.74 / 14) < 1e-9


def test_health_states(kpis):
    assert kpis.health_states == {
        "HEALTHY": 2,
        "DEGRADED": 2,
        "SUSPECT_OUTAGE": 1,
        "SAFETY_REVIEW": 1,
    }


def test_fault_recurrence(kpis):
    assert kpis.max_fault_recurrence == 3  # C004
    assert len(kpis.chronic_failure_sites) == 1
    assert kpis.chronic_failure_sites[0]["charger_id"] == "C004"
    assert kpis.chronic_failure_sites[0]["fault_recurrence_count"] == 3


def test_anomaly_counts(kpis):
    assert kpis.anomalies_total == 7
    assert kpis.anomaly_severity == {"SAFETY": 1, "CRITICAL": 1, "WARN": 3, "INFO": 2}
    assert kpis.anomaly_types == {
        "reporting_lag": 2,
        "fault_recurrence": 1,
        "suspected_outage": 1,
        "energy_outlier": 2,
        "safety_event": 1,
    }


def test_maintenance_events(kpis):
    assert kpis.maintenance_events == 4
    assert kpis.maintenance_by_severity == {"SAFETY": 1, "MAJOR": 1, "MINOR": 1, "INFO": 1}


def test_as_dict_full_precision(kpis):
    d = kpis.as_dict()
    assert d["chargers_deployed"] == 6
    assert d["active_chargers"] == 4
    assert d["energy_delivered_kwh"] == 100.0
    # full precision preserved — display rounding happens in the render layer
    assert abs(d["est_uptime_pct"] - 5.02 / 6) < 1e-9
    assert abs(d["data_completeness_pct"] - 12.74 / 14) < 1e-9
    assert d["anomalies_total"] == 7
    assert d["chronic_failure_sites"] == 1
    assert d["maintenance_events"] == 4
