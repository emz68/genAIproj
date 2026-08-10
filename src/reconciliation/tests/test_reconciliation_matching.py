"""Matching precision/recall tests (Y5).

Known truth is the planted fixture (src/reconciliation/fixtures/): four
stations, five charger records (two of which describe the same physical port
B1), and one unknown-station session/event that must stay unresolved.

We assert:
- recall: every pair of records that should share a cluster does;
- precision: no pair that should be in different clusters is merged;
- session/event association matches the planted station mapping.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.reconciliation.resolve import resolve, station_ref

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Planted truth: charger_id -> expected cluster members (charger_ids)
EXPECTED_CHARGER_CLUSTERS = {
    "ST-1001#1#J1772#1": {"ST-1001#1#J1772#1"},
    "ST-1001#2#CCS#1": {"ST-1001#2#CCS#1"},
    "ST-2001#1#J1772#1": {"ST-2001#1#J1772#1"},  # both B1 records merge here
    "ST-3001#1#J1772#1": {"ST-3001#1#J1772#1"},
}

# session_id prefix -> expected charger_id ('' = unresolved)
EXPECTED_SESSION_CHARGER = {
    "cary-a1-": "ST-1001#1#J1772#1",
    "cary-a2-": "ST-1001#2#CCS#1",
    "cary-b1-": "ST-2001#1#J1772#1",
    "cary-c1-": "ST-3001#1#J1772#1",
    "cary-x-": None,
}

EXPECTED_EVENT_CHARGER = {
    "evt-b1": "ST-2001#1#J1772#1",
    "evt-c1": "ST-3001#1#J1772#1",
    "evt-c2": "ST-3001#1#J1772#1",
    "evt-x1": None,
}


def _load():
    chargers, sessions, events = [], [], []
    with open(FIXTURES / "validated.jsonl") as f:
        for line in f:
            rec = json.loads(line)
            rt = rec["record_type"]
            (chargers if rt == "charger" else sessions if rt == "session" else events).append(rec)
    return chargers, sessions, events


def test_charger_clustering_recall_and_precision():
    chargers, _, _ = _load()
    res = resolve(chargers, [], [])
    # Map each charger index to its cluster root set.
    cluster_of = {}
    for ci, cluster in enumerate(res.charger_clusters):
        for idx in cluster:
            cluster_of[idx] = ci
    clusters_by_id = {}
    for idx, c in enumerate(chargers):
        clusters_by_id.setdefault(c["charger_id"], set()).add(cluster_of[idx])

    for charger_id, expected_members in EXPECTED_CHARGER_CLUSTERS.items():
        got = clusters_by_id.get(charger_id, set())
        assert len(got) == 1, f"{charger_id} should be in exactly one cluster"
        # All expected members share the same cluster (recall).
        for other_id in expected_members:
            assert got == clusters_by_id[other_id], f"{charger_id} vs {other_id} split"
        # No foreign record shares it (precision).
        for other_id, other_cluster in clusters_by_id.items():
            if other_id in expected_members:
                continue
            assert not (got & other_cluster), f"{charger_id} wrongly merged with {other_id}"


def test_charger_count():
    chargers, _, _ = _load()
    res = resolve(chargers, [], [])
    assert len(res.charger_clusters) == 4  # 5 records → 4 physical ports


def test_session_association_precision_recall():
    chargers, sessions, _ = _load()
    res = resolve(chargers, sessions, [])
    charger_of = {}
    for ci, cluster in enumerate(res.charger_clusters):
        for idx in cluster:
            charger_of[chargers[idx]["charger_id"]] = ci

    for s in sessions:
        sid = s["session_id"]
        prefix = next(p for p in EXPECTED_SESSION_CHARGER if sid.startswith(p))
        expected_cid = EXPECTED_SESSION_CHARGER[prefix]
        got_cluster = res.session_charger.get(sessions.index(s))
        if expected_cid is None:
            assert got_cluster is None, f"{sid} should be unresolved, got cluster {got_cluster}"
        else:
            assert got_cluster == charger_of[expected_cid], (
                f"{sid} associated to wrong charger"
            )


def test_event_association():
    chargers, _, events = _load()
    res = resolve(chargers, [], events)
    charger_of = {}
    for ci, cluster in enumerate(res.charger_clusters):
        for idx in cluster:
            charger_of[chargers[idx]["charger_id"]] = ci
    for e in events:
        eid = e["event_id"]
        expected_cid = EXPECTED_EVENT_CHARGER.get(eid)
        got_cluster = res.event_charger.get(events.index(e))
        if expected_cid is None:
            assert got_cluster is None, f"{eid} should be unresolved"
        else:
            assert got_cluster == charger_of[expected_cid], f"{eid} wrong cluster"


def test_session_dedup_groups():
    """Planted duplicates: a1-003 (exact) and a1-201/a1-202 (fuzzy)."""
    chargers, sessions, _ = _load()
    res = resolve(chargers, sessions, [])
    groups = res.session_groups
    by_survivor = {}
    for g in groups:
        by_survivor[sessions[g[-1]]["session_id"]] = [sessions[i]["session_id"] for i in g[:-1]]
    # exact dup: a1-003 absorbed
    assert "cary-a1-003" in by_survivor and "cary-a1-003" in by_survivor["cary-a1-003"]
    # fuzzy dup: a1-202 absorbed by a1-201
    assert by_survivor.get("cary-a1-201") == ["cary-a1-202"]
    # no cross-station merging
    total_groups = len(groups)
    assert total_groups == 21  # 23 sessions − 2 deduped
