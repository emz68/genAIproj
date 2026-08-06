"""PSC compliance report (M1): CSV content, lineage appendix, restatement."""

from __future__ import annotations

import csv
import io

from src.reporting.views import render_psc_report

REPORT_DATE = "2026-08-06"


def test_psc_csv_kpi_rows(records, kpis, validation_report, reconciliation_report, pipeline_health):
    csv_text, _ = render_psc_report(
        records, kpis, validation_report, reconciliation_report, pipeline_health,
        REPORT_DATE,
    )
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    kpi = {r["metric"]: r for r in rows if r["section"] == "KPI"}
    assert kpi["Chargers deployed"]["value"] == "6"
    assert kpi["Chargers active"]["value"] == "4"
    assert kpi["Sessions"]["value"] == "8"
    assert kpi["Energy delivered"]["value"] == "100.00"
    assert kpi["Est. fleet uptime"]["value"] == "83.7%"  # 5.02/6 → ×100
    assert kpi["Data completeness"]["value"] == "91.0%"  # 12.74/14 → ×100


def test_psc_csv_lineage_rows(records, kpis, validation_report, reconciliation_report, pipeline_health):
    csv_text, _ = render_psc_report(
        records, kpis, validation_report, reconciliation_report, pipeline_health,
        REPORT_DATE,
    )
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    lineage = [r for r in rows if r["section"] == "LINEAGE"]
    v_rows = {r["metric"]: r["value"] for r in lineage if r["note"].startswith("Validation")}
    r_rows = {r["metric"]: r["value"] for r in lineage if r["note"].startswith("Reconciliation")}
    p_rows = {r["metric"]: r["value"] for r in lineage if r["note"].startswith("Pipeline")}
    # validation report figures
    assert v_rows["records in"] == "100"
    assert v_rows["records out"] == "98"
    assert v_rows["quarantined"] == "2"
    # reconciliation report figures
    assert r_rows["golden records out"] == "18"
    assert r_rows["duplicates removed"] == "6"
    assert r_rows["clusters"] == "12"
    # per-source quality scores
    assert v_rows["cary_sessions — avg quality score"] == "0.93"
    assert v_rows["afdc_stations — avg quality score"] == "0.97"
    # lag figures (Yash's numbers, rendered only)
    assert r_rows["cary_sessions — p50 / p95 (days)"] == "2.5 / 8.0"
    # pipeline health
    assert p_rows["ingestion — records"] == "124 → 121"
    assert p_rows["reporting — duration / LLM calls"] == "0.40s / 0"


def test_psc_html_contains_kpis_and_lineage(records, kpis, validation_report, reconciliation_report, pipeline_health):
    _, html = render_psc_report(
        records, kpis, validation_report, reconciliation_report, pipeline_health,
        REPORT_DATE,
    )
    assert "PSC Compliance Report" in html
    assert REPORT_DATE in html
    assert "Program KPIs" in html
    assert "Data lineage appendix" in html
    assert "83.7%" in html
    assert "100.00" in html


def test_restatement_banner_and_delta(records, kpis, validation_report, reconciliation_report, pipeline_health, prior_kpis):
    csv_text, html = render_psc_report(
        records, kpis, validation_report, reconciliation_report, pipeline_health,
        REPORT_DATE,
        restates_date="2026-07-01",
        prior_kpis=prior_kpis,
    )
    assert "RESTATES report of 2026-07-01" in html
    assert "Restatement" in html

    rows = list(csv.DictReader(io.StringIO(csv_text)))
    restate = {r["metric"]: r for r in rows if r["section"] == "RESTATEMENT"}
    # chargers 6 vs 5 → +1  (value=current, unit=prior, note=delta)
    assert restate["Chargers deployed"]["value"] == "6"
    assert restate["Chargers deployed"]["unit"] == "5"
    assert restate["Chargers deployed"]["note"] == "+1"
    # energy 100.0 vs 80.0 → +20.00
    assert restate["Energy delivered (kWh)"]["value"] == "100.00"
    assert restate["Energy delivered (kWh)"]["unit"] == "80.00"
    assert restate["Energy delivered (kWh)"]["note"] == "+20.00"
    # uptime: 0.83666…×100 = 83.7 vs prior 80.5 → +3.2
    assert restate["Est. fleet uptime (%)"]["value"] == "83.7"
    assert restate["Est. fleet uptime (%)"]["unit"] == "80.5"
    assert restate["Est. fleet uptime (%)"]["note"] == "+3.2"
    # completeness: 0.91×100 = 91.0 vs prior 85.0 → +6.0
    assert restate["Data completeness (%)"]["value"] == "91.0"
    assert restate["Data completeness (%)"]["unit"] == "85.0"
    assert restate["Data completeness (%)"]["note"] == "+6.0"


def test_no_restatement_without_flags(records, kpis, validation_report, reconciliation_report, pipeline_health):
    csv_text, html = render_psc_report(
        records, kpis, validation_report, reconciliation_report, pipeline_health,
        REPORT_DATE,
    )
    assert "RESTATES report of" not in html
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert all(r["section"] != "RESTATEMENT" for r in rows)


def test_lineage_unavailable_without_reports(records, kpis):
    csv_text, html = render_psc_report(records, kpis, None, None, None, REPORT_DATE)
    assert "No lineage data available" in html
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert all(r["section"] != "LINEAGE" for r in rows)
