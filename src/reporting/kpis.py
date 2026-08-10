"""P4 — Reporting & Experience: KPI aggregation (RENDER ONLY, streaming).

Boundary rule (§7): *Emily renders; she computes no availability, latency, or
quality math herself.* Everything here is aggregation of values that earlier
stages already computed:

  * ``metrics.est_uptime_pct`` / ``metrics.fault_recurrence_count`` — Yash (P3)
  * ``health.state`` — Yash (P3)
  * ``quality.score`` — Sanja (P2, recomputed per §5.0) → surfaced as
    data-completeness proxy
  * ``anomalies[*].severity`` — Yash (P3)
  * §5.5 report files — Sanja / Yash / Anastasia

No per-charger uptime, lag, or quality math is ever recomputed here.

Streaming (§2.3): ``Kpis`` is built by iterating the golden records ONCE and
retains only aggregates plus a tiny per-charger light record for
chronic-failure sites (id, city, count, health state) — never the full
records. Views consume record iterables and render in a single pass each, so
the reporting stage keeps memory flat on the §8 scale run.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .models import GoldenCharger, GoldenMaintenance, GoldenRecord, GoldenSession

ACTIVE_STATUS = "ACTIVE"
CHRONIC_FAILURE_THRESHOLD = 3  # fault_recurrence_count >= 3 → chronic-failure site
SEVERITY_ORDER = ["SAFETY", "CRITICAL", "WARN", "INFO"]
HEALTH_ORDER = ["SAFETY_REVIEW", "SUSPECT_OUTAGE", "DEGRADED", "HEALTHY"]


class Kpis:
    """Hand-verifiable fleet KPIs aggregated from golden records (one pass)."""

    def __init__(self, records: Iterable[GoldenRecord]):
        self.records_in = 0
        self.chargers_deployed = 0
        self.active_chargers = 0
        self.sessions_count = 0
        self.sessions_missing_energy = 0
        self.energy_delivered_kwh = 0.0
        self.maintenance_events = 0

        self._uptime_values: List[float] = []
        self._score_values: List[float] = []
        self.chargers_with_metrics = 0
        self.max_fault_recurrence = 0
        self.chronic_failure_sites: List[Dict] = []  # light per-site records

        self.health_states: Dict[str, int] = {}
        self.anomaly_severity: Dict[str, int] = {}
        self.anomaly_types: Dict[str, int] = {}
        self.anomalies_total = 0
        self.maintenance_by_severity: Dict[str, int] = {}

        for r in records:
            self.records_in += 1
            for a in r.anomalies:
                self.anomalies_total += 1
                sev = a.severity or "UNKNOWN"
                typ = a.type or "unknown"
                self.anomaly_severity[sev] = self.anomaly_severity.get(sev, 0) + 1
                self.anomaly_types[typ] = self.anomaly_types.get(typ, 0) + 1

            if isinstance(r, GoldenCharger):
                self.chargers_deployed += 1
                if (r.status or "").upper() == ACTIVE_STATUS:
                    self.active_chargers += 1
                if r.metrics:
                    self.chargers_with_metrics += 1
                    self._uptime_values.append(r.metrics.est_uptime_pct)
                    rec = r.metrics.fault_recurrence_count
                    self.max_fault_recurrence = max(self.max_fault_recurrence, rec)
                    if rec >= CHRONIC_FAILURE_THRESHOLD:
                        self.chronic_failure_sites.append(
                            {
                                "charger_id": r.charger_id or r.golden_id or "?",
                                "city": (r.address.city if r.address else None) or "—",
                                "fault_recurrence_count": rec,
                                "health_state": (r.health.state if r.health else None) or "—",
                            }
                        )
                state = (r.health.state if r.health else None) or "UNKNOWN"
                self.health_states[state] = self.health_states.get(state, 0) + 1
                if r.quality and r.quality.score is not None:
                    self._score_values.append(r.quality.score)

            elif isinstance(r, GoldenSession):
                self.sessions_count += 1
                if r.energy_kwh is None:
                    self.sessions_missing_energy += 1
                else:
                    self.energy_delivered_kwh += r.energy_kwh
                if r.quality and r.quality.score is not None:
                    self._score_values.append(r.quality.score)

            elif isinstance(r, GoldenMaintenance):
                self.maintenance_events += 1
                sev = r.severity or "UNKNOWN"
                self.maintenance_by_severity[sev] = (
                    self.maintenance_by_severity.get(sev, 0) + 1
                )

    # ------------------------------------------------------------------
    @property
    def inactive_chargers(self) -> int:
        return self.chargers_deployed - self.active_chargers

    @property
    def est_uptime_pct(self) -> Optional[float]:
        """Fleet mean of Yash's per-charger est_uptime_pct (fraction, §5.4)."""
        values = [v for v in self._uptime_values if v is not None]
        if not values:
            return None
        return sum(values) / len(values)

    @property
    def data_completeness_pct(self) -> Optional[float]:
        """Mean of Sanja's final quality.score over charger+session records."""
        values = [v for v in self._score_values if v is not None]
        if not values:
            return None
        return sum(values) / len(values)

    # ------------------------------------------------------------------
    def as_dict(self) -> Dict[str, object]:
        """Flat dict of KPIs — used for CSV rows and hand-verifiable tests.

        Values keep full precision; display rounding happens in the render
        layer (fmt_number / fmt_pct), never here.
        """
        return {
            "report_date": None,  # filled by caller when known
            "chargers_deployed": self.chargers_deployed,
            "active_chargers": self.active_chargers,
            "inactive_chargers": self.inactive_chargers,
            "sessions_count": self.sessions_count,
            "sessions_missing_energy": self.sessions_missing_energy,
            "energy_delivered_kwh": round(self.energy_delivered_kwh, 2),
            "est_uptime_pct": self.est_uptime_pct,
            "data_completeness_pct": self.data_completeness_pct,
            "chargers_with_metrics": self.chargers_with_metrics,
            "max_fault_recurrence": self.max_fault_recurrence,
            "chronic_failure_sites": len(self.chronic_failure_sites),
            "anomalies_total": self.anomalies_total,
            "maintenance_events": self.maintenance_events,
        }
