"""P2 validation stage CLI — ``python -m src.validation.run`` (PDM §6).

Consumes canonical JSONL (P1 output or own fixtures), emits ``validated.jsonl``
(P2-normalized, every line parseable against the §5.1–5.3 P2 models) and
``validation_report.json`` exactly per §5.5.

    python -m src.validation.run --in canonical.jsonl \
        --out artifacts/validated.jsonl --report artifacts/validation_report.json \
        [--no-llm] [--limit N] [--log-json] [--rules rules_override.yaml]

Protocol (all stages, PDM §6):
- ``--no-llm``: deterministic fallback; zero LLM calls.
- ``--limit N``: emit at most N output records total, then stop.
- ``--log-json``: stderr log lines are JSON with ``"log": true``; on success
  the final stderr line is ``{"metrics": {records_in, records_out, llm_calls,
  llm_tokens}}``.  On failure the process exits nonzero and the final stderr
  line is ``{"error": true, "stage": "validation", "message", "detail"}``.
- Streaming: records are processed line-by-line; only counters accumulate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, NoReturn, TextIO

from .cleaning import CleaningPipeline
from .report import build_report
from .rules import Rules

_STAGE = "validation"


class Logger:
    def __init__(self, json_mode: bool):
        self.json_mode = json_mode

    def log(self, level: str, message: str, **detail: Any) -> None:
        if self.json_mode:
            print(json.dumps({"log": True, "level": level, "message": message, "detail": detail}),
                  file=sys.stderr)
        else:
            print(f"[{level}] {message}", file=sys.stderr)


def _fail(message: str, detail: dict[str, Any] | None = None) -> NoReturn:
    """§6 failure protocol: nonzero exit, final stderr line is the error JSON."""
    print(json.dumps({"error": True, "stage": _STAGE, "message": message, "detail": detail or {}}),
          file=sys.stderr)
    sys.exit(1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.validation.run",
        description="Validate and clean canonical JSONL (Phase P2).",
    )
    parser.add_argument("--in", dest="in_path", required=True, metavar="FILE",
                        help="input canonical JSONL (P1 output or fixture)")
    parser.add_argument("--out", dest="out_path", required=True, metavar="FILE",
                        help="output validated JSONL")
    parser.add_argument("--report", dest="report_path", required=True, metavar="FILE",
                        help="output validation_report.json (§5.5)")
    parser.add_argument("--no-llm", action="store_true",
                        help="deterministic fallback; never call the LLM")
    parser.add_argument("--limit", type=int, default=0, metavar="N",
                        help="emit at most N output records total (0 = unlimited)")
    parser.add_argument("--log-json", action="store_true",
                        help="JSON log lines on stderr; final line is the metrics object")
    parser.add_argument("--rules", default=None, metavar="FILE",
                        help="optional YAML override, deep-merged onto rules.yaml")
    return parser.parse_args(argv)


def _process_stream(
    fin: TextIO,
    fout: TextIO,
    pipeline: CleaningPipeline,
    logger: Logger,
    limit: int,
) -> dict[str, Any]:
    records_in = 0
    records_out = 0
    quarantined = 0
    skipped = 0
    per_source: dict[str, dict[str, Any]] = {}
    per_issue: Counter = Counter()

    for lineno, line in enumerate(fin, 1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            skipped += 1
            logger.log("warn", f"unparseable JSON on line {lineno}", line=lineno, error=str(exc))
            continue
        if not isinstance(raw, dict):
            skipped += 1
            logger.log("warn", f"non-object JSON on line {lineno}", line=lineno)
            continue

        records_in += 1
        try:
            output, is_quarantined = pipeline.process(raw)
        except Exception as exc:  # a record must never kill the run
            skipped += 1
            logger.log("error", f"record on line {lineno} failed", line=lineno, error=str(exc))
            continue
        if output is None:
            skipped += 1
            logger.log("warn", f"unknown record_type on line {lineno}", line=lineno)
            continue

        fout.write(json.dumps(output, ensure_ascii=False) + "\n")
        records_out += 1
        if is_quarantined:
            quarantined += 1

        source = str((raw.get("provenance") or {}).get("source") or "unknown")
        stats = per_source.setdefault(source, {"records": 0, "score_sum": 0.0, "issues": Counter()})
        stats["records"] += 1
        stats["score_sum"] += float(output["quality"]["score"])
        for code in output["quality"]["issues"]:
            stats["issues"][code] += 1
            per_issue[code] += 1

        if limit and records_out >= limit:
            logger.log("info", f"reached --limit {limit}; stopping")
            break

    return {
        "records_in": records_in,
        "records_out": records_out,
        "quarantined": quarantined,
        "skipped": skipped,
        "per_source": per_source,
        "per_issue": per_issue,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logger = Logger(args.log_json)

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    report_path = Path(args.report_path)

    if not in_path.is_file():
        _fail(f"input file not found: {in_path}", {"path": str(in_path)})
    if args.limit < 0:
        _fail("--limit must be >= 0", {"limit": args.limit})
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(f"cannot create output directories: {exc}", {"error": str(exc)})

    try:
        rules = Rules.load(args.rules)
    except Exception as exc:
        _fail(f"cannot load rule catalog: {exc}", {"error": str(exc)})

    pipeline = CleaningPipeline(rules, llm_enabled=not args.no_llm)

    try:
        with in_path.open("r", encoding="utf-8") as fin, out_path.open("w", encoding="utf-8") as fout:
            stats = _process_stream(fin, fout, pipeline, logger, args.limit)
    except OSError as exc:
        _fail(f"cannot read input or write output: {exc}", {"error": str(exc)})

    report = build_report(
        records_in=stats["records_in"],
        records_out=stats["records_out"],
        quarantined=stats["quarantined"],
        per_source=stats["per_source"],
        per_issue=stats["per_issue"],
    )
    try:
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot write report: {exc}", {"error": str(exc)})

    # §6 stderr protocol: final line is the metrics object (--log-json).
    metrics = {
        "records_in": stats["records_in"],
        "records_out": stats["records_out"],
        "llm_calls": pipeline.llm_calls,
        "llm_tokens": pipeline.llm_tokens,
    }
    if args.log_json:
        print(json.dumps({"metrics": metrics}), file=sys.stderr)
    else:
        print(
            f"validated: in={stats['records_in']} out={stats['records_out']} "
            f"quarantined={stats['quarantined']} skipped={stats['skipped']}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
