"""Y1 — Entity resolution: cluster charger records into physical entities,
associate sessions and maintenance events to those entities, and dedup
sessions and events. P3 is the *sole owner of all dedup* (§7 boundary rules).

Design (all deterministic — fixed thresholds, stable tie-breaks):

- **Charger clustering** (per *port* — P1 emits one record per physical
  port, so two ports of one station are distinct entities). Blocking keys:
  exact ``charger_id``/``station_id``, normalized ``city|zip``, rounded geo
  cell (lat/lon 1 decimal ≈ 10 km, recall-friendly), and first-3-tokens of
  the normalized station name. Within each block, records are compared
  pairwise with a match predicate (below) and merged via union-find.
  Cross-block matches are impossible by construction except via ids.

- **Match predicate** (two records describe the same physical port when):
  1. both carry ``charger_id`` — equal ⇒ same port, different ⇒ different
     ports (never merged, even at the same station); or
  2. same non-null ``station_id`` (single-port stations; ports with
     charger_id are already discriminated by rule 1); or
  3. geo distance ≤ ``GEO_MATCH_KM`` AND (name similarity ≥ ``NAME_SIM_MATCH``
     OR street+zip+city all equal); or
  4. name similarity ≥ ``NAME_SIM_STRONG`` AND same city; or
  5. street+zip+city all equal (non-empty).

- **Session → charger association.** Sessions carry their station reference
  as standard fields (``station_id``/``charger_id``) and/or extra fields that
  P1 preserves verbatim (``station_name``, ``address_1``, ``city``,
  ``zip_postal_code`` — see src/ingestion README). We resolve via exact ids
  first, then name similarity ≥ ``NAME_SIM_MATCH``, then address equality,
  then geo proximity (name sim ≥ 0.7 within 1 km). Best-scoring candidate
  wins; sessions matching nothing stay unresolved (own golden record).

- **Session dedup** (within one charger cluster, or unresolved group):
  exact ``session_id`` match, or fuzzy: |start diff| ≤ ``DUP_START_MIN`` min
  AND |energy diff| ≤ max(``DUP_ENERGY_ABS``, rel% of larger) AND |duration
  diff| ≤ ``DUP_DURATION_REL`` (both null durations treated as equal). The
  survivor maximizes (source_rank, quality.score, ingested_at, session_id).

- **Event dedup:** same ``event_id``, or same (charger cluster, event_date,
  event_type, severity, normalized description).

Memory note (§2.3): records are held as compact dicts — clustering is
inherently cross-record, so this stage is O(records); I/O is line-by-line
both directions and no whole-file blob is ever materialized.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .matching import haversine_km, name_similarity, norm, source_rank

# Matching thresholds (deterministic — documented in module README)
GEO_MATCH_KM = 1.0
NAME_SIM_MATCH = 0.85
NAME_SIM_STRONG = 0.92
GEO_WITH_NAME_SIM = 0.70
DUP_START_MIN = 15.0
DUP_ENERGY_ABS = 0.5
DUP_ENERGY_REL = 0.10
DUP_DURATION_REL = 0.15


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[rj] = ri


# ---------------------------------------------------------------------------
# Station references
# ---------------------------------------------------------------------------

_ADDR_EXTRA_KEYS = {
    "street": ("street", "address_1", "address", "street_address"),
    "city": ("city",),
    "zip": ("zip", "zip_postal_code", "postal_code"),
    "state": ("state", "state_province"),
}
_NAME_EXTRA_KEYS = ("station_name", "station", "name", "location_name")


def station_ref(rec: dict) -> dict:
    """Extract a normalized station reference from any record, reading both
    the canonical fields (§5) and the extra fields P1 preserves verbatim."""
    addr = rec.get("address") or {}
    ref = {
        "charger_id": rec.get("charger_id"),
        "station_id": rec.get("station_id"),
        "name": rec.get("station_name") or rec.get("name") or rec.get("station"),
        "street": addr.get("street") or rec.get("address_1") or rec.get("street"),
        "city": addr.get("city") or rec.get("city"),
        "zip": addr.get("zip") or rec.get("zip_postal_code") or rec.get("postal_code"),
        "state": addr.get("state") or rec.get("state") or rec.get("state_province"),
        "lat": rec.get("lat"),
        "lon": rec.get("lon"),
    }
    for key, cands in _ADDR_EXTRA_KEYS.items():
        if ref[key]:
            continue
        for c in cands:
            if rec.get(c):
                ref[key] = rec[c]
                break
    for c in _NAME_EXTRA_KEYS:
        if rec.get(c):
            ref["name"] = rec[c]
            break
    ref["name_norm"] = norm(ref["name"])
    ref["street_norm"] = norm(ref["street"])
    ref["city_norm"] = norm(ref["city"])
    ref["zip_norm"] = norm(ref["zip"])
    return ref


def blocking_keys(ref: dict) -> List[str]:
    keys = []
    if ref["charger_id"]:
        keys.append("chg:" + str(ref["charger_id"]).strip().lower())
    if ref["station_id"]:
        keys.append("sid:" + str(ref["station_id"]).strip().lower())
    if ref["city_norm"] and ref["zip_norm"]:
        keys.append(f"cityzip:{ref['city_norm']}|{ref['zip_norm']}")
    if ref["lat"] is not None and ref["lon"] is not None:
        keys.append(f"geo:{ref['lat']:.1f},{ref['lon']:.1f}")
    if ref["name_norm"]:
        tokens = ref["name_norm"].split()
        if tokens:
            keys.append("name3:" + " ".join(tokens[:3]))
    return keys


def same_entity(a: dict, b: dict) -> bool:
    """Match predicate for charger records (see module docstring)."""
    a_cid, b_cid = a.get("charger_id"), b.get("charger_id")
    if a_cid and b_cid:
        return str(a_cid).strip().lower() == str(b_cid).strip().lower()
    a_id, b_id = a.get("station_id"), b.get("station_id")
    if a_id and b_id and str(a_id).strip().lower() == str(b_id).strip().lower():
        return True
    dist = haversine_km(a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon"))
    geo_ok = dist <= GEO_MATCH_KM
    sim = name_similarity(a.get("name"), b.get("name"))
    addr_ok = bool(
        a.get("street_norm")
        and a["street_norm"] == b.get("street_norm")
        and a.get("city_norm")
        and a["city_norm"] == b.get("city_norm")
        and a.get("zip_norm")
        and a["zip_norm"] == b.get("zip_norm")
    )
    if geo_ok and (sim >= NAME_SIM_MATCH or addr_ok):
        return True
    if sim >= NAME_SIM_STRONG and a.get("city_norm") and a["city_norm"] == b.get("city_norm"):
        return True
    if addr_ok:
        return True
    return False


def _session_charger_score(sess_ref: dict, chg_ref: dict) -> Tuple[float, ...]:
    """Scoring tuple for session→charger association. Higher = better match.
    Returns (match_class, name_sim) where match_class ranks evidence types."""
    if sess_ref["charger_id"] and chg_ref["charger_id"]:
        if str(sess_ref["charger_id"]).strip().lower() == str(chg_ref["charger_id"]).strip().lower():
            return (5.0, 1.0)
    if sess_ref["station_id"] and chg_ref["station_id"]:
        if str(sess_ref["station_id"]).strip().lower() == str(chg_ref["station_id"]).strip().lower():
            return (4.0, 1.0)
    sim = name_similarity(sess_ref["name"], chg_ref["name"])
    addr_ok = bool(
        sess_ref["street_norm"]
        and sess_ref["street_norm"] == chg_ref["street_norm"]
        and sess_ref["city_norm"]
        and sess_ref["city_norm"] == chg_ref["city_norm"]
        and sess_ref["zip_norm"]
        and sess_ref["zip_norm"] == chg_ref["zip_norm"]
    )
    if addr_ok and sim >= NAME_SIM_MATCH:
        return (3.0, sim)
    if addr_ok and sess_ref["zip_norm"] and chg_ref["zip_norm"]:
        return (3.0, sim)
    dist = haversine_km(
        sess_ref["lat"], sess_ref["lon"], chg_ref["lat"], chg_ref["lon"]
    )
    if dist <= GEO_MATCH_KM and sim >= GEO_WITH_NAME_SIM:
        return (2.0, sim)
    if sim >= NAME_SIM_MATCH:
        return (1.0, sim)
    return (0.0, sim)


@dataclass
class Resolution:
    """Result of entity resolution over one input set."""

    chargers: List[dict]
    sessions: List[dict]
    events: List[dict]
    charger_clusters: List[List[int]] = field(default_factory=list)  # idx lists
    session_charger: Dict[int, int] = field(default_factory=dict)  # sess idx -> cluster idx
    event_charger: Dict[int, int] = field(default_factory=dict)  # ev idx -> cluster idx
    session_groups: List[List[int]] = field(default_factory=list)  # dedup groups
    event_groups: List[List[int]] = field(default_factory=list)  # dedup groups


def _cluster_chargers(chargers: List[dict]) -> List[List[int]]:
    n = len(chargers)
    uf = UnionFind(n)
    refs = [station_ref(c) for c in chargers]
    blocks: Dict[str, List[int]] = defaultdict(list)
    for i, ref in enumerate(refs):
        for key in blocking_keys(ref):
            blocks[key].append(i)
    for members in blocks.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if same_entity(refs[a], refs[b]):
                    uf.union(a, b)
    clusters: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        clusters[uf.find(i)].append(i)
    # Deterministic order: by first member index
    ordered = sorted(clusters.values(), key=lambda cl: cl[0])
    return ordered


def _associate(refs: List[dict], charger_refs: List[dict], charger_clusters: List[List[int]]) -> Dict[int, int]:
    """Associate sessions/events (by ref) to the best-matching charger cluster.
    Unresolved items are omitted from the mapping."""
    # Index charger refs by blocking key for candidate lookup.
    index: Dict[str, List[int]] = defaultdict(list)  # key -> charger idx
    for ci, ref in enumerate(charger_refs):
        for key in blocking_keys(ref):
            index[key].append(ci)
    out: Dict[int, int] = {}
    cluster_of_charger: Dict[int, int] = {}
    for ci, cluster in enumerate(charger_clusters):
        for chg_idx in cluster:
            cluster_of_charger[chg_idx] = ci
    for si, ref in enumerate(refs):
        seen: set = set()
        candidates: List[int] = []
        for key in blocking_keys(ref):
            for ci in index.get(key, ()):
                if ci not in seen:
                    seen.add(ci)
                    candidates.append(ci)
        best = (0.0, 0.0, -1)  # (match_class, sim, charger_idx)
        for ci in candidates:
            score = _session_charger_score(ref, charger_refs[ci])
            if score > best[:2]:
                best = (score[0], score[1], ci)
        if best[0] >= 1.0:
            out[si] = cluster_of_charger[best[2]]
    return out


def _parse_ts(ts) -> Optional[float]:
    if not ts:
        return None
    try:
        from datetime import datetime

        s = str(ts)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _duration_min(rec: dict) -> Optional[float]:
    if rec.get("duration_min") is not None:
        return float(rec["duration_min"])
    start, end = _parse_ts(rec.get("start_time")), _parse_ts(rec.get("end_time"))
    if start is not None and end is not None and end >= start:
        return (end - start) / 60.0
    return None


def _session_duplicate(a: dict, b: dict) -> bool:
    """Exact (session_id) or fuzzy duplicate test for two sessions."""
    a_id, b_id = a.get("session_id"), b.get("session_id")
    if a_id and b_id and str(a_id).strip() == str(b_id).strip():
        return True
    ta, tb = _parse_ts(a.get("start_time")), _parse_ts(b.get("start_time"))
    if ta is None or tb is None or abs(ta - tb) > DUP_START_MIN * 60:
        return False
    ea, eb = a.get("energy_kwh"), b.get("energy_kwh")
    if ea is not None and eb is not None:
        if abs(ea - eb) > max(DUP_ENERGY_ABS, DUP_ENERGY_REL * max(ea, eb)):
            return False
    elif (ea is None) != (eb is None):
        return False
    da, db = _duration_min(a), _duration_min(b)
    if da is not None and db is not None and abs(da - db) > DUP_DURATION_REL * max(da, db, 1.0):
        return False
    return True


def _event_duplicate(a: dict, b: dict, same_charger: bool) -> bool:
    a_id, b_id = a.get("event_id"), b.get("event_id")
    if a_id and b_id and str(a_id).strip() == str(b_id).strip():
        return True
    if not same_charger:
        return False
    return (
        a.get("event_date") == b.get("event_date")
        and a.get("event_type") == b.get("event_type")
        and a.get("severity") == b.get("severity")
        and norm(a.get("description")) == norm(b.get("description"))
    )


def _survivor_index(group: List[int], records: List[dict], refs: List[dict]) -> int:
    """Deterministic survivor: max (source_rank, quality.score, ingested_at,
    then lexicographic on station name + id)."""
    def key(i):
        rec = records[i]
        q = rec.get("quality") or {}
        ref = refs[i]
        return (
            -source_rank((rec.get("provenance") or {}).get("source")),
            float(q.get("score") or 0.0),
            str((rec.get("provenance") or {}).get("ingested_at") or ""),
            str(ref.get("name") or ""),
            str(ref.get("station_id") or ""),
        )
    return max(group, key=key)


def resolve(chargers: List[dict], sessions: List[dict], events: List[dict]) -> Resolution:
    """Full Y1 pipeline. Inputs are raw record dicts (already parsed)."""
    res = Resolution(chargers=chargers, sessions=sessions, events=events)
    res.charger_clusters = _cluster_chargers(chargers)
    charger_refs = [station_ref(c) for c in chargers]

    # Associate sessions and events to charger clusters.
    sess_refs = [station_ref(s) for s in sessions]
    res.session_charger = _associate(sess_refs, charger_refs, res.charger_clusters)
    ev_refs = [station_ref(e) for e in events]
    res.event_charger = _associate(ev_refs, charger_refs, res.charger_clusters)

    # Group sessions for dedup: within charger cluster (or unresolved group
    # keyed by station name, so distinct unknown stations never cross-dedup).
    sess_groups: Dict[Tuple[int, str], List[int]] = defaultdict(list)
    for si in range(len(sessions)):
        key = (res.session_charger.get(si, -1), sess_refs[si]["name_norm"] or "?")
        sess_groups[key].append(si)
    for key, group in sess_groups.items():
        if len(group) < 2:
            res.session_groups.append([group[0]])
            continue
        comp = UnionFind(len(group))
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if _session_duplicate(sessions[a], sessions[b]):
                    comp.union(i, j)
        for root_members in _partition(comp, len(group)):
            actual = [group[i] for i in root_members]
            if not actual:
                continue
            survivor = _survivor_index(actual, sessions, sess_refs)
            res.session_groups.append([i for i in actual if i != survivor] + [survivor])
    # Deterministic group order: by survivor index.
    res.session_groups.sort(key=lambda g: g[-1])

    # Dedup events.
    ev_groups: Dict[Tuple[int, str], List[int]] = defaultdict(list)
    for ei in range(len(events)):
        key = (res.event_charger.get(ei, -1), "c")
        ev_groups[key].append(ei)
    for key, group in ev_groups.items():
        if len(group) < 2:
            res.event_groups.append([group[0]])
            continue
        comp = UnionFind(len(group))
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                same_chg = res.event_charger.get(a) == res.event_charger.get(b)
                if _event_duplicate(events[a], events[b], same_chg):
                    comp.union(i, j)
        for root_members in _partition(comp, len(group)):
            actual = [group[i] for i in root_members]
            if not actual:
                continue
            survivor = _survivor_index(actual, events, ev_refs)
            res.event_groups.append([i for i in actual if i != survivor] + [survivor])
    res.event_groups.sort(key=lambda g: g[-1])
    return res


def _partition(uf: UnionFind, n: int) -> List[List[int]]:
    parts: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        parts[uf.find(i)].append(i)
    return list(parts.values())
