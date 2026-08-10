"""Regression tests for the 2026-08-10 reconciliation audit findings.

Each test pins a wrongful merge/deletion class found in the NYC run:
driver-blind session dedup, distance-free strong-name clustering, and the
municipal-fleet linkage gaps. See docs/demo history for the audit record.
"""

from src.reconciliation.resolve import (
    _enrich_event_ref,
    _session_duplicate,
    blocking_keys,
    resolve,
    same_entity,
    station_ref,
)

_PROV = {"source": "nyc_dot", "source_file": "f.csv", "ingested_at": "2026-08-10T00:00:00+00:00"}


def _sess(**over):
    base = {
        "record_type": "session",
        "session_id": "s1",
        "driver_id": "driver-a",
        "charger_id": "BTCE0101:1",
        "station_id": "BTCE0101",
        "station_name": "QBO - Queens Borough Hall Municipal Parking Garage",
        "start_time": "2025-08-21T22:28:17-04:00",
        "energy_kwh": 19.952,
        "duration_min": 260.25,
        "provenance": _PROV,
        "quality": {"score": 1.0, "issues": [], "fixes_applied": []},
    }
    base.update(over)
    return base


# ---- session dedup: the QBO exhibit and its neighbors ----------------------

def test_different_drivers_are_never_duplicates():
    a = _sess()
    b = _sess(session_id="s2", driver_id="driver-b", charger_id="BTCE0103:1",
              start_time="2025-08-21T22:42:35-04:00", energy_kwh=21.651)
    # the audit's exhibit: two customers, two boxes, 14 min apart
    assert not _session_duplicate(a, b)


def test_different_charge_boxes_are_never_duplicates():
    a = _sess()
    b = _sess(session_id="s2", charger_id="BTCE0103:1")  # same driver, other box
    assert not _session_duplicate(a, b)


def test_same_driver_replug_is_not_a_duplicate():
    a = _sess(start_time="2025-08-21T23:15:00-04:00", energy_kwh=2.538, duration_min=30.0)
    b = _sess(session_id="s2", start_time="2025-08-21T23:22:00-04:00",
              energy_kwh=2.226, duration_min=25.0)
    assert not _session_duplicate(a, b)  # 7 min later, separately billed


def test_true_reexport_duplicate_is_caught():
    a = _sess()
    b = _sess(session_id="s2")  # identical fields, different session_id
    assert _session_duplicate(a, b)


def test_idless_exact_copy_still_caught_and_coincidence_spared():
    a = _sess(session_id="c1", driver_id=None, charger_id=None, station_id=None,
              station_name="TOWN OF CARY / TOWN HALL-PWH",
              start_time="2023-01-03T17:58:04+00:00", energy_kwh=3.976, duration_min=54.83)
    dup = dict(a, session_id="c2")  # injector-planted exact copy
    assert _session_duplicate(a, dup)
    near = dict(a, session_id="c3", start_time="2023-01-03T18:06:04+00:00", energy_kwh=3.7)
    assert not _session_duplicate(a, near)  # 8 min apart — a different car


def test_resolve_keeps_both_customers_sessions():
    a = _sess()
    b = _sess(session_id="s2", driver_id="driver-b", charger_id="BTCE0103:1",
              start_time="2025-08-21T22:42:35-04:00", energy_kwh=21.651)
    res = resolve([], [a, b], [])
    assert len(res.session_groups) == 2
    assert all(len(g) == 1 for g in res.session_groups)


# ---- charger clustering: strong names need co-location ---------------------

def _chg(name, lat=None, lon=None, street=None, city="New York", station_id=None):
    return station_ref({
        "record_type": "charger",
        "station_id": station_id,
        "station_name": name,
        "address": {"street": street, "city": city, "state": "NY", "zip": "10001"},
        "lat": lat, "lon": lon,
        "provenance": {"source": "afdc", "source_file": "f", "ingested_at": "t"},
        "quality": {},
    })


def test_same_brand_name_far_apart_not_merged():
    a = _chg("Icon Parking 235 W 48th St", lat=40.7601, lon=-73.9866)
    b = _chg("Icon Parking 235 E 88th St", lat=40.7796, lon=-73.9516)  # ~3.5 km
    assert not same_entity(a, b)


def test_same_name_same_site_merged():
    a = _chg("Nissan of Smithtown", lat=40.8551, lon=-73.2001, street="535 Middle Country Rd")
    b = _chg("Nissan of Smithtown", lat=40.8551, lon=-73.2001, street="535 Middle Country Rd")
    assert same_entity(a, b)


def test_strong_name_without_coords_needs_same_street():
    a = _chg("Verizon - Syracuse", street="4725 S Salina St", city="Syracuse")
    b = _chg("Verizon - Syracuse", street="6360 Thompson Rd", city="Syracuse")
    assert not same_entity(a, b)
    c = _chg("Verizon - Syracuse", street="4725 S Salina St", city="Syracuse")
    assert same_entity(a, c)


# ---- fleet linkage: alias blocking and box-id extraction -------------------

def test_prefixed_alias_shares_a_blocking_key():
    plain = station_ref(_sess(station_name="Court Square Municipal Parking Garage"))
    prefixed = station_ref(_sess(station_name="CSQ - Court Square Municipal Parking Garage",
                                 charger_id=None, station_id=None))
    assert set(blocking_keys(plain)) & set(blocking_keys(prefixed))


def test_event_box_id_lifted_from_free_text():
    event = {
        "record_type": "maintenance", "event_id": "e1",
        "description": "customer complaint via 311\ncharge box EVB-P2042308: screen dead",
        "extracted_fields": {}, "provenance": _PROV, "quality": {},
    }
    ref = _enrich_event_ref(station_ref(event), event)
    assert ref["station_id"] == "EVB-P2042308"
    event2 = dict(event, description="box 101088 overtemp @ 147F")
    assert _enrich_event_ref(station_ref(event2), event2)["station_id"] == "101088"


def test_event_links_to_municipal_box_charger():
    charger = {
        "record_type": "charger", "charger_id": "EVB-P2042308:1", "station_id": "EVB-P2042308",
        "network": "NYC DOT Municipal", "station_name": "JON - Jerome 190th Street Municipal Parking",
        "provenance": _PROV, "quality": {"score": 1.0},
    }
    event = {
        "record_type": "maintenance", "event_id": "e1",
        "description": "charge box EVB-P2042308: screen dead",
        "extracted_fields": {}, "provenance": _PROV, "quality": {},
    }
    res = resolve([charger], [], [event])
    assert res.event_charger.get(0) is not None
