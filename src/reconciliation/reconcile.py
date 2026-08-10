"""Y2 — Conflict resolution → golden records (§5.4).

Builds the single source of truth from resolved clusters:

- **Survivorship.** For each cluster, a primary member is chosen by
  (source_rank, ingested_at, quality.score, record order) — registry sources
  outrank municipal data outrank contractor reports; fresher and higher-scored
  records win ties. Field-level conflicts are resolved per field by the same
  ranking over the members that carry a non-null value for that field; every
  decision is logged in ``conflicts_resolved`` with ``chosen``/``rejected``/
  ``rule``.

- **Deterministic golden_id.** ``sha256(entity_key)`` truncated to 20 hex
  chars, prefixed ``chg-``/``ses-``/``mnt-``. Entity keys are *stable
  identities*, not input positions:
    - charger: ``station_id`` when the cluster has one, else
      ``name|city|state|lat|lon`` (rounded to 4 dp) of the primary member;
    - session: ``session_id`` when present, else
      ``<charger_golden_id>|<start>|<energy>`` (unresolved sessions fall back
      to ``start|end|energy|station_name``);
    - maintenance: ``event_id``.
  Re-running over a superset of inputs therefore keeps the same golden_id for
  the same entity and never duplicates it (Y2 re-run semantics).

- **merged_from** lists the unique ``(source, raw_ref)`` of every cluster
  member; extra fields of the primary member are preserved (extra="allow").

Maintenance events pass through deduped (§5.4): the survivor record gains the
golden_id of its resolved charger (or its own event key when unresolved).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .matching import source_rank

CHARGER_FIELDS = [
    "network",
    "address.street", "address.city", "address.state", "address.zip",
    "lat", "lon",
    "connector_type", "power_kw", "level", "status", "install_date",
]
SESSION_FIELDS = [
    "start_time", "end_time", "energy_kwh", "peak_kw", "duration_min", "fault_code",
]


def _get(rec: dict, path: str):
    cur = rec
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _cmp(v) -> Tuple:
    """Comparison-normalized value for distinctness checks."""
    if isinstance(v, float):
        return ("f", round(v, 4))
    if isinstance(v, (int, bool)):
        return ("f", float(v))
    if isinstance(v, str):
        return ("s", v.strip().lower())
    return ("o", repr(v))


def _member_rank(rec: dict, order: int) -> Tuple:
    prov = rec.get("provenance") or {}
    q = rec.get("quality") or {}
    return (
        source_rank(prov.get("source")),
        str(prov.get("ingested_at") or ""),
        float(q.get("score") or 0.0),
        order,
    )


def _pick_primary(members: List[dict]) -> Tuple[dict, int]:
    """Primary member by (source_rank, ingested_at, quality.score, order)."""
    best, best_key = members[0], _member_rank(members[0], 0)
    for order, m in enumerate(members[1:], start=1):
        k = _member_rank(m, order)
        if k > best_key:
            best, best_key = m, k
    return best, members.index(best)


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def charger_entity_key(members: List[dict]) -> str:
    """Entity key for a golden charger (a physical *port*). Prefer
    charger_id (port-level, unique); fall back to station_id (single-port
    stations); last resort name+geo of the primary member."""
    cids = {str(m.get("charger_id")).strip().lower() for m in members if m.get("charger_id")}
    if len(cids) == 1:
        return "cid:" + next(iter(cids))
    sids = {str(m.get("station_id")).strip().lower() for m in members if m.get("station_id")}
    if len(sids) == 1:
        return "sid:" + next(iter(sids))
    primary, _ = _pick_primary(members)
    addr = primary.get("address") or {}
    lat = primary.get("lat")
    lon = primary.get("lon")
    geo = f"{lat:.4f},{lon:.4f}" if lat is not None and lon is not None else "no-geo"
    name = str(primary.get("station_name") or primary.get("name") or "").strip().lower()
    return f"nm:{name}|{addr.get('city') or ''}|{addr.get('state') or ''}|{geo}"


def session_entity_key(sess: dict, charger_gid: Optional[str]) -> str:
    sid = sess.get("session_id")
    if sid:
        return "sid:" + str(sid).strip().lower()
    if charger_gid:
        return f"{charger_gid}|{sess.get('start_time')}|{sess.get('energy_kwh')}"
    name = str(sess.get("station_name") or sess.get("name") or "").strip().lower()
    return f"{name}|{sess.get('start_time')}|{sess.get('end_time')}|{sess.get('energy_kwh')}"


def resolve_conflicts(members: List[dict], fields: List[str]) -> Tuple[dict, List[dict]]:
    """Field-level survivorship. Returns (fused_record, conflicts_resolved)."""
    primary, _ = _pick_primary(members)
    fused = dict(primary)  # start from primary; extras preserved
    conflicts: List[dict] = []
    for path in fields:
        present = [(m, _get(m, path)) for m in members]
        present = [(m, v) for m, v in present if v is not None]
        if len(present) < 2:
            continue
        distinct: Dict[Tuple, Tuple[dict, object]] = {}
        for m, v in present:
            distinct.setdefault(_cmp(v), (m, v))
        if len(distinct) == 1:
            continue
        # Winner per field = max member rank among carriers.
        winner_m, winner_v = max(
            ((m, v) for m, v in present),
            key=lambda mv: _member_rank(mv[0], present.index(mv)),
        )
        rejected = [v for k, (m, v) in distinct.items() if m is not winner_m]
        # Write chosen value into fused record at the field path.
        parts = path.split(".")
        cur = fused
        for p in parts[:-1]:
            nxt = cur.get(p)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[p] = nxt
            cur = nxt
        cur[parts[-1]] = winner_v
        conflicts.append(
            {
                "field": path,
                "chosen": winner_v,
                "rejected": rejected,
                "rule": "survivorship",
            }
        )
    return fused, conflicts


def merged_from(members: List[dict]) -> List[dict]:
    seen = set()
    out = []
    for m in members:
        prov = m.get("provenance") or {}
        pair = (prov.get("source"), prov.get("raw_ref"))
        if pair in seen:
            continue
        seen.add(pair)
        out.append({"source": prov.get("source"), "raw_ref": prov.get("raw_ref")})
    return out


@dataclass
class ReconcileResult:
    golden_chargers: List[dict] = field(default_factory=list)
    golden_sessions: List[dict] = field(default_factory=list)
    golden_events: List[dict] = field(default_factory=list)
    charger_by_cluster: Dict[int, dict] = field(default_factory=dict)
    sessions_by_cluster: Dict[int, List[dict]] = field(default_factory=dict)
    events_by_cluster: Dict[int, List[dict]] = field(default_factory=dict)
    golden_by_session_idx: Dict[int, dict] = field(default_factory=dict)
    golden_by_event_idx: Dict[int, dict] = field(default_factory=dict)
    duplicates_removed: int = 0
    conflicts_resolved: int = 0
    clusters: int = 0


def build_golden(res) -> ReconcileResult:
    """Assemble golden records from a Resolution (Y1 output)."""
    out = ReconcileResult()
    out.clusters = len(res.charger_clusters)

    # --- Chargers ----------------------------------------------------------
    for ci, cluster in enumerate(res.charger_clusters):
        members = [res.chargers[i] for i in cluster]
        fused, conflicts = resolve_conflicts(members, CHARGER_FIELDS)
        gid = "chg-" + _hash_key(charger_entity_key(members))
        golden = dict(fused)
        golden.update(
            {
                "golden_id": gid,
                "merged_from": merged_from(members),
                "conflicts_resolved": conflicts,
                "anomalies": [],
                "health": {"state": "HEALTHY", "since": None, "evidence": []},
                "metrics": {
                    "est_uptime_pct": None,
                    "fault_recurrence_count": 0,
                    "reporting_lag_p50_days": None,
                    "reporting_lag_p95_days": None,
                },
            }
        )
        out.golden_chargers.append(golden)
        out.charger_by_cluster[ci] = golden
        out.conflicts_resolved += len(conflicts)
        out.duplicates_removed += len(cluster) - 1  # merged-away members

    # --- Sessions ----------------------------------------------------------
    for group in res.session_groups:
        survivor_idx = group[-1]
        members = [res.sessions[i] for i in group]
        dups = group[:-1]
        survivor = res.sessions[survivor_idx]
        cluster_idx = res.session_charger.get(survivor_idx, -1)
        charger_gid = None
        if cluster_idx >= 0:
            charger_gid = out.charger_by_cluster[cluster_idx]["golden_id"]
        fused, conflicts = resolve_conflicts(members, SESSION_FIELDS)
        gid = "ses-" + _hash_key(session_entity_key(survivor, charger_gid))
        golden = dict(fused)
        golden.update(
            {
                "golden_id": gid,
                "merged_from": merged_from(members),
                "conflicts_resolved": conflicts,
                "anomalies": [],
            }
        )
        # Enrich with resolved charger identity when we know it.
        if charger_gid:
            golden.setdefault("charger_golden_id", charger_gid)
            if not golden.get("station_id") and out.charger_by_cluster[cluster_idx].get("station_id"):
                golden["station_id"] = out.charger_by_cluster[cluster_idx]["station_id"]
        out.golden_sessions.append(golden)
        out.golden_by_session_idx[survivor_idx] = golden
        out.sessions_by_cluster.setdefault(cluster_idx, []).append(golden)
        out.conflicts_resolved += len(conflicts)
        out.duplicates_removed += len(dups)
        for dup_idx in dups:
            out.golden_by_session_idx[dup_idx] = golden  # duplicates map to same golden

    # --- Maintenance -------------------------------------------------------
    for group in res.event_groups:
        survivor_idx = group[-1]
        members = [res.events[i] for i in group]
        dups = group[:-1]
        cluster_idx = res.event_charger.get(survivor_idx, -1)
        charger_gid = None
        if cluster_idx >= 0:
            charger_gid = out.charger_by_cluster[cluster_idx]["golden_id"]
        gid = "mnt-" + _hash_key(str(members[0].get("event_id") or "evt"))
        golden = dict(res.events[survivor_idx])
        golden.update({"golden_id": gid})
        if charger_gid:
            golden.setdefault("charger_golden_id", charger_gid)
        out.golden_events.append(golden)
        out.golden_by_event_idx[survivor_idx] = golden
        out.events_by_cluster.setdefault(cluster_idx, []).append(golden)
        out.duplicates_removed += len(dups)
    return out
