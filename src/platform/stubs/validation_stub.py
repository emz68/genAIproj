"""A6 stub for the validation stage (§6): canonical JSONL → validated JSONL + report.

Trivial pass-through with just enough §5.0 bookkeeping to stay contract-valid:
moves the P1 score to quality.extraction_confidence, sets score = 1.0, and
coerces enum/date fields that would fail the P2 models (unknown connector →
OTHER, unknown status → UNKNOWN, unparseable date/number → null). Records the
§5.5 validation report. Records that fail even the raw model are quarantined
(counted, not emitted) — real rule logic is Sanja's P2.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from src.platform import protocol, schemas

STAGE = "validation"

_CONNECTORS = {"J1772", "CCS", "CHAdeMO", "NACS", "OTHER"}
_LEVELS = {"L1", "L2", "DCFC"}
_STATUSES = {"ACTIVE", "INACTIVE", "MAINTENANCE", "UNKNOWN"}
_SEVERITIES = {"INFO", "MINOR", "MAJOR", "SAFETY"}
_EVENT_TYPES = {"INSPECTION", "REPAIR", "FAULT", "INSTALL", "COMPLAINT", "OTHER"}


def _iso_or_none(value, kind: str):
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        if kind == "date":
            date.fromisoformat(text)
        elif datetime.fromisoformat(text).tzinfo is None:
            return None  # §5.2 requires a timezone; naive → treat as unfixable
        return value
    except ValueError:
        return None


def _float_or_none(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _normalize(obj: dict) -> dict:
    rt = obj["record_type"]
    if rt == "charger":
        ct = obj.get("connector_type")
        obj["connector_type"] = ct if ct in _CONNECTORS else ("OTHER" if ct else None)
        obj["level"] = obj.get("level") if obj.get("level") in _LEVELS else None
        st = obj.get("status")
        obj["status"] = st if st in _STATUSES else ("UNKNOWN" if st else None)
        obj["install_date"] = _iso_or_none(obj.get("install_date"), "date")
        for key in ("lat", "lon", "power_kw"):
            obj[key] = _float_or_none(obj.get(key))
    elif rt == "session":
        for key in ("start_time", "end_time"):
            obj[key] = _iso_or_none(obj.get(key), "datetime")
        for key in ("energy_kwh", "peak_kw", "duration_min"):
            obj[key] = _float_or_none(obj.get(key))
    elif rt == "maintenance":
        sev = obj.get("severity")
        obj["severity"] = sev if sev in _SEVERITIES else None
        et = obj.get("event_type")
        obj["event_type"] = et if et in _EVENT_TYPES else ("OTHER" if et else None)
    quality = dict(obj.get("quality") or {})
    quality["extraction_confidence"] = quality.get("score")
    quality["score"] = 1.0
    quality.setdefault("issues", [])
    quality.setdefault("fixes_applied", [])
    obj["quality"] = quality
    return obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"python -m src.platform.stubs.{STAGE}_stub")
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--out", dest="out_path", required=True)
    parser.add_argument("--report", dest="report_path", required=True)
    protocol.add_common_flags(parser)
    args = parser.parse_args(argv)
    return protocol.run_guarded(STAGE, lambda: _execute(args))


def _execute(args) -> int:
    in_path = Path(args.in_path)
    if not in_path.is_file():
        protocol.fail(STAGE, f"input file not found: {in_path}", {"path": str(in_path)})

    records_in = 0
    quarantined = 0
    per_source: dict[str, dict] = {}
    per_issue: dict[str, int] = {}

    try:
        with protocol.JsonlWriter(Path(args.out_path)) as out:
            for lineno, obj in protocol.iter_jsonl(in_path):
                if args.limit is not None and out.count >= args.limit:
                    break  # §6: at most N output records, then STOP consuming
                records_in += 1
                try:
                    schemas.parse_canonical_line(obj)
                except (ValidationError, ValueError) as e:
                    quarantined += 1
                    per_issue["unparseable_record"] = per_issue.get("unparseable_record", 0) + 1
                    protocol.log(f"quarantined line {lineno}: {e}", log_json=args.log_json, stage=STAGE)
                    continue
                normalized = _normalize(obj)
                schemas.parse_validated_line(normalized)  # own-output contract check
                source = (normalized.get("provenance") or {}).get("source", "unknown")
                bucket = per_source.setdefault(source, {"records": 0, "avg_score": 1.0, "issues": {}})
                bucket["records"] += 1
                out.write(protocol.strip_nones(normalized))
            records_out = out.count
    except ValueError as e:  # bad JSON from iter_jsonl → controlled §6 failure
        protocol.fail(STAGE, str(e), {"input": str(in_path)})

    report = {
        "records_in": records_in,
        "records_out": records_out,
        "quarantined": quarantined,
        "per_source": per_source,
        "per_issue": per_issue,
    }
    schemas.ValidationReport.model_validate(report)
    report_path = Path(args.report_path)
    protocol.ensure_parent_dir(report_path)
    import json
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    protocol.emit_metrics(protocol.base_metrics(records_in=records_in, records_out=records_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
