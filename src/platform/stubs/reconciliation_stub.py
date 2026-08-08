"""A6 stub for the reconciliation stage (§6): validated JSONL → golden JSONL + report.

Pass-through that adds the §5.4 golden fields with deterministic golden_ids
(stable hash of the entity key), HEALTHY health states, empty metrics, and a
§5.5 reconciliation report. No real matching/dedup — that is Yash's P3.
Maintenance events pass through with their resolved charger's golden_id (§5.4).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from src.platform import protocol, schemas

STAGE = "reconciliation"


def _gid(key: str | None, fallback: str) -> str:
    return "g-" + hashlib.sha1((key or fallback).encode()).hexdigest()[:12]


def _merged_from(obj: dict) -> list[dict]:
    prov = obj.get("provenance") or {}
    return [{"source": prov.get("source", "unknown"), "raw_ref": prov.get("raw_ref")}]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"python -m src.platform.stubs.{STAGE}_stub")
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--out", dest="out_path", required=True)
    parser.add_argument("--report", dest="report_path", required=True)
    protocol.add_common_flags(parser)
    args = parser.parse_args(argv)

    in_path = Path(args.in_path)
    if not in_path.is_file():
        protocol.fail(STAGE, f"input file not found: {in_path}", {"path": str(in_path)})

    records_in = 0
    charger_gids: set[str] = set()

    try:
        with protocol.JsonlWriter(Path(args.out_path)) as out:
            for lineno, obj in protocol.iter_jsonl(in_path):
                records_in += 1
                try:
                    schemas.parse_validated_line(obj)
                except (ValidationError, ValueError) as e:
                    protocol.fail(STAGE, f"upstream contract violation at line {lineno}: {e}",
                                  {"input": str(in_path), "line": lineno})
                rt = obj["record_type"]
                if rt == "charger":
                    gid = _gid(obj.get("charger_id") or obj.get("station_id"), f"line{lineno}")
                    charger_gids.add(gid)
                    obj.update({
                        "golden_id": gid,
                        "merged_from": _merged_from(obj),
                        "conflicts_resolved": [],
                        "anomalies": [],
                        "health": {"state": "HEALTHY", "since": None, "evidence": []},
                        "metrics": {"est_uptime_pct": None, "fault_recurrence_count": 0,
                                    "reporting_lag_p50_days": None, "reporting_lag_p95_days": None},
                    })
                elif rt == "session":
                    obj.update({
                        "golden_id": _gid(obj.get("session_id"), f"line{lineno}"),
                        "merged_from": _merged_from(obj),
                        "conflicts_resolved": [],
                        "anomalies": [],
                    })
                else:  # maintenance passes through, linked to its charger (§5.4)
                    obj["golden_id"] = _gid(obj.get("charger_id") or obj.get("station_id"),
                                            "unmatched")
                schemas.parse_golden_line(obj)  # own-output contract check
                if args.limit is None or out.count < args.limit:
                    out.write(obj)
            records_out = out.count
    except ValueError as e:
        protocol.fail(STAGE, str(e), {"input": str(in_path)})

    report = {
        "records_in": records_in,
        "golden_records_out": records_out,
        "duplicates_removed": 0,
        "clusters": len(charger_gids),
        "conflicts_resolved": 0,
        "anomalies": {},
        "per_source_lag_days": {},
    }
    schemas.ReconciliationReport.model_validate(report)
    report_path = Path(args.report_path)
    protocol.ensure_parent_dir(report_path)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    protocol.emit_metrics(protocol.base_metrics(records_in=records_in, records_out=records_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
