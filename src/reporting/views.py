"""P4 — Reporting & Experience: the four deliverable views (M1–M4).

All numbers rendered here come from golden records (§5.4, computed by P2/P3)
or the optional §5.5 report files (computed by Sanja/Yash/Anastasia). When an
optional report file is absent the affected section renders a graceful
"data unavailable" note (M5 requirement).

Streaming (§2.3): every render function consumes record ITERABLES in a single
pass (never materializes the dataset). Per-record fragments accumulate only in
proportion to the OUTPUT size, keeping memory flat on the §8 scale run. The
only inputs that must be materialized are the map points (tiny tuples).

Every function is pure and deterministic: same inputs → same bytes, which is
what the HTML snapshot tests rely on.
"""

from __future__ import annotations

import csv
import io
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .kpis import CHRONIC_FAILURE_THRESHOLD, HEALTH_ORDER, SEVERITY_ORDER, Kpis
from .models import (
    GoldenCharger,
    GoldenMaintenance,
    GoldenRecord,
    GoldenSession,
    PipelineHealth,
    ReconciliationReport,
    ValidationReport,
)
from .render import (
    Point,
    badge,
    esc,
    fmt_number,
    fmt_pct,
    kpi_grid,
    page,
    svg_map,
    table,
)

# ---------------------------------------------------------------------------
# M1 — PSC compliance report (CSV + HTML)
# ---------------------------------------------------------------------------


def render_psc_report(
    records: Iterable[GoldenRecord],  # accepted for API symmetry; KPI data comes from `kpis`
    kpis: Kpis,
    validation_report: Optional[ValidationReport],
    reconciliation_report: Optional[ReconciliationReport],
    pipeline_health: Optional[PipelineHealth],
    report_date: str,
    restates_date: Optional[str] = None,
    prior_kpis: Optional[Dict] = None,
) -> Tuple[str, str]:
    """Return (csv_text, html_text) for the PSC compliance report (M1).

    Purely aggregate-driven (kpis + §5.5 reports) — no per-record pass.
    """
    k = kpis.as_dict()

    kpi_rows = [
        ("Chargers deployed", fmt_number(k["chargers_deployed"], 0), "count", "golden charger records"),
        ("Chargers active", fmt_number(k["active_chargers"], 0), "count", "status = ACTIVE"),
        ("Energy delivered", fmt_number(k["energy_delivered_kwh"], 2), "kWh", "sum over sessions"),
        ("Sessions", fmt_number(k["sessions_count"], 0), "count", "golden session records"),
        ("Est. fleet uptime", fmt_pct(k["est_uptime_pct"]), "%", "fleet mean of per-charger est_uptime_pct (P3 metrics)"),
        ("Data completeness", fmt_pct(k["data_completeness_pct"]), "%", "mean of P2 quality.score over charger+session records"),
    ]

    lineage_rows = _lineage_rows(validation_report, reconciliation_report, pipeline_health)

    # --- restated-report mode (M1) -----------------------------------------
    restate_html = ""
    delta_rows: List[Tuple[str, str, str, str]] = []
    if restates_date and prior_kpis:
        banner = (
            f'<div class="banner">&#9888; This report RESTATES report of {esc(restates_date)} '
            f"— golden data has superseded the prior period's run.</div>"
        )
        restate_html = f"<section><h2>Restatement</h2>{banner}{_delta_table(kpis, prior_kpis)}</section>"
        delta_rows = _delta_rows(kpis, prior_kpis)

    html = page(
        "PSC Compliance Report",
        "Program KPIs with data lineage — rendered from golden records and stage reports",
        f"""
<section>
  <h2>Program KPIs</h2>
  {kpi_grid([(label, value, note) for label, value, _, note in kpi_rows])}
</section>
{restate_html}
<section>
  <h2>Data lineage appendix</h2>
  <p class="muted">Per-stage record counts and quality figures rendered from the §5.5 stage
  reports — demonstrates that reporting is accurate and on time.</p>
  {table(["Stage", "Metric", "Value"], lineage_rows, empty_note="No lineage data available")}
</section>
""",
        report_date,
    )

    # --- CSV (same content, flat rows with section markers) -----------------
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "metric", "value", "unit", "note"])
    for label, value, unit, note in kpi_rows:
        writer.writerow(["KPI", label, value, unit, note])
    for section, metric, value in lineage_rows:
        writer.writerow(["LINEAGE", metric, value, "", section])
    for metric, prior, current, delta in delta_rows:
        # RESTATEMENT rows reuse the value/unit/note columns as
        # current / prior / delta for flat-CSV readability.
        writer.writerow(["RESTATEMENT", metric, current, prior, delta])
    return buf.getvalue(), html


