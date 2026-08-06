"""P4 — Reporting & Experience: CLI entrypoint (§6 contract).

Usage:
    python -m src.reporting.run --in <golden.jsonl> --out artifacts/reports/ \
        [--validation-report <path>] [--reconciliation-report <path>] \
        [--pipeline-health <path>] [--restates <date>] [--prior-kpis <path>] \
        [--no-llm] [--limit N] [--log-json] [--report-date YYYY-MM-DD]

Contract (§6):
  * --no-llm       deterministic fallback (template summaries, no API calls)
  * --limit N      read at most N golden records (across all record types) —
                   reporting emits files, so the record cap bounds what is
                   rendered; harness asserts ≤ N, never == N
  * --log-json     log lines are JSON objects with "log": true; on success the
                   FINAL stderr line is {"metrics": {...}}; on failure exit
                   nonzero and the final stderr line is {"error": true, ...}
  * output        directory of CSV/HTML under --out (mkdir -p'd here)
  * streaming     golden.jsonl is consumed line-by-line, never whole-file

The three §5.5 report flags are optional; absent files render their sections
as "data unavailable".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from typing import List, Optional

from .kpis import Kpis
from .models import (
    GoldenCharger,
    GoldenRecord,
    GoldenSession,
    PipelineHealth,
    ReconciliationReport,
    ValidationReport,
    parse_golden_line,
)
from .summaries import generate_summaries
from .views import (
    render_cx_view,
    render_dashboard,
    render_psc_report,
    render_safety_view,
)

STAGE = "reporting"


# ---------------------------------------------------------------------------
# logging (stderr protocol §6)
# ---------------------------------------------------------------------------


class Logger:
    def __init__(self, log_json: bool):
        self.log_json = log_json

    def log(self, message: str, **fields):
        if self.log_json:
            payload = {"log": True, "stage": STAGE, "message": message, **fields}
            print(json.dumps(payload), file=sys.stderr)
        else:
            print(f"[{STAGE}] {message}", file=sys.stderr)

    def metrics(self, records_in: int, records_out: int, llm_calls: int, llm_tokens: int):
        payload = {
            "metrics": {
                "records_in": records_in,
                "records_out": records_out,
                "llm_calls": llm_calls,
                "llm_tokens": llm_tokens,
            }
        }
        print(json.dumps(payload), file=sys.stderr)

    def error(self, message: str, detail: Optional[dict] = None):
        payload = {"error": True, "stage": STAGE, "message": message, "detail": detail or {}}
        print(json.dumps(payload), file=sys.stderr)


# ---------------------------------------------------------------------------
# streaming input
# ---------------------------------------------------------------------------


def iter_golden(path: str):
    """Yield golden records line-by-line.

    Lazy generator — the reporting stage never holds the dataset in memory
    (§2.3 streaming mandate). Each caller opens its own pass over the file.
    """
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield parse_golden_line(line)


def _limited(iterable, limit: Optional[int]):
    """Wrap a record iterable so at most ``limit`` records are produced."""
    if limit is None:
        return iterable  # plain function branch — NOT a generator when unbounded

    def _gen():
        n = 0
        for item in iterable:
            yield item
            n += 1
            if n >= limit:
                return

    return _gen()


def load_optional_report(path: Optional[str], model, logger: Logger, label: str):
    """Load a §5.5 report file. Flag given but file unreadable → hard error."""
    if path is None:
        return None
    if not os.path.isfile(path):
        msg = f"{label} file not found: {path}"
        logger.error(msg)
        raise FileNotFoundError(msg)
    with open(path, "r", encoding="utf-8") as fh:
        return model.model_validate(json.load(fh))


def _load_prior_kpis(path: Optional[str]) -> Optional[dict]:
    if path is None:
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.reporting.run",
        description="P4 Reporting: PSC report, dashboard, safety view, CX view.",
    )
    p.add_argument("--in", dest="in_path", required=True, help="golden.jsonl (P3 output, §5.4)")
    p.add_argument("--out", dest="out_dir", default="artifacts/reports/", help="output directory")
    p.add_argument("--validation-report", default=None)
    p.add_argument("--reconciliation-report", default=None)
    p.add_argument("--pipeline-health", default=None)
    p.add_argument("--restates", default=None, metavar="DATE",
                   help="restated-report mode: DATE this report restates (M1)")
    p.add_argument("--prior-kpis", default=None, metavar="PATH",
                   help="JSON of prior-period KPIs for the restatement delta section")
    p.add_argument("--report-date", default=None, metavar="YYYY-MM-DD",
                   help="report date (default: today; pin for deterministic snapshots)")
    p.add_argument("--no-llm", action="store_true", help="deterministic fallback, no API calls")
    p.add_argument("--limit", type=int, default=None, help="max golden records to read")
    p.add_argument("--log-json", action="store_true", help="JSON log lines + metrics/error protocol")
    return p


def run(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logger = Logger(args.log_json)

    try:
        logger.log("stage started", in_path=args.in_path)
        if not os.path.isfile(args.in_path):
            raise FileNotFoundError(f"golden input file not found: {args.in_path}")

        # pass 1: streaming aggregate pass → Kpis (no record retention)
        kpis = Kpis(_limited(iter_golden(args.in_path), args.limit))
        logger.log(f"read {kpis.records_in} golden records")

        validation_report = load_optional_report(
            args.validation_report, ValidationReport, logger, "validation-report"
        )
        reconciliation_report = load_optional_report(
            args.reconciliation_report, ReconciliationReport, logger, "reconciliation-report"
        )
        pipeline_health = load_optional_report(
            args.pipeline_health, PipelineHealth, logger, "pipeline-health"
        )
        prior_kpis = _load_prior_kpis(args.prior_kpis)

        summaries = generate_summaries(kpis, no_llm=args.no_llm)

        report_date = args.report_date or date.today().isoformat()
        generated_at = datetime.now().isoformat(timespec="seconds")

        os.makedirs(args.out_dir, exist_ok=True)

        # M1 — PSC compliance report (CSV + HTML); aggregates only
        psc_csv, psc_html = render_psc_report(
            iter_golden(args.in_path), kpis, validation_report, reconciliation_report,
            pipeline_health, report_date, args.restates, prior_kpis,
        )
        _write(args.out_dir, "psc_compliance_report.csv", psc_csv)
        _write(args.out_dir, "psc_compliance_report.html", psc_html)

        # M2 — dashboard, M3 — safety: one streaming pass each
        _write(args.out_dir, "dashboard.html", render_dashboard(
            _limited(iter_golden(args.in_path), args.limit),
            kpis, validation_report, reconciliation_report, generated_at))
        _write(args.out_dir, "safety_view.html", render_safety_view(
            _limited(iter_golden(args.in_path), args.limit), kpis, generated_at))

        # M4 — CX view: separate streaming passes for chargers and sessions
        def _chargers():
            for r in _limited(iter_golden(args.in_path), args.limit):
                if isinstance(r, GoldenCharger):
                    yield r

        def _sessions():
            for r in _limited(iter_golden(args.in_path), args.limit):
                if isinstance(r, GoldenSession):
                    yield r

        _write(args.out_dir, "customer_experience.html", render_cx_view(
            _chargers(), _sessions(), kpis, summaries, generated_at))

        llm_calls = sum(1 for s in summaries if s.get("llm_used"))
        llm_tokens = 0  # tokens are not surfaced by the template path; LLM path keeps 0 for now
        logger.log("stage finished", out_dir=args.out_dir, files=5)
        logger.metrics(kpis.records_in, kpis.records_in, llm_calls, llm_tokens)
        return 0
    except Exception as exc:  # noqa: BLE001 — protocol requires a JSON error line
        logger.error(str(exc), {"exception": type(exc).__name__})
        return 1


def _write(out_dir: str, name: str, content: str) -> str:
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return path


if __name__ == "__main__":
    sys.exit(run())
