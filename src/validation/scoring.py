"""Quality scoring (S6) — src/validation/.

PDM §5.0 lifecycle: ingestion writes an initial ``quality.score`` = parsing /
extraction confidence; validation moves that value to
``quality.extraction_confidence`` and recomputes ``score`` as the
issue-severity-weighted final value in [0.0, 1.0].

    score = extraction_confidence − Σ(weights[issue.severity])  per unique code

clamped to [0.0, 1.0].  Weights, the default weight for unknown severities,
and the quarantine threshold live in ``rules.yaml`` (rules as data).
"""

from __future__ import annotations

import math
from typing import Any

from .rules import Issue


def compute_score(
    extraction_confidence: float | None,
    issues: list[Issue],
    scoring_config: dict[str, Any],
) -> float:
    """Issue-severity-weighted final score (PDM §5.0)."""
    base = extraction_confidence if extraction_confidence is not None else 1.0
    weights = scoring_config.get("weights", {})
    default_weight = float(scoring_config.get("default_weight", 0.1))
    penalized_codes: set[str] = set()
    penalty = 0.0
    for issue in issues:
        if issue.code in penalized_codes:
            continue  # one penalty per unique issue code per record
        penalized_codes.add(issue.code)
        penalty += float(weights.get(issue.severity, default_weight))
    score = max(0.0, min(1.0, base - penalty))
    return round(score, int(scoring_config.get("round_digits", 4)))


def is_quarantined(score: float, scoring_config: dict[str, Any]) -> bool:
    """Unfixable / low-quality records are quarantined in the report but
    still emitted with a low score (PDM S3: never drop a record)."""
    return score < float(scoring_config.get("quarantine_below", 0.4))


def extraction_confidence_from(record_quality: Any) -> float | None:
    """P1's ``quality.score`` becomes P2's ``extraction_confidence``."""
    if not isinstance(record_quality, dict):
        return None
    value = record_quality.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None
