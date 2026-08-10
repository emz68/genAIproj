# Pipeline demo — real run, real outputs

Demonstration package from the full pipeline run of **2026-08-10** on merged `main`
(258,343 raw records — the official NYC DOT municipal-garage dataset plus the AFDC New York registry and contractor reports — → 247,886 golden records, offline).

- **`pipeline_demo.html`** — the presentation infographic: inputs, the four-stage flow with a
  real record traced through every stage, outputs, and scale numbers. Open directly in a browser.
- **`outputs/`** — actual artifacts of the run:
  - `psc_compliance_report.csv` / `.html` — the regulatory report with the data-lineage appendix
  - `customer_experience.html` — availability/reliability view
  - `dashboard.html.gz`, `safety_view.html.gz` — operational dashboard and safety triage (gunzip to view)
  - `validation_report.json`, `reconciliation_report.json`, `pipeline_health.json` — the §5.5 stage reports
  - `golden_records_sample.jsonl` — first 50 golden records (the full 20 MB file is reproducible, not committed)

## The one-record story (for the presentation)

A contractor's free-text 311 complaint (`data/seed/contractor_reports_nyc/nyc_report_006_complaint.txt`):

```
customer complaint via 311, logged 2025-06-24
location given: "JON - Jerome 190th Street Municipal Parking"
charge box EVB-P2042308: screen dead
routed to: J. Alvarez  priority: urgent-safety
```

1. **Ingestion (P1)** extracts a typed `maintenance` record: `event_type: COMPLAINT`,
   `severity: SAFETY` (from "urgent-safety"), the garage name pulled from the quoted text.
2. **Validation (P2)** normalizes dates/units across the run (e.g. session timestamps
   `08/21/2025 19:10:54.0000000 → 2025-08-21T19:10:54-04:00`, correct NY DST offset),
   flags issues, logs every fix inside the record.
3. **Reconciliation (P3)** assigns a stable `golden_id`; across the run it removed 10,457
   duplicates, resolved 16,157 field conflicts, and found 6,765 duplicate-billing patterns.
4. **Reporting (P4)** puts it on the safety triage list, one such event flagged as having
   no follow-up repair on record.

## Reproduce

```bash
pip install -r requirements.txt
mkdir -p data/raw
cp data/seed/nyc_ev_charging_municipal_lots_garages.csv.gz data/seed/afdc_stations_ny_elec.geojson.gz data/raw/
cp data/seed/contractor_reports_nyc/*.txt data/raw/
python -m src.platform.run --pipeline full --config src/platform/config.yaml --no-llm
```

Timing at this volume: ingestion 2.3 s, validation 28 s, reporting 10 s; reconciliation
73.6 min (known bottleneck, punch-listed to P3 — entity-resolution blocking).
