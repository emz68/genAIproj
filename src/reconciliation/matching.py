"""Normalization, string similarity, geo helpers, and source priority.

All matching helpers are deterministic (fixed thresholds, no randomness), so
reconciliation output is byte-stable across re-runs given identical input.
"""

from __future__ import annotations

import difflib
import math
import re

_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")


def norm(s) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if s is None:
        return ""
    t = _TOKEN_RE.sub(" ", str(s).lower()).strip()
    return _WS_RE.sub(" ", t)


def name_similarity(a, b) -> float:
    """Max of character-sequence ratio and token-overlap ratio.

    Handles both near-identical names ("Town Hall-PWH" vs "Town Hall PWH")
    and lightly reordered ones ("Cary Town Hall Parking" vs
    "TOWN OF CARY / TOWN HALL").
    """
    na, nb = norm(a), norm(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    tok = (2.0 * len(ta & tb)) / (len(ta) + len(tb)) if (ta or tb) else 0.0
    return max(seq, tok)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km (WGS84 sphere, R = 6371.0088 km)."""
    if None in (lat1, lon1, lat2, lon2):
        return float("inf")
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dl / 2) ** 2
    return 2.0 * 6371.0088 * math.asin(math.sqrt(a))


def _contains_any(haystack, needles) -> bool:
    h = str(haystack).lower()
    return any(n in h for n in needles)


def source_rank(source) -> int:
    """Authority ranking for conflict survivorship. Lower = more trusted.

    - 0: registry mirrors (AFDC / BTS / NREL / DOT) — authoritative for
      static charger facts
    - 1: municipal open data (Town of Cary sessions)
    - 2: contractor / unstructured reports (synthetic digests, emails)
    - 3: unknown sources
    """
    if not source:
        return 3
    if _contains_any(source, ("afdc", "bts", "nrel", "dot", "alternative fuels")):
        return 0
    if _contains_any(source, ("cary", "town of cary", "municipal", "open data")):
        return 1
    if _contains_any(source, ("contractor", "report", "synthetic", "email", "digest")):
        return 2
    return 3
