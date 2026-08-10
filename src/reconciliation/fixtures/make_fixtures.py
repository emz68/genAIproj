"""Deterministic fixture builder for P3 (Y5).

Writes:
- fixtures/validated.jsonl      — P2-normalized input with planted
  duplicates / conflicts / anomalies (the "known truth" plants below)
- fixtures/validated_late.jsonl — superset: base + late-arriving records,
  for the Y2 supersede-semantics test (same entities keep the same
  golden_id; never duplicate)

Run:  python -m src.reconciliation.fixtures.make_fixtures   (or directly)
Reproducible: byte-identical output on every run (no randomness).

PLANTED TRUTH (each plant is caught by exactly one detector):
- A1 charger (ST-1001#1 J1772, Cary Town Hall): 8 golden sessions Jan–Mar
  then silence → utilization_cliff (SUSPECT_OUTAGE); one 42.7 kWh session →
  energy_outlier; a near-identical session pair (distinct ids) →
  duplicate_billing.
- A2 charger (ST-1001#2 CCS): two time-overlapping sessions on one port →
  concurrent_sessions; both carry fault_code E-42 → repeated_fault_code.
- B1 charger (ST-2001#1, Raleigh Municipal): registry update record with
  conflicting power_kw/status → 2 conflicts_resolved (fresher wins); one
  session with peak 9.2 kW > 6.6×1.1 → power_over_rated (SAFETY); a SAFETY
  maintenance event with no later REPAIR → unresolved_safety_event →
  SAFETY_REVIEW.
- C1 charger (ST-3001#1, Cary DT Deck): clean — HEALTHY control; its SAFETY
  event is followed by a REPAIR (resolved, no anomaly); duplicate event
  record → maintenance dedup.
- Unknown-station session + maintenance event → pass through unresolved.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

INGEST_SESSION = "2024-11-10T00:00:00Z"  # late-arriving cary batch
INGEST_AFDC = "2024-01-10T00:00:00Z"
INGEST_AFDC_LATE = "2024-06-01T00:00:00Z"
INGEST_CONTRACTOR = "2024-11-20T00:00:00Z"


def _quality(score: float, issues=None, fixes=None) -> dict:
    return {"score": score, "extraction_confidence": score,
            "issues": issues or [], "fixes_applied": fixes or []}


def charger(charger_id, station_id, station_name, street, city, state, zipcode,
            lat, lon, connector, power, level, status, source_file, ingested,
            score=0.95, network="ChargePoint", install="2015-03-01T00:00:00Z"):
    return {
        "record_type": "charger",
        "charger_id": charger_id,
        "station_id": station_id,
        "station_name": station_name,
        "network": network,
        "address": {"street": street, "city": city, "state": state, "zip": zipcode},
        "lat": lat, "lon": lon,
        "connector_type": connector, "power_kw": power, "level": level,
        "status": status, "install_date": install,
        "provenance": {"source": "afdc-bts", "source_file": source_file,
                       "ingested_at": ingested, "raw_ref": f"{station_id}:{charger_id}:{source_file}"},
        "quality": _quality(score),
    }


def session(session_id, charger_id, station_id, station_name, street, city, zipcode,
            start, end, energy, peak, dur, source="cary", fault=None, score=0.95,
            source_file="ev_charging_sessions_cary.csv.gz", raw_ref=None):
    return {
        "record_type": "session",
        "session_id": session_id,
        "charger_id": charger_id,
        "station_id": station_id,
        "station_name": station_name,
        "address_1": street, "city": city, "zip_postal_code": zipcode,
        "start_time": start, "end_time": end,
        "energy_kwh": energy, "peak_kw": peak, "duration_min": dur,
        **({"fault_code": fault} if fault else {}),
        "provenance": {"source": source, "source_file": source_file,
                       "ingested_at": INGEST_SESSION, "raw_ref": raw_ref or session_id},
        "quality": _quality(score),
    }


def event(event_id, charger_id, station_id, station_name, date, etype, severity,
          desc, source_file="report_001_fault.txt", score=0.7):
    return {
        "record_type": "maintenance",
        "event_id": event_id,
        "charger_id": charger_id, "station_id": station_id,
        "station_name": station_name,
        "event_date": date, "event_type": etype, "severity": severity,
        "description": desc,
        "provenance": {"source": "contractor", "source_file": source_file,
                       "ingested_at": INGEST_CONTRACTOR, "raw_ref": event_id},
        "quality": _quality(score),
    }


def base_records() -> list:
    r = []
    # --- Chargers -----------------------------------------------------------
    # A1/A2 — TOWN OF CARY / TOWN HALL-PWH (Cary NC)
    r.append(charger("ST-1001#1#J1772#1", "ST-1001", "TOWN OF CARY / TOWN HALL-PWH",
                     "228 Ambassador Loop", "Cary", "NC", "27513",
                     35.7866, -78.7815, "J1772", 6.6, "L2", "ACTIVE",
                     "afdc_stations_nc_elec.geojson.gz", INGEST_AFDC))
    r.append(charger("ST-1001#2#CCS#1", "ST-1001", "TOWN OF CARY / TOWN HALL-PWH",
                     "228 Ambassador Loop", "Cary", "NC", "27513",
                     35.7866, -78.7815, "CCS", 50.0, "DCFC", "ACTIVE",
                     "afdc_stations_nc_elec.geojson.gz", INGEST_AFDC))
    # B1 — City of Raleigh - Municipal Building, + a LATE registry update
    # (same port, conflicting power/status) → conflict resolution.
    r.append(charger("ST-2001#1#J1772#1", "ST-2001", "City of Raleigh - Municipal Building",
                     "1 Exchange Plaza", "Raleigh", "NC", "27601",
                     35.7770, -78.6400, "J1772", 6.6, "L2", "ACTIVE",
                     "afdc_stations_nc_elec.geojson.gz", INGEST_AFDC, network="Non-Networked"))
    r.append(charger("ST-2001#1#J1772#1", "ST-2001", "City of Raleigh - Municipal Building",
                     "1 Exchange Plaza", "Raleigh", "NC", "27601",
                     35.7770, -78.6400, "J1772", 7.2, "L2", "MAINTENANCE",
                     "afdc_stations_nc_elec_update.geojson.gz", INGEST_AFDC_LATE, network="Non-Networked",
                     score=0.9))
    # C1 — TOWN OF CARY / DT DECK P2 (2)
    r.append(charger("ST-3001#1#J1772#1", "ST-3001", "TOWN OF CARY / DT DECK P2 (2)",
                     "113 Walnut St", "Cary", "NC", "27511",
                     35.7849, -78.7811, "J1772", 6.6, "L2", "ACTIVE",
                     "afdc_stations_nc_elec.geojson.gz", INGEST_AFDC))

    # --- Sessions (Source A style) ------------------------------------------
    # A1: Jan–Mar sessions then silence → utilization_cliff.
    a1_normals = [
        ("2024-01-08T09:00:00Z", "2024-01-08T10:30:00Z", 5.1, 6.5, 90),
        ("2024-01-22T09:00:00Z", "2024-01-22T10:30:00Z", 4.9, 6.4, 90),
        ("2024-02-05T09:00:00Z", "2024-02-05T10:30:00Z", 5.2, 6.5, 90),
        ("2024-02-19T09:00:00Z", "2024-02-19T10:30:00Z", 5.0, 6.4, 90),
        ("2024-03-04T09:00:00Z", "2024-03-04T10:30:00Z", 4.8, 6.3, 90),
        ("2024-03-18T09:00:00Z", "2024-03-18T10:30:00Z", 5.3, 6.5, 90),
    ]
    for i, (s, e, en, pk, d) in enumerate(a1_normals, start=1):
        r.append(session(f"cary-a1-{i:03d}", "ST-1001#1#J1772#1", "ST-1001",
                         "TOWN OF CARY / TOWN HALL-PWH", "228 Ambassador Loop",
                         "Cary", "27513", s, e, en, pk, d, raw_ref=f"a1-{i:03d}"))
    # exact duplicate of session #3 (same session_id) → dedup
    r.append(session("cary-a1-003", "ST-1001#1#J1772#1", "ST-1001",
                     "TOWN OF CARY / TOWN HALL-PWH", "228 Ambassador Loop",
                     "Cary", "27513", "2024-02-05T09:00:00Z", "2024-02-05T10:30:00Z",
                     5.2, 6.5, 90, raw_ref="a1-003dup", score=0.9))
    # energy outlier: 42.7 kWh on an L2 port
    r.append(session("cary-a1-101", "ST-1001#1#J1772#1", "ST-1001",
                     "TOWN OF CARY / TOWN HALL-PWH", "228 Ambassador Loop",
                     "Cary", "27513", "2024-03-25T09:00:00Z", "2024-03-25T11:30:00Z",
                     42.7, 6.6, 150, raw_ref="a1-101"))
    # duplicate-billing pair: near-identical start/energy, distinct ids
    r.append(session("cary-a1-201", "ST-1001#1#J1772#1", "ST-1001",
                     "TOWN OF CARY / TOWN HALL-PWH", "228 Ambassador Loop",
                     "Cary", "27513", "2024-03-28T08:00:00Z", "2024-03-28T09:30:00Z",
                     5.4, 6.5, 90, raw_ref="a1-201"))
    r.append(session("cary-a1-202", "ST-1001#1#J1772#1", "ST-1001",
                     "TOWN OF CARY / TOWN HALL-PWH", "228 Ambassador Loop",
                     "Cary", "27513", "2024-03-28T08:05:00Z", "2024-03-28T09:35:00Z",
                     5.6, 6.5, 90, raw_ref="a1-202"))

    # A2: concurrent pair + repeated fault code E-42
    r.append(session("cary-a2-001", "ST-1001#2#CCS#1", "ST-1001",
                     "TOWN OF CARY / TOWN HALL-PWH", "228 Ambassador Loop",
                     "Cary", "27513", "2024-04-10T09:00:00Z", "2024-04-10T09:40:00Z",
                     24.1, 49.0, 40, fault="E-42", raw_ref="a2-001"))
    r.append(session("cary-a2-002", "ST-1001#2#CCS#1", "ST-1001",
                     "TOWN OF CARY / TOWN HALL-PWH", "228 Ambassador Loop",
                     "Cary", "27513", "2024-04-10T09:20:00Z", "2024-04-10T10:00:00Z",
                     23.8, 48.5, 40, fault="E-42", raw_ref="a2-002"))

    # B1: 5 sessions, one with peak 9.2 kW > 6.6×1.1 → power_over_rated
    b1_sessions = [
        ("2024-05-01T12:00:00Z", "2024-05-01T13:30:00Z", 5.0, 6.5, 90),
        ("2024-05-15T12:00:00Z", "2024-05-15T13:30:00Z", 5.2, 9.2, 90),  # over-rated
        ("2024-06-01T12:00:00Z", "2024-06-01T13:30:00Z", 4.9, 6.4, 90),
        ("2024-06-15T12:00:00Z", "2024-06-15T13:30:00Z", 5.1, 6.5, 90),
        ("2024-07-01T12:00:00Z", "2024-07-01T13:30:00Z", 5.0, 6.4, 90),
    ]
    for i, (s, e, en, pk, d) in enumerate(b1_sessions, start=1):
        r.append(session(f"cary-b1-{i:03d}", "ST-2001#1#J1772#1", "ST-2001",
                         "City of Raleigh - Municipal Building", "1 Exchange Plaza",
                         "Raleigh", "27601", s, e, en, pk, d, raw_ref=f"b1-{i:03d}"))

    # C1: 5 clean sessions (control)
    c1_sessions = [
        ("2024-07-10T08:00:00Z", "2024-07-10T09:30:00Z", 5.0, 6.4, 90),
        ("2024-07-24T08:00:00Z", "2024-07-24T09:30:00Z", 5.1, 6.5, 90),
        ("2024-08-07T08:00:00Z", "2024-08-07T09:30:00Z", 4.9, 6.4, 90),
        ("2024-08-21T08:00:00Z", "2024-08-21T09:30:00Z", 5.2, 6.5, 90),
        ("2024-09-04T08:00:00Z", "2024-09-04T09:30:00Z", 5.0, 6.4, 90),
    ]
    for i, (s, e, en, pk, d) in enumerate(c1_sessions, start=1):
        r.append(session(f"cary-c1-{i:03d}", "ST-3001#1#J1772#1", "ST-3001",
                         "TOWN OF CARY / DT DECK P2 (2)", "113 Walnut St",
                         "Cary", "27511", s, e, en, pk, d, raw_ref=f"c1-{i:03d}"))

    # Unknown-station session → unresolved golden session
    r.append(session("cary-x-001", None, None, "MYSTERY PLAZA CHARGER",
                     "1 Nowhere St", "Cary", "27513",
                     "2024-06-15T14:00:00Z", "2024-06-15T15:00:00Z",
                     3.9, 6.4, 60, raw_ref="x-001"))

    # --- Maintenance events (Source C style) --------------------------------
    # B1: SAFETY fault, never repaired → unresolved_safety_event
    r.append(event("evt-b1", "ST-2001#1#J1772#1", "ST-2001",
                   "City of Raleigh - Municipal Building", "2024-10-01", "FAULT",
                   "SAFETY", "charger smoking, cable insulation melted",
                   source_file="report_001_fault.txt"))
    # C1: SAFETY fault + REPAIR 4 days later → resolved; duplicate event → dedup
    r.append(event("evt-c1", "ST-3001#1#J1772#1", "ST-3001",
                   "TOWN OF CARY / DT DECK P2 (2)", "2024-11-01", "FAULT",
                   "SAFETY", "breaker trip x3", source_file="report_002_fault.txt"))
    r.append(event("evt-c1", "ST-3001#1#J1772#1", "ST-3001",
                   "TOWN OF CARY / DT DECK P2 (2)", "2024-11-01", "FAULT",
                   "SAFETY", "breaker trip x3", source_file="report_002_fault.txt"))
    r.append(event("evt-c2", "ST-3001#1#J1772#1", "ST-3001",
                   "TOWN OF CARY / DT DECK P2 (2)", "2024-11-05", "REPAIR",
                   "MINOR", "replaced breaker", source_file="report_003_email.txt"))
    # Unknown-station event → passes through unresolved
    r.append(event("evt-x1", None, None, "MYSTERY PLAZA CHARGER", "2024-09-20",
                   "INSPECTION", "INFO", "routine site check",
                   source_file="report_004_inspection.txt"))
    return r


def late_records() -> list:
    """Late-arriving records (superset): new session for A1, a brand-new
    station, and a late maintenance event."""
    return [
        session("cary-a1-301", "ST-1001#1#J1772#1", "ST-1001",
                "TOWN OF CARY / TOWN HALL-PWH", "228 Ambassador Loop",
                "Cary", "27513", "2024-10-20T09:00:00Z", "2024-10-20T10:30:00Z",
                5.0, 6.4, 90, raw_ref="a1-301"),
        charger("ST-4001#1#J1772#1", "ST-4001", "Wake Forest Gateway Plaza",
                "900 Durham Rd", "Wake Forest", "NC", "27587",
                35.9799, -78.5099, "J1772", 6.6, "L2", "ACTIVE",
                "afdc_stations_nc_elec.geojson.gz", INGEST_AFDC_LATE),
        event("evt-c3", "ST-3001#1#J1772#1", "ST-3001",
              "TOWN OF CARY / DT DECK P2 (2)", "2024-11-10", "INSPECTION",
              "MINOR", "post-repair verification", source_file="report_005_email.txt"),
    ]


def main() -> None:
    records = base_records()
    with open(HERE / "validated.jsonl", "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(HERE / "validated_late.jsonl", "w", encoding="utf-8") as f:
        for rec in records + late_records():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} base records → validated.jsonl")
    print(f"wrote {len(records) + len(late_records())} records → validated_late.jsonl")


if __name__ == "__main__":
    main()
