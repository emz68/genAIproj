"""Y3 — Anomaly detection (cross-record / statistical).

All rules are deterministic and documented in the module README. Anomalies
are attached to the golden record they concern (charger-level on the golden
charger, session-level on the golden session) with the §5.4 shape:
``{"type", "severity", "detail", "evidence"}``.

Rules
-----
- ``duplicate_billing`` — a session dedup group merged ≥2 records with
  *distinct* session_ids (fuzzy duplicates: same charge reported twice).
  Severity WARN, CRITICAL when the merged energy is large (≥ 30 kWh).
  Attached to the golden session; evidence = all member session_ids.
- ``energy_outlier`` — per charger, session energy beyond Q3 + 3·IQR (or
  robust z > 5 on degenerate spreads); needs ≥ 5 sessions on the charger.
  WARN; attached to the golden session; evidence = session_id.
- ``utilization_cliff`` — charger previously active (≥ ``CLIFF_MIN_PRIOR_SESSIONS``
  sessions) then silent for ≥ ``CLIFF_GAP_DAYS`` (30 d) at the end of the
  observation period, with no MAJOR/SAFETY maintenance explaining the
  silence. CRITICAL; attached to the golden charger; evidence = last
  session_id. Drives SUSPECT_OUTAGE.
- ``concurrent_sessions`` — two sessions with overlapping [start, end) on the
  *same port* (shared non-null charger_id, or a single-port charger cluster).
  CRITICAL (physically impossible); evidence = both session_ids.
- ``repeated_fault_code`` — the same fault_code on ≥ 2 sessions of one
  charger. WARN; attached to the charger; evidence = session_ids.
- ``power_over_rated`` — session peak_kw > charger power_kw × 1.1. SAFETY;
  attached to the charger; evidence = session_id.
- ``unresolved_safety_event`` — a SAFETY maintenance event with no later
  REPAIR event on the same charger. SAFETY; attached to the charger;
  evidence = event_id. Drives SAFETY_REVIEW.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Optional

from .dates import lag_days, parse_iso, p50_p95, robust_outlier_bounds

CLIFF_GAP_DAYS = 30.0
CLIFF_MIN_PRIOR_SESSIONS = 3
CONCURRENCY_ENERGY_MIN = 0.5
DUP_BILLING_CRITICAL_KWH = 30.0
POWER_OVER_RATED_TOL = 1.1


def _sess_interval(sess: dict):
    a, b = parse_iso(sess.get("start_time")), parse_iso(sess.get("end_time"))
    if a is None or b is None:
        return None
    return (a, b)


def _overlaps(iv1, iv2) -> bool:
    a1, b1 = iv1
    a2, b2 = iv2
    return a1 < b2 and a2 < b1


def _period_end(rr):
    """Global observation-period end: max date across all golden sessions
    and maintenance events."""
    end = None
    for s in rr.golden_sessions:
        d = parse_iso(s.get("start_time"))
        if d is not None and (end is None or d > end):
            end = d
    for e in rr.golden_events:
        d = parse_iso(e.get("event_date"))
        if d is not None and (end is None or d > end):
            end = d
    return end


def detect_anomalies(res, rr) -> Dict[str, int]:
    """Attach anomalies to golden records; return {type: count}."""
    counts: Counter = Counter()
    period_end = _period_end(rr)

    # --- duplicate_billing: from session dedup groups with distinct ids ---
    for group in res.session_groups:
        members = [res.sessions[i] for i in group]
        ids = {str(m.get("session_id")) for m in members if m.get("session_id")}
        if len(ids) <= 1:
            continue  # exact duplicates only — data-entry dup, not double-billing
        golden = rr.golden_by_session_idx[group[-1]]
        energy = golden.get("energy_kwh") or 0.0
        sev = "CRITICAL" if energy >= DUP_BILLING_CRITICAL_KWH else "WARN"
        golden.setdefault("anomalies", []).append(
            {
                "type": "duplicate_billing",
                "severity": sev,
                "detail": (
                    f"session billed twice: {len(ids)} distinct session_ids "
                    f"({', '.join(sorted(ids))}) merged into one golden record"
                ),
                "evidence": sorted(ids),
            }
        )
        counts["duplicate_billing"] += 1

    # --- Per-charger statistical passes ------------------------------------
    for ci, golden_chg in rr.charger_by_cluster.items():
        sessions = rr.sessions_by_cluster.get(ci, [])
        events = rr.events_by_cluster.get(ci, [])
        if not sessions:
            continue

        # Energy outliers (per charger distribution).
        energies = [(s, s.get("energy_kwh")) for s in sessions]
        energies = [(s, float(e)) for s, e in energies if e is not None]
        bounds = robust_outlier_bounds([e for _, e in energies])
        if bounds and len(energies) >= 5:
            _, upper = bounds
            if upper is not None:
                for s, e in energies:
                    if e > upper:
                        s.setdefault("anomalies", []).append(
                            {
                                "type": "energy_outlier",
                                "severity": "WARN",
                                "detail": (
                                    f"energy {e:.2f} kWh exceeds upper fence "
                                    f"{upper:.2f} kWh for charger "
                                    f"{golden_chg['golden_id']}"
                                ),
                                "evidence": [str(s.get("session_id"))]
                                if s.get("session_id")
                                else [],
                            }
                        )
                        counts["energy_outlier"] += 1

        # Utilization cliffs (silent charger = suspected outage): active
        # charger (>= CLIFF_MIN_PRIOR_SESSIONS sessions) with no session in
        # the trailing CLIFF_GAP_DAYS of the observation period, unless a
        # MAJOR/SAFETY maintenance event explains the silence.
        dated = [(s, parse_iso(s.get("start_time"))) for s in sessions]
        dated = [(s, d) for s, d in dated if d is not None]
        dated.sort(key=lambda sd: sd[1])
        if len(dated) >= CLIFF_MIN_PRIOR_SESSIONS and period_end is not None:
            last_s, last_d = dated[-1]
            silent_days = (period_end - last_d).total_seconds() / 86400.0
            if silent_days >= CLIFF_GAP_DAYS:
                maint_explains = any(
                    e.get("severity") in ("MAJOR", "SAFETY")
                    and (parse_iso(e.get("event_date")) or last_d) > last_d
                    for e in events
                )
                if not maint_explains:
                    golden_chg.setdefault("anomalies", []).append(
                        {
                            "type": "utilization_cliff",
                            "severity": "CRITICAL",
                            "detail": (
                                f"no sessions for {int(silent_days)} days "
                                f"(last session {last_d.date().isoformat()}) "
                                f"after {len(dated)} prior sessions — "
                                f"suspected outage"
                            ),
                            "evidence": [str(last_s.get("session_id"))]
                            if last_s.get("session_id")
                            else [],
                        }
                    )
                    counts["utilization_cliff"] += 1

        # Concurrent sessions on one port. Each charger cluster IS one
        # physical port (P1 expands per-port), so any overlap within a
        # cluster is physically impossible.
        intervals = [(s, _sess_interval(s)) for s in sessions]
        intervals = [(s, iv) for s, iv in intervals if iv is not None]
        for i in range(len(intervals)):
            for j in range(i + 1, len(intervals)):
                s1, iv1 = intervals[i]
                s2, iv2 = intervals[j]
                if not _overlaps(iv1, iv2):
                    continue
                if (s1.get("energy_kwh") or 0) < CONCURRENCY_ENERGY_MIN and (
                    s2.get("energy_kwh") or 0
                ) < CONCURRENCY_ENERGY_MIN:
                    continue
                golden_chg.setdefault("anomalies", []).append(
                    {
                        "type": "concurrent_sessions",
                        "severity": "CRITICAL",
                        "detail": (
                            f"impossible concurrent sessions on port "
                            f"{golden_chg['charger_id'] or golden_chg['golden_id']}: "
                            f"{s1.get('start_time')}–{s1.get('end_time')} "
                            f"overlaps {s2.get('start_time')}–{s2.get('end_time')}"
                        ),
                        "evidence": [
                            str(s1.get("session_id")),
                            str(s2.get("session_id")),
                        ],
                    }
                )
                counts["concurrent_sessions"] += 1

        # Repeated fault codes.
        fault_groups: Dict[str, List[dict]] = defaultdict(list)
        for s in sessions:
            fc = s.get("fault_code")
            if fc:
                fault_groups[str(fc)].append(s)
        for fc, fc_sessions in fault_groups.items():
            if len(fc_sessions) >= 2:
                golden_chg.setdefault("anomalies", []).append(
                    {
                        "type": "repeated_fault_code",
                        "severity": "WARN",
                        "detail": (
                            f"fault code {fc!r} seen on {len(fc_sessions)} "
                            f"sessions of charger {golden_chg['golden_id']}"
                        ),
                        "evidence": [
                            str(s.get("session_id")) for s in fc_sessions
                        ],
                    }
                )
                counts["repeated_fault_code"] += 1

        # Power draw above rated power_kw.
        rated = golden_chg.get("power_kw")
        if rated:
            for s in sessions:
                pk = s.get("peak_kw")
                if pk is not None and float(pk) > float(rated) * POWER_OVER_RATED_TOL:
                    golden_chg.setdefault("anomalies", []).append(
                        {
                            "type": "power_over_rated",
                            "severity": "SAFETY",
                            "detail": (
                                f"session peak {pk} kW exceeds rated "
                                f"{rated} kW × {POWER_OVER_RATED_TOL} on "
                                f"charger {golden_chg['golden_id']} at "
                                f"{s.get('start_time')}"
                            ),
                            "evidence": [str(s.get("session_id"))]
                            if s.get("session_id")
                            else [],
                        }
                    )
                    counts["power_over_rated"] += 1

        # Unresolved SAFETY maintenance events. A SAFETY event is unresolved
        # unless a REPAIR on the *same port* (charger_id when both carry it,
        # else the same cluster) has event_date >= the safety event's date.
        safety_events = [e for e in events if e.get("severity") == "SAFETY"]
        for e in safety_events:
            ed = parse_iso(e.get("event_date"))
            if ed is None:
                continue
            e_port = e.get("charger_id")
            repairs = [
                parse_iso(x.get("event_date"))
                for x in events
                if x.get("event_type") == "REPAIR"
                and (x.get("charger_id") == e_port if e_port else True)
            ]
            if any(r is not None and r >= ed for r in repairs):
                continue
            golden_chg.setdefault("anomalies", []).append(
                {
                    "type": "unresolved_safety_event",
                    "severity": "SAFETY",
                    "detail": (
                        f"SAFETY maintenance event {e.get('event_id')} "
                        f"({e.get('event_date')}) has no subsequent REPAIR "
                        f"on charger {golden_chg['golden_id']}"
                    ),
                    "evidence": [str(e.get("event_id"))],
                }
            )
            counts["unresolved_safety_event"] += 1

    return dict(counts)


def per_source_lag_days(rr) -> Dict[str, Dict[str, Optional[float]]]:
    """Per-source reporting-lag distributions (Y3, feeds §5.5 report)."""
    lags: Dict[str, List[float]] = defaultdict(list)
    for s in rr.golden_sessions:
        prov = s.get("provenance") or {}
        lag = lag_days(prov.get("ingested_at"), s.get("start_time"))
        if lag is not None:
            lags[str(prov.get("source"))].append(lag)
    for e in rr.golden_events:
        prov = e.get("provenance") or {}
        lag = lag_days(prov.get("ingested_at"), e.get("event_date"))
        if lag is not None:
            lags[str(prov.get("source"))].append(lag)
    out = {}
    for src, vals in lags.items():
        p50, p95 = p50_p95(vals)
        out[src] = {"p50": p50, "p95": p95}
    return out
