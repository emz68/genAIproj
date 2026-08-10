"""Y4 — Safety, health states & derived metrics (§5.4).

P3 is the *sole computer* of all derived metrics; P4 renders them, never
recomputes (§7 boundary rules).

Health state resolution (priority order, deterministic):
1. ``SAFETY_REVIEW`` — any SAFETY anomaly on the charger
   (``unresolved_safety_event``, ``power_over_rated``);
2. ``SUSPECT_OUTAGE`` — a ``utilization_cliff`` anomaly;
3. ``DEGRADED`` — ``repeated_fault_code``, ``energy_outlier``,
   ``concurrent_sessions``, ``duplicate_billing``, or est. uptime < 95%;
4. ``HEALTHY`` — otherwise.

``health.since`` = ISO date of the earliest evidence driving the state;
``health.evidence`` = the detail strings of the deciding anomalies.

Metrics (per charger):
- ``est_uptime_pct`` = 1 − (suspected-outage days / days in period).
  Period = first→last session date (inclusive) on the charger; outage days =
  sum of qualifying cliff gaps (≥ ``CLIFF_GAP_DAYS``, ≥ 3 prior sessions,
  not explained by a MAJOR/SAFETY maintenance window). Null when the charger
  has no sessions. Clamped to [0, 1], rounded to 4 dp.
- ``fault_recurrence_count`` = number of sessions carrying a fault_code.
- ``reporting_lag_p50_days`` / ``reporting_lag_p95_days`` = percentile
  distribution of (ingested_at − start_time) over the charger's sessions.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .dates import lag_days, parse_iso, p50_p95

UPTIME_MIN = 0.95  # below this → DEGRADED

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _anomaly_types(charger: dict) -> List[str]:
    return [a["type"] for a in charger.get("anomalies", [])]


def _earliest_evidence_date(charger: dict, anomaly_types: List[str]) -> Optional[str]:
    """Earliest ISO date appearing in the deciding anomalies' details."""
    best: Optional[str] = None
    for a in charger.get("anomalies", []):
        if a["type"] not in anomaly_types:
            continue
        for m in _DATE_RE.findall(a.get("detail", "")):
            if best is None or m < best:
                best = m
    return best


def _global_period(rr) -> Optional[tuple]:
    """(start, end) datetimes of the global observation period — min/max
    across all golden sessions and maintenance events."""
    start = end = None
    for s in rr.golden_sessions:
        d = parse_iso(s.get("start_time"))
        if d is None:
            continue
        start = d if start is None else min(start, d)
        end = d if end is None else max(end, d)
    for e in rr.golden_events:
        d = parse_iso(e.get("event_date"))
        if d is None:
            continue
        start = d if start is None else min(start, d)
        end = d if end is None else max(end, d)
    if start is None or end is None:
        return None
    return (start, end)


def _outage_days(sessions: List[dict], events: List[dict], period_end) -> float:
    """Trailing silent days for the charger (same rule as the
    utilization_cliff anomaly: ≥ CLIFF_MIN_PRIOR_SESSIONS sessions, silent
    ≥ CLIFF_GAP_DAYS at period end, no MAJOR/SAFETY maintenance explaining
    the silence). SUSPECT_OUTAGE ⇔ outage > 0 by construction."""
    from .anomalies import CLIFF_GAP_DAYS, CLIFF_MIN_PRIOR_SESSIONS

    dated = [(s, parse_iso(s.get("start_time"))) for s in sessions]
    dated = [(s, d) for s, d in dated if d is not None]
    dated.sort(key=lambda sd: sd[1])
    if len(dated) < CLIFF_MIN_PRIOR_SESSIONS or period_end is None:
        return 0.0
    last_d = dated[-1][1]
    silent_days = (period_end - last_d).total_seconds() / 86400.0
    if silent_days < CLIFF_GAP_DAYS:
        return 0.0
    maint_explains = any(
        e.get("severity") in ("MAJOR", "SAFETY")
        and (parse_iso(e.get("event_date")) or last_d) > last_d
        for e in events
    )
    return 0.0 if maint_explains else silent_days


def compute_health_and_metrics(rr) -> None:
    """Mutates each golden charger in place with health + metrics."""
    period = _global_period(rr)
    period_days = None
    if period is not None:
        period_days = (period[1] - period[0]).days + 1
    for ci, chg in rr.charger_by_cluster.items():
        sessions = rr.sessions_by_cluster.get(ci, [])
        events = rr.events_by_cluster.get(ci, [])
        types = _anomaly_types(chg)

        # ---- Metrics first (health may depend on est_uptime) --------------
        metrics = chg.setdefault("metrics", {})
        metrics["fault_recurrence_count"] = sum(
            1 for s in sessions if s.get("fault_code")
        )
        lags = [
            lag_days((s.get("provenance") or {}).get("ingested_at"), s.get("start_time"))
            for s in sessions
        ]
        p50, p95 = p50_p95(lags)
        metrics["reporting_lag_p50_days"] = p50
        metrics["reporting_lag_p95_days"] = p95
        if sessions and period_days and period is not None:
            outage = _outage_days(sessions, events, period[1])
            uptime = max(0.0, min(1.0, 1.0 - outage / max(period_days, 1)))
            metrics["est_uptime_pct"] = round(uptime, 4)
        else:
            metrics["est_uptime_pct"] = None

        # ---- Health state -------------------------------------------------
        if any(t in types for t in ("unresolved_safety_event", "power_over_rated")):
            state = "SAFETY_REVIEW"
        elif "utilization_cliff" in types:
            state = "SUSPECT_OUTAGE"
        elif (
            "repeated_fault_code" in types
            or "energy_outlier" in types
            or "concurrent_sessions" in types
            or "duplicate_billing" in types
            or (metrics.get("est_uptime_pct") is not None and metrics["est_uptime_pct"] < UPTIME_MIN)
        ):
            state = "DEGRADED"
        else:
            state = "HEALTHY"

        deciding = {
            "SAFETY_REVIEW": ("unresolved_safety_event", "power_over_rated"),
            "SUSPECT_OUTAGE": ("utilization_cliff",),
            "DEGRADED": (
                "repeated_fault_code",
                "energy_outlier",
                "concurrent_sessions",
                "duplicate_billing",
            ),
            "HEALTHY": (),
        }[state]
        evidence = [
            a["detail"] for a in chg.get("anomalies", []) if a["type"] in deciding
        ]
        since = _earliest_evidence_date(chg, list(deciding)) if deciding else None
        chg["health"] = {
            "state": state,
            "since": since,
            "evidence": evidence,
        }
