"""P3 stage CLI — Reconciliation & Anomaly Detection (§6 frozen contract).

```bash
python -m src.reconciliation.run --in <validated.jsonl> \
    --out artifacts/golden.jsonl --report artifacts/reconciliation_report.json
```

Flags (all stages): ``--no-llm`` (deterministic — reconciliation never calls
an LLM, so this is accepted and always on), ``--limit N`` (emit at most N
golden records total), ``--log-json`` (§6 stderr protocol).

Stderr protocol:
- with ``--log-json``: log lines are JSON objects containing ``"log": true``;
- on success, the final stderr line is ``{"metrics": {"records_in": n,
  "records_out": n, "llm_calls": 0, "llm_tokens": 0}}``;
- on failure: nonzero exit and final stderr line ``{"error": true,
  "stage": "reconciliation", "message": "...", "detail": {}}``.

Input is streamed line-by-line (never loaded as a whole-file blob); golden
output is written one record per line.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .anomalies import detect_anomalies
from .health import compute_health_and_metrics
from .models import parse_record
from .reconcile import build_golden
from .report import build_report
from .resolve import resolve

STAGE = "reconciliation"


def _log_json(msg: dict) -> None:
    msg.setdefault("log", True)
    print(json.dumps(msg, default=str), file=sys.stderr)


def _metrics_line(records_in: int, records_out: int) -> None:
    print(
        json.dumps(
            {
                "metrics": {
                    "records_in": records_in,
                    "records_out": records_out,
                    "llm_calls": 0,
                    "llm_tokens": 0,
                }
            }
        ),
        file=sys.stderr,
    )


def _error_line(message: str, detail: dict) -> None:
    print(
        json.dumps({"error": True, "stage": STAGE, "message": message, "detail": detail}),
        file=sys.stderr,
    )


def _read_records(path: Path):
    """Stream-parse input JSONL into (chargers, sessions, events)."""
    chargers, sessions, events = [], [], []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {lineno}: invalid JSON: {exc}") from exc
            try:
                parsed = parse_record(rec)
            except ValueError as exc:
                raise ValueError(f"line {lineno}: {exc}") from exc
            data = parsed.model_dump(exclude_unset=False)
            rt = data.get("record_type")
            if rt == "charger":
                chargers.append(data)
            elif rt == "session":
                sessions.append(data)
            elif rt == "maintenance":
                events.append(data)
    return chargers, sessions, events


def _write_golden(path: Path, chargers, sessions, events, limit: int | None) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(path, "w", encoding="utf-8") as f:
        for rec in chargers:
            if limit is not None and written >= limit:
                return written
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
        for rec in sessions:
            if limit is not None and written >= limit:
                return written
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
        for rec in events:
            if limit is not None and written >= limit:
                return written
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
    return written


def run(args: argparse.Namespace) -> int:
    in_path = Path(args.input)
    out_path = Path(args.output)
    report_path = Path(args.report) if args.report else None
    if not in_path.is_file():
        raise FileNotFoundError(f"input not found: {in_path}")

    if args.log_json:
        _log_json({"stage": STAGE, "event": "started", "input": str(in_path)})

    chargers, sessions, events = _read_records(in_path)
    records_in = len(chargers) + len(sessions) + len(events)
    if args.log_json:
        _log_json(
            {
                "stage": STAGE,
                "event": "parsed",
                "records_in": records_in,
                "chargers": len(chargers),
                "sessions": len(sessions),
                "events": len(events),
            }
        )

    res = resolve(chargers, sessions, events)
    rr = build_golden(res)
    anomaly_counts = detect_anomalies(res, rr)
    compute_health_and_metrics(rr)
    records_out = _write_golden(out_path, rr.golden_chargers, rr.golden_sessions, rr.golden_events, args.limit)

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = build_report(records_in, rr, anomaly_counts, records_out)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        if args.log_json:
            _log_json({"stage": STAGE, "event": "report_written", "path": str(report_path)})

    if args.log_json:
        _log_json(
            {
                "stage": STAGE,
                "event": "done",
                "golden_chargers": len(rr.golden_chargers),
                "golden_sessions": len(rr.golden_sessions),
                "golden_events": len(rr.golden_events),
                "clusters": rr.clusters,
            }
        )
    _metrics_line(records_in, records_out)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.reconciliation.run",
        description="P3 Reconciliation & Anomaly Detection (golden records / single source of truth).",
    )
    parser.add_argument("--in", dest="input", required=True, help="validated JSONL (P2 output or fixtures)")
    parser.add_argument("--out", dest="output", required=True, help="golden.jsonl output path")
    parser.add_argument("--report", dest="report", required=True, help="reconciliation_report.json path")
    parser.add_argument("--no-llm", action="store_true", help="accepted for §6 compatibility; reconciliation is always deterministic")
    parser.add_argument("--limit", type=int, default=None, help="emit at most N golden records total")
    parser.add_argument("--log-json", action="store_true", help="§6 stderr JSON log protocol")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 — §6 error protocol
        _error_line(str(exc), {"argv": argv or sys.argv[1:]})
        return 1


if __name__ == "__main__":
    sys.exit(main())