def _delta_table(kpis: "Kpis", prior: Dict) -> str:
    return table(
        ["KPI", "Prior report", "Current report", "Delta"],
        _delta_rows(kpis, prior),
        empty_note="No comparable prior figures",
    )


def _delta_rows(kpis: "Kpis", prior: Dict) -> List[Tuple[str, str, str, str]]:
    """Delta rows from RAW KPI values (not the display-rounded dict), so the
    restatement deltas are computed exactly and only formatted for display."""
    pairs = [
        ("chargers_deployed", "Chargers deployed", kpis.chargers_deployed, 0),
        ("active_chargers", "Chargers active", kpis.active_chargers, 0),
        ("sessions_count", "Sessions", kpis.sessions_count, 0),
        ("energy_delivered_kwh", "Energy delivered (kWh)", kpis.energy_delivered_kwh, 2),
        ("est_uptime_pct", "Est. fleet uptime (%)", kpis.est_uptime_pct, 1),
        ("data_completeness_pct", "Data completeness (%)", kpis.data_completeness_pct, 1),
    ]
    rows = []
    for key, label, current, nd in pairs:
        p = prior.get(key)
        if p is None or current is None:
            continue
        c = current
        # est_uptime_pct / data_completeness_pct are fractions in golden data
        # (§5.4) but percentage points in the prior-KPI file — align scales.
        if key in ("est_uptime_pct", "data_completeness_pct"):
            c = c * 100
        delta = c - p
        sign = "+" if delta > 0 else ""
        rows.append((label, fmt_number(p, nd), fmt_number(c, nd), f"{sign}{delta:.{nd}f}"))
    return rows


def _lineage_rows(
    validation_report: Optional[ValidationReport],
    reconciliation_report: Optional[ReconciliationReport],
    pipeline_health: Optional[PipelineHealth],
) -> List[Tuple[str, str, str]]:
    """(section, metric, value) rows for the lineage appendix."""
    rows: List[Tuple[str, str, str]] = []
    if validation_report:
        rows += [
            ("Validation", "records in", str(validation_report.records_in)),
            ("Validation", "records out", str(validation_report.records_out)),
            ("Validation", "quarantined", str(validation_report.quarantined)),
        ]
        for src, stats in sorted(validation_report.per_source.items()):
            avg = fmt_number(stats.avg_score, 2) if stats.avg_score is not None else "N/A"
            rows.append(("Validation per-source", f"{src} — avg quality score", avg))
        for code, n in sorted(validation_report.per_issue.items()):
            rows.append(("Validation per-issue", code, str(n)))
    if reconciliation_report:
        rows += [
            ("Reconciliation", "records in", str(reconciliation_report.records_in)),
            ("Reconciliation", "golden records out", str(reconciliation_report.golden_records_out)),
            ("Reconciliation", "duplicates removed", str(reconciliation_report.duplicates_removed)),
            ("Reconciliation", "clusters", str(reconciliation_report.clusters)),
            ("Reconciliation", "conflicts resolved", str(reconciliation_report.conflicts_resolved)),
        ]
        for src, lag in sorted(reconciliation_report.per_source_lag_days.items()):
            p50 = fmt_number(lag.p50, 1) if lag.p50 is not None else "N/A"
            p95 = fmt_number(lag.p95, 1) if lag.p95 is not None else "N/A"
            rows.append(("Reconciliation lag", f"{src} — p50 / p95 (days)", f"{p50} / {p95}"))
    if pipeline_health:
        for stage, st in sorted(pipeline_health.stages.items()):
            dur = fmt_number(st.duration_s, 2) if st.duration_s is not None else "N/A"
            rows.append(("Pipeline", f"{stage} — records", f"{st.records_in} → {st.records_out}"))
            rows.append(("Pipeline", f"{stage} — duration / LLM calls", f"{dur}s / {st.llm_calls}"))
    return rows


# ---------------------------------------------------------------------------
# M2 — Operational awareness dashboard (static HTML)
# ---------------------------------------------------------------------------


