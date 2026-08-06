"""HTML snapshot tests (M5): rendered pages must match committed snapshots
byte-for-byte — same inputs, same bytes (deterministic rendering).

To regenerate snapshots after an intentional change:
    python -m src.reporting.tests.update_snapshots
"""

from __future__ import annotations

from pathlib import Path

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


def _snapshot(name: str) -> str:
    return (SNAPSHOTS / name).read_text(encoding="utf-8")


def test_psc_html_snapshot(records, kpis, validation_report, reconciliation_report, pipeline_health):
    _, html = render_psc_report(
        records, kpis, validation_report, reconciliation_report, pipeline_health,
        REPORT_DATE,
    )
    assert html == _snapshot("psc_compliance_report.html")


def test_dashboard_html_snapshot(records, kpis, validation_report, reconciliation_report):
    html = render_dashboard(records, kpis, validation_report, reconciliation_report, GEN_AT)
    assert html == _snapshot("dashboard.html")


def test_safety_html_snapshot(records, kpis):
    html = render_safety_view(records, kpis, GEN_AT)
    assert html == _snapshot("safety_view.html")


def test_cx_html_snapshot(records, kpis):
    from src.reporting.models import GoldenCharger, GoldenSession

    summaries = generate_summaries(kpis, no_llm=True)
    chargers = [r for r in records if isinstance(r, GoldenCharger)]
    sessions = [r for r in records if isinstance(r, GoldenSession)]
    html = render_cx_view(chargers, sessions, kpis, summaries, GEN_AT)
    assert html == _snapshot("customer_experience.html")
