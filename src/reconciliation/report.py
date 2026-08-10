"""Y5 — reconciliation_report.json (§5.5).

Shape (frozen):
``{"records_in": n, "golden_records_out": n, "duplicates_removed": n,
   "clusters": n, "conflicts_resolved": n, "anomalies": {"<type>": n},
   "per_source_lag_days": {"<source>": {"p50": f, "p95": f}}}``
"""

from __future__ import annotations

from typing import Dict

from .anomalies import per_source_lag_days


def build_report(
    records_in: int,
    rr,
    anomaly_counts: Dict[str, int],
    records_out: int,
) -> Dict:
    return {
        "records_in": records_in,
        "golden_records_out": records_out,
        "duplicates_removed": rr.duplicates_removed,
        "clusters": rr.clusters,
        "conflicts_resolved": rr.conflicts_resolved,
        "anomalies": dict(sorted(anomaly_counts.items())),
        "per_source_lag_days": per_source_lag_days(rr),
    }