def render_dashboard(
    records: Iterable[GoldenRecord],
    kpis: Kpis,
    validation_report: Optional[ValidationReport],
    reconciliation_report: Optional[ReconciliationReport],
    generated_at: str,
) -> str:
    k = kpis.as_dict()

    cards: List[str] = []
    points: List[Point] = []
    feed: List[Tuple[int, str]] = []  # (severity rank, html)

    for r in records:  # single streaming pass
        if isinstance(r, GoldenCharger):
            state = r.health.state if r.health else None
            uptime = fmt_pct(r.metrics.est_uptime_pct if r.metrics else None)
            city = r.address.city if r.address and r.address.city else "—"
            cards.append(
                f'<div class="card" style="border-left-color:{_health_color(state)}">'
                f'<div class="cid">{esc(r.charger_id or r.golden_id or "?")} '
                f'{badge(state or "UNKNOWN", _health_color(state))}</div>'
                f'<div class="meta">{esc(city)} · uptime {esc(uptime)} · '
                f'status {esc((r.status or "UNKNOWN").upper())}</div></div>'
            )
            if r.lat is not None and r.lon is not None:
                points.append(Point.from_charger(r))
        for a in r.anomalies:
            feed.append((_sev_rank(a.severity), _anomaly_block(a, r)))

    grid = (
        f'<div class="card-grid">{"".join(cards)}</div>'
        if cards
        else '<p class="muted">No charger records available.</p>'
    )

    feed.sort(key=lambda t: t[0])
    feed_html = (
        "".join(html for _, html in feed)
        if feed
        else '<p class="muted">No anomalies in this dataset.</p>'
    )

    # league table (validation report only — per-contractor data quality) ----
    if validation_report and validation_report.per_source:
        league = sorted(
            validation_report.per_source.items(),
            key=lambda kv: (kv[1].avg_score if kv[1].avg_score is not None else -1),
            reverse=True,
        )
        league_rows = [
            (
                esc(src),
                str(stats.records),
                fmt_number(stats.avg_score, 3) if stats.avg_score is not None else "N/A",
                esc(", ".join(f"{c}:{n}" for c, n in sorted(stats.issues.items())) or "—"),
            )
            for src, stats in league
        ]
        league_html = table(["Source / contractor", "Records", "Avg quality score", "Top issues"], league_rows)
    else:
        league_html = '<p class="muted">Validation report not provided — league table data unavailable.</p>'

    # latency tracker (reconciliation report only — Yash's lag figures) ------
    if reconciliation_report and reconciliation_report.per_source_lag_days:
        lag_rows = [
            (esc(src), fmt_number(lag.p50, 1), fmt_number(lag.p95, 1))
            for src, lag in sorted(reconciliation_report.per_source_lag_days.items())
        ]
        lag_html = table(["Source", "Reporting lag p50 (days)", "Reporting lag p95 (days)"], lag_rows)
    else:
        lag_html = '<p class="muted">Reconciliation report not provided — latency tracker data unavailable.</p>'

    return page(
        "Operational Awareness Dashboard",
        "Fleet status, anomalies, contractor quality and reporting latency",
        f"""
<section>
  <h2>At a glance</h2>
  {kpi_grid([
      ("Chargers", f"{k['chargers_deployed']} ({k['active_chargers']} active)", "deployed (active)"),
      ("Sessions", fmt_number(k["sessions_count"], 0), "golden sessions"),
      ("Energy", f"{fmt_number(k['energy_delivered_kwh'], 1)} kWh", "delivered"),
      ("Fleet uptime", fmt_pct(k["est_uptime_pct"]), "mean est. uptime"),
      ("Anomalies", str(k["anomalies_total"]), "all severities"),
      ("Chronic-failure sites", str(k["chronic_failure_sites"]), f"fault recurrence ≥ {CHRONIC_FAILURE_THRESHOLD}"),
  ])}
</section>
<section>
  <h2>Fleet status grid</h2>
  {grid}
  {svg_map(points)}
</section>
<section>
  <h2>Anomaly feed by severity</h2>
  {feed_html}
</section>
<section>
  <h2>Per-contractor data-quality league table</h2>
  {league_html}
</section>
<section>
  <h2>Reporting-latency tracker</h2>
  <p class="muted">Renders Yash's per-source lag figures (P3) only — never recomputed.</p>
  {lag_html}
</section>
""",
        generated_at,
    )


