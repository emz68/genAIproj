"""NYC DOT "Municipal Lots and Garages" schema — reader dispatch and mapping.

Added at integration time when the official dataset landed (PDM §7 P1:
"contracts are dataset-agnostic; only P1 readers change").
"""

import json
import os
import subprocess
import sys

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures", "nyc_municipal_sample.csv")


def _run(tmp_path):
    out = tmp_path / "canonical.jsonl"
    in_dir = tmp_path / "raw"
    in_dir.mkdir()
    with open(FIXTURE, "rb") as src, open(in_dir / "nyc_municipal_sample.csv", "wb") as dst:
        dst.write(src.read())
    proc = subprocess.run(
        [sys.executable, "-m", "src.ingestion.run", "--in", str(in_dir), "--out", str(out), "--no-llm"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in out.read_text().splitlines()]


def test_nyc_rows_become_sessions_with_verbatim_values(tmp_path):
    recs = _run(tmp_path)
    assert len(recs) == 3
    r = recs[0]
    assert r["record_type"] == "session"
    assert r["session_id"].startswith("nyc-")
    assert r["charger_id"] == "101013:1"
    assert r["station_id"] == "101013"
    assert r["station_name"] == "JGU - Jerome Gun Hill Road Municipal Parking Garage"
    # verbatim: P1 does not normalize values (§5.0)
    assert r["start_time"] == "08/21/2025 19:10:54.0000000"
    assert r["energy_kwh"] == "41.369"
    assert r["duration_min"] == "720.3"
    assert r["invalidity_reason"] == "NULL"  # literal string kept; P2's call
    assert r["provenance"]["source"] == "nyc_dot"


def test_nyc_midnight_crossing_session_has_no_end_time(tmp_path):
    r = _run(tmp_path)[0]  # connected 19:10, disconnected 07:11 next day
    assert "end_time" not in r  # absent == null; no disconnect DATE exists
    assert r["disconnected_time"] == "07:11:12.0000000"  # raw evidence kept


def test_nyc_invalid_session_passes_through_with_status(tmp_path):
    r = _run(tmp_path)[2]
    assert r["session_status"] == "INVALID"
    assert r["invalidity_reason"] == "ZERO_CHARGING_TIME"
    assert r["energy_kwh"] == "0"  # never silently dropped — P2 scores it


def test_cary_schema_still_dispatches_to_cary_assembler(tmp_path):
    in_dir = tmp_path / "raw"
    in_dir.mkdir()
    (in_dir / "cary.csv").write_text(
        "start_date;station_name;charging_time_hh_mm_ss;energy_kwh;address_1;address_2;city;state_province;zip_postal_code\n"
        "2023-01-03T17:58:04+00:00;TOWN OF CARY / TOWN HALL-PWH;00:54:50;3.976;228 Ambassador Loop;;Cary;North Carolina;27513\n",
        encoding="utf-8",
    )
    out = tmp_path / "canonical.jsonl"
    proc = subprocess.run(
        [sys.executable, "-m", "src.ingestion.run", "--in", str(in_dir), "--out", str(out), "--no-llm"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rec = json.loads(out.read_text().splitlines()[0])
    assert rec["session_id"].startswith("cary-")
    assert rec["provenance"]["source"] == "cary"
