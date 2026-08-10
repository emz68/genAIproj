"""validation_report.json (S6) — src/validation/.

Shape is exactly per PDM §5.5 (minimal keys; nothing added):

    {
      "records_in": n, "records_out": n, "quarantined": n,
      "per_source": {"<source>": {"records": n, "avg_score": f,
                                   "issues": {"<code>": n}}},
      "per_issue": {"<code>": n}
    }
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def build_report(
    records_in: int,
    records_out: int,
    quarantined: int,
    per_source: dict[str, dict[str, Any]],
    per_issue: Counter,
) -> dict[str, Any]:
    """Assemble the §5.5 report dict.

    ``per_source`` entries are ``{"records": n, "score_sum": f,
    "issues": Counter}`` (accumulated while streaming); they are converted
    to the final shape here.
    """
    per_source_out: dict[str, dict[str, Any]] = {}
    for source, stats in per_source.items():
        count = int(stats["records"])
        avg_score = round(float(stats["score_sum"]) / count, 4) if count else 0.0
        per_source_out[source] = {
            "records": count,
            "avg_score": avg_score,
            "issues": dict(stats["issues"]),
        }
    return {
        "records_in": int(records_in),
        "records_out": int(records_out),
        "quarantined": int(quarantined),
        "per_source": per_source_out,
        "per_issue": dict(per_issue),
    }
