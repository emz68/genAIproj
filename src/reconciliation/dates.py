"""Date parsing and percentile helpers (deterministic)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Optional


def parse_iso(ts) -> Optional[datetime]:
    """Parse ISO-8601 with optional 'Z' suffix; returns tz-aware UTC."""
    if not ts:
        return None
    try:
        s = str(ts)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def iso_date(ts) -> Optional[str]:
    dt = parse_iso(ts)
    return dt.date().isoformat() if dt else None


def lag_days(ingested_at, event_ts) -> Optional[float]:
    """(ingested_at − event_ts) in days, clamped at 0 (clock skew → 0)."""
    a, b = parse_iso(ingested_at), parse_iso(event_ts)
    if a is None or b is None:
        return None
    return max(0.0, (a - b).total_seconds() / 86400.0)


def percentile_sorted(sorted_vals: List[float], p: float) -> Optional[float]:
    """Nearest-rank percentile over an ascending list. p in [0, 100]."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = max(1, min(len(sorted_vals), round(p / 100.0 * len(sorted_vals))))
    return sorted_vals[rank - 1]


def p50_p95(values: Iterable[Optional[float]]) -> tuple:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return (None, None)
    return (percentile_sorted(vals, 50), percentile_sorted(vals, 95))


def robust_outlier_bounds(values: List[float], iqr_mult: float = 3.0) -> Optional[tuple]:
    """(lower, upper) fence via IQR; None when < 5 values (too few to judge)."""
    if len(values) < 5:
        return None
    s = sorted(values)
    n = len(s)
    q1 = s[n // 4]
    q3 = s[(3 * n) // 4]
    iqr = q3 - q1
    if iqr <= 0:
        # Degenerate spread: fall back to MAD-based robust z.
        med = s[n // 2]
        mad = sorted(abs(v - med) for v in s)[n // 2]
        scale = 1.4826 * mad
        if scale <= 0:
            return None
        return (None, med + 5.0 * scale)  # robust z > 5 → outlier
    return (q1 - iqr_mult * iqr, q3 + iqr_mult * iqr)
