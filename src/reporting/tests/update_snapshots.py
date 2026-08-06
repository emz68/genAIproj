"""Regenerate committed HTML snapshots (run deliberately, not by pytest).

Usage: python -m src.reporting.tests.update_snapshots
"""

from __future__ import annotations

from pathlib import Path

from src.reporting.kpis import Kpis
from src.reporting.models import (
    GoldenCharger,
    GoldenSession,
    PipelineHealth,
    ReconciliationReport,
    ValidationReport,
    parse_golden_line,
)
from src.reporting.summaries import generate_summaries
from src.reporting.views import (
    render_cx_view,
    render_dashboard,
    render_psc_report,
    render_safety_view,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
SNAPSHOTS = FIXTURES / "snapshots"

REPORT_DATE = "2026-08-06"
GEN_AT = "2026-08-06T00:00:00"


def main() -> None:
    with open(FIXTURES / "golden.jsonl", encoding="utf-8") as fh:
        records = [parse_golden_line(line) for line in fh if line.strip()]
    kpis = Kpis(records)
    validation_report = ValidationReport.model_validate(
        __import__("json").loads((FIXTURES / "validation_report.json").read_text(encoding="utf-8"))
    )
    reconciliation_report = ReconciliationReport.model_validate(
        __import__("json").loads((FIXTURES / "reconciliation_report.json").read_text(encoding="utf-8"))
    )
    pipeline_health = PipelineHealth.model_validate(
        __import__("json").loads((FIXTURES / "pipeline_health.json").read_text(encoding="utf-8"))
    )

    SNAPSHOTS.mkdir(parents=True, exist_ok=True)

    _, psc_html = render_psc_report(
        records, kpis, validation_report, reconciliation_report, pipeline_health,
        REPORT_DATE,
    )
    (SNAPSHOTS / "psc_compliance_report.html").write_text(psc_html, encoding="utf-8")

    dash = render_dashboard(records, kpis, validation_report, reconciliation_report, GEN_AT)
    (SNAPSHOTS / "dashboard.html").write_text(dash, encoding="utf-8")

    safety = render_safety_view(records, kpis, GEN_AT)
    (SNAPSHOTS / "safety_view.html").write_text(safety, encoding="utf-8")

    cx = render_cx_view(
        (r for r in records if isinstance(r, GoldenCharger)),
        (r for r in records if isinstance(r, GoldenSession)),
        kpis,
        generate_summaries(kpis, no_llm=True),
        GEN_AT,
    )
    (SNAPSHOTS / "customer_experience.html").write_text(cx, encoding="utf-8")

    print(f"Wrote 4 snapshots to {SNAPSHOTS}")


if __name__ == "__main__":
    main()