def _anomaly_block(a, host: GoldenRecord) -> str:
    host_label = host.record_type + " " + (host.golden_id or "?")
    return (
        f'<div class="anomaly" style="border-left-color:{_sev_color(a.severity)}">'
        f'{badge(a.severity or "UNKNOWN", _sev_color(a.severity))} '
        f"<strong>{esc(a.type or 'anomaly')}</strong> — {esc(a.detail or '')} "
        f'<span class="muted">· {esc(host_label)}</span><br>'
        f'<span class="muted">evidence: {esc(", ".join(a.evidence) or "—")}</span></div>'
    )


# ---------------------------------------------------------------------------
# M3 — Safety & infrastructure-health view
# ---------------------------------------------------------------------------


def render_safety_view(
    records: Iterable[GoldenRecord],
    kpis: Kpis,
    generated_at: str,
) -> str:
    triage: List[Tuple[int, str]] = []  # (health rank, html) — SAFETY_REVIEW first
    hot: List[Tuple[int, str]] = []  # (severity rank, html) — SAFETY first
    ev_rows: List[Tuple] = []

    for r in records:  # single streaming pass
        if isinstance(r, GoldenCharger):
            state = r.health.state if r.health else None
            since = r.health.since if r.health else None
            evidence = r.health.evidence if r.health else []
            city = r.address.city if r.address and r.address.city else "—"
            triage.append(
                (
                    _health_rank(state),
                    f'<div class="card" style="border-left-color:{_health_color(state)}">'
                    f'<div class="cid">{esc(r.charger_id or r.golden_id or "?")} '
                    f'{badge(state or "UNKNOWN", _health_color(state))}</div>'
                    f'<div class="meta">{esc(city)} · since {esc(since or "N/A")} · '
                    f'status {esc((r.status or "UNKNOWN").upper())}</div>'
                    f'<div class="meta muted">evidence: {esc(", ".join(evidence) or "—")}</div></div>',
                )
            )
        elif isinstance(r, GoldenMaintenance):
            ev_rows.append(
                (
                    esc(r.event_id or "—"),
                    esc(r.event_date or "N/A"),
                    esc(r.event_type or "—"),
                    badge(r.severity or "UNKNOWN", _sev_color(r.severity)),
                    esc(r.charger_id or r.station_id or "—"),
                    esc((r.description or "")[:120]),
                    esc(r.golden_id or "—"),
                )
            )
        for a in r.anomalies:
            if (a.severity or "") in ("SAFETY", "CRITICAL"):
                hot.append((_sev_rank(a.severity), _anomaly_block(a, r)))

    triage.sort(key=lambda t: t[0])
    hot.sort(key=lambda t: t[0])

    triage_html = (
        f'<div class="card-grid">{"".join(html for _, html in triage)}</div>'
        if triage
        else '<p class="muted">No charger records available.</p>'
    )
    hot_html = (
        "".join(html for _, html in hot)
        if hot
        else '<p class="muted">No SAFETY or CRITICAL anomalies in this dataset.</p>'
    )
    ev_html = (
        table(
            ["Event ID", "Date", "Type", "Severity", "Charger", "Description", "Golden ID"],
            ev_rows,
        )
        if ev_rows
        else '<p class="muted">No maintenance events passed through in this dataset.</p>'
    )

    return page(
        "Safety & Infrastructure-Health View",
        "Triage by health state, SAFETY/CRITICAL anomalies, and maintenance evidence",
        f"""
<section>
  <h2>Infrastructure triage (by health state)</h2>
  <p class="muted">Health states computed by P3 (Y4) — rendered verbatim; SAFETY_REVIEW first.</p>
  {triage_html}
</section>
<section>
  <h2>SAFETY &amp; CRITICAL anomalies</h2>
  {hot_html}
</section>
<section>
  <h2>Maintenance evidence (pass-through)</h2>
  {ev_html}
</section>
""",
        generated_at,
    )


# ---------------------------------------------------------------------------
# M4 — Customer-experience view
# ---------------------------------------------------------------------------


