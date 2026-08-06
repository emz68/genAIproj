# src/reporting — P4 Reporting & Experience (Emily)

Consumes **golden records** (§5.4, P3 output) plus the three **optional §5.5
report files** and renders the four P4 deliverables. This module **renders
only** — every availability, latency, and quality figure displayed here was
computed by an earlier stage (Sanja P2 / Yash P3 / Anastasia P5) and is
surfaced verbatim (§7 boundary rules).

## CLI (§6 contract)

```bash
python -m src.reporting.run --in <golden.jsonl> --out artifacts/reports/ \
    [--validation-report <path>] [--reconciliation-report <path>] \
    [--pipeline-health <path>] \
    [--restates <date>] [--prior-kpis <path>] \
    [--no-llm] [--limit N] [--log-json] [--report-date YYYY-MM-DD]
```

| Flag | Meaning |
|---|---|
| `--in` | golden.jsonl (§5.4), streamed line-by-line (never whole-file) |
| `--out` | output directory (created with `mkdir -p`) — gets 5 files |
| `--validation-report` / `--reconciliation-report` / `--pipeline-health` | optional §5.5 reports; absent → sections render "data unavailable" |
| `--restates <date>` + `--prior-kpis <path>` | **restated-report mode** (M1): banner "restates report of \<date\>" + KPI delta section |
| `--no-llm` | deterministic template summaries; no API calls (tests always use this) |
| `--limit N` | read at most N golden records (reporting emits files, so the cap bounds what is rendered); harness asserts ≤ N |
| `--log-json` | JSON log lines; final stderr line is the `{"metrics": …}` protocol line; failures exit nonzero with a final `{"error": true, …}` line |
| `--report-date` | pins the report date (deterministic snapshots) |

**Outputs** (all under `--out`, all non-empty):

| File | Deliverable |
|---|---|
| `psc_compliance_report.csv` / `.html` | M1 — PSC compliance report: KPIs + data-lineage appendix (per-stage counts, quality-score distribution, quarantine summary, lag figures) + optional restatement |
| `dashboard.html` | M2 — operational awareness dashboard: fleet status grid + data-driven SVG map, anomaly feed by severity, per-contractor league table, reporting-latency tracker |
| `safety_view.html` | M3 — safety & infrastructure-health view: triage by health state, SAFETY/CRITICAL anomalies, maintenance evidence |
| `customer_experience.html` | M4 — availability by location, chronic-failure sites, plain-language summaries (LLM or `--no-llm` templates) |

## KPI definitions (hand-verifiable, fixture truth)

Computed from golden records as **pure aggregation** — see `kpis.py`:

| KPI | Definition | Fixture value |
|---|---|---|
| Chargers deployed / active | count of golden charger records / `status == ACTIVE` | 6 / 4 |
| Sessions / energy | count of golden sessions / Σ `energy_kwh` | 8 / 100.00 kWh |
| Est. fleet uptime | mean of per-charger `metrics.est_uptime_pct` (Yash) — **never recomputed** | 83.7% (5.02/6 × 100) |
| Data completeness | mean of `quality.score` (Sanja) over charger+session records | 91.0% (12.74/14 × 100) |
| Chronic-failure sites | `metrics.fault_recurrence_count ≥ 3` (Yash's counts) | 1 (C004) |

Note: `est_uptime_pct` is stored as a **fraction** per §5.4
(1 − outage-days/days-in-period); display multiplies by 100. The restatement
delta aligns prior-KPI percentage points with these fractions.

## LLM usage

`summaries.py` calls the Claude API (`ANTHROPIC_API_KEY` from the environment,
never committed) only when `--no-llm` is absent; it is asked to paraphrase
figures, never to compute. Any failure falls back to deterministic templates,
and `llm_calls` in the metrics line reflects real attempts. All tests run
offline with `--no-llm`.

## Development

```bash
uv venv .venv && uv pip install --python .venv/Scripts/python.exe -r requirements.txt pytest
.venv/Scripts/python.exe -m pytest src/reporting -q        # 47 tests, offline
.venv/Scripts/python.exe -m src.reporting.tests.update_snapshots   # regenerate HTML snapshots after intentional render changes
```

Tests cover: hand-computed KPIs, CSV/HTML content, **HTML snapshots**,
models (§5.0 extra=allow / absent==null), and the **§6 protocol** (metrics
line, error line, `--limit`, `--log-json`) via subprocess.
