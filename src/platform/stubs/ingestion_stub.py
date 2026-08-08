"""A6 stub for the ingestion stage (§6): directory of raw files → canonical JSONL.

Emits deterministic, P1-schema-valid records: one maintenance record per .txt
file, one charger + one session per other file. No real parsing — that is
Emlyn's P1. Values are clean so downstream stubs stay schema-valid.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from src.platform import protocol

STAGE = "ingestion"


def _source_for(path: Path) -> str:
    suffix = path.name.lower()
    if suffix.endswith(".txt"):
        return "contractor_reports"
    if ".csv" in suffix:
        return "sessions_csv"
    if ".json" in suffix or ".geojson" in suffix:
        return "station_registry"
    return "unknown"


def _records_for(path: Path, rel: str) -> list[dict]:
    h = hashlib.sha1(rel.encode()).hexdigest()
    provenance = {
        "source": _source_for(path),
        "source_file": rel,
        "ingested_at": protocol.utc_now_iso(),
        "raw_ref": rel,
    }
    if path.name.lower().endswith(".txt"):
        try:
            with protocol.open_maybe_gzip(path) as fh:  # bounded read (§2.3)
                description = fh.read(400).strip() or "(empty)"
        except OSError:
            description = "(unreadable)"
        return [{
            "record_type": "maintenance",
            "event_id": f"stub-evt-{h[:12]}",
            "description": description,
            "extracted_fields": {},
            "provenance": provenance,
            "quality": {"score": 0.5, "issues": [], "fixes_applied": []},
        }]
    charger_id = f"stub-chg-{h[:10]}"
    return [
        {
            "record_type": "charger",
            "charger_id": charger_id,
            "station_id": f"stub-stn-{h[:10]}",
            "network": "STUB-NET",
            "address": {"street": "228 Ambassador Loop", "city": "Cary", "state": "NC", "zip": "27513"},
            "lat": 35.787, "lon": -78.781,
            "connector_type": "J1772", "power_kw": 6.6, "level": "L2", "status": "ACTIVE",
            "install_date": "2020-01-01",
            "provenance": provenance,
            "quality": {"score": 1.0, "issues": [], "fixes_applied": []},
        },
        {
            "record_type": "session",
            "session_id": f"stub-sess-{h[:10]}",
            "charger_id": charger_id,
            "station_id": f"stub-stn-{h[:10]}",
            "start_time": "2023-01-03T17:00:00+00:00",
            "end_time": "2023-01-03T18:00:00+00:00",
            "energy_kwh": 7.4, "peak_kw": 6.6, "duration_min": 60.0,
            "provenance": provenance,
            "quality": {"score": 1.0, "issues": [], "fixes_applied": []},
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"python -m src.platform.stubs.{STAGE}_stub")
    parser.add_argument("--in", dest="in_dir", required=True)
    parser.add_argument("--out", dest="out_path", required=True)
    protocol.add_common_flags(parser)
    args = parser.parse_args(argv)
    return protocol.run_guarded(STAGE, lambda: _execute(args))


def _execute(args) -> int:
    in_dir = Path(args.in_dir)
    if not in_dir.is_dir():
        protocol.fail(STAGE, f"input directory not found: {in_dir}", {"path": str(in_dir)})

    files = sorted(p for p in in_dir.rglob("*") if p.is_file() and not p.name.startswith("."))
    files_read = 0
    with protocol.JsonlWriter(Path(args.out_path)) as out:
        for path in files:
            if args.limit is not None and out.count >= args.limit:
                break
            files_read += 1
            rel = str(path.relative_to(in_dir))
            protocol.log(f"reading {rel}", log_json=args.log_json, stage=STAGE)
            for record in _records_for(path, rel):
                if args.limit is not None and out.count >= args.limit:
                    break
                out.write(protocol.strip_nones(record))
        emitted = out.count

    protocol.emit_metrics(protocol.base_metrics(records_in=files_read, records_out=emitted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