def render_cx_view(
    chargers: Iterable[GoldenCharger],
    sessions: Iterable[GoldenSession],
    kpis: Kpis,
    summaries: Sequence[Dict],
    generated_at: str,
) -> str:
    """Availability/reliability by location, chronic-failure sites, summaries.

    Takes chargers and sessions as separate iterables so the caller can stream
    each type independently (two passes over the input file, flat memory).
    """
    # pass 1: chargers → city buckets + charger→city map
    by_city: Dict[str, Dict] = {}
    charger_city: Dict[str, Optional[str]] = {}
    for c in chargers:
        city = (c.address.city if c.address and c.address.city else None)
        charger_city[c.charger_id] = city
        bucket = by_city.setdefault(
            city or "Unknown location", {"chargers": 0, "uptime": [], "energy": 0.0, "sessions": 0}
        )
        bucket["chargers"] += 1
        if c.metrics and c.metrics.est_uptime_pct is not None:
            bucket["uptime"].append(c.metrics.est_uptime_pct)

    # pass 2: sessions → energy/session counts per charger city
    for s in sessions:
        city = charger_city.get(s.charger_id) or "Unknown location"
        bucket = by_city.setdefault(
            city, {"chargers": 0, "uptime": [], "energy": 0.0, "sessions": 0}
        )
        bucket["sessions"] += 1
        if s.energy_kwh is not None:
            bucket["energy"] += s.energy_kwh

    loc_rows = []
    for city, b in sorted(by_city.items(), key=lambda kv: -len(kv[1]["uptime"])):
        mean_up = (sum(b["uptime"]) / len(b["uptime"])) if b["uptime"] else None
        loc_rows.append(
            (
                esc(city),
                str(b["chargers"]),
                fmt_pct(mean_up),
                str(b["sessions"]),
                f"{b['energy']:.1f}",
            )
        )
    loc_html = table(
        ["Location", "Chargers", "Avg availability (est. uptime)", "Sessions", "Energy (kWh)"],
        loc_rows,
    )

    # chronic-failure sites (light records accumulated by Kpis, P3 counts) ----
    if kpis.chronic_failure_sites:
        cf_rows = [
            (
                esc(site["charger_id"]),
                esc(site["city"]),
                str(site["fault_recurrence_count"]),
                esc(site["health_state"]),
            )
            for site in sorted(
                kpis.chronic_failure_sites,
                key=lambda s: -s["fault_recurrence_count"],
            )
        ]
        cf_html = table(
            ["Charger", "Location", "Fault recurrence count", "Health state"], cf_rows
        )
    else:
        cf_html = f'<p class="muted">No sites with fault recurrence ≥ {CHRONIC_FAILURE_THRESHOLD}.</p>'

    # plain-language summaries -------------------------------------------------
    sum_html = ""
    for s in summaries:
        llm_badge = badge("LLM-generated" if s.get("llm_used") else "template", "#1565c0" if s.get("llm_used") else "#9e9e9e")
        sum_html += (
            f'<div class="anomaly"><h3 style="margin:0 0 6px">{esc(s.get("title", ""))} {llm_badge}</h3>'
            f'<p style="margin:0">{esc(s.get("text", ""))}</p></div>'
        )

    return page(
        "Customer-Experience View",
        "Availability by location, chronic-failure sites, plain-language summaries",
        f"""
<section>
  <h2>Availability &amp; reliability by location</h2>
  {loc_html}
</section>
<section>
  <h2>Chronic-failure sites</h2>
  <p class="muted">Fault recurrence count (P3 metrics) ≥ {CHRONIC_FAILURE_THRESHOLD}.</p>
  {cf_html}
</section>
<section>
  <h2>Plain-language summaries</h2>
  {sum_html}
</section>
""",
        generated_at,
    )


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _sev_rank(severity: Optional[str]) -> int:
    return SEVERITY_ORDER.index(severity) if severity in SEVERITY_ORDER else len(SEVERITY_ORDER)


def _health_rank(state: Optional[str]) -> int:
    return HEALTH_ORDER.index(state) if state in HEALTH_ORDER else len(HEALTH_ORDER)


def _health_color(state: Optional[str]) -> str:
    from .render import HEALTH_COLORS

    return HEALTH_COLORS.get(state, HEALTH_COLORS[None])


def _sev_color(severity: Optional[str]) -> str:
    from .render import SEVERITY_COLORS

    return SEVERITY_COLORS.get(severity, SEVERITY_COLORS["UNKNOWN"])
