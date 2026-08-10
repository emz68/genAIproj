# Pipeline demo — real run, real outputs

Demonstration package from the full pipeline run of **2026-08-10** on merged `main`
(22,228 raw records, three sources, three formats → 21,100 golden records in ~40 s, offline).

- **`pipeline_demo.html`** — the presentation infographic: inputs, the four-stage flow with a
  real record traced through every stage, outputs, and scale numbers. Open directly in a browser.
- **`outputs/`** — actual artifacts of the run:
  - `psc_compliance_report.csv` / `.html` — the regulatory report with the data-lineage appendix
  - `customer_experience.html` — availability/reliability view
  - `dashboard.html.gz`, `safety_view.html.gz` — operational dashboard and safety triage (gunzip to view)
  - `validation_report.json`, `reconciliation_report.json`, `pipeline_health.json` — the §5.5 stage reports
  - `golden_records_sample.jsonl` — first 50 golden records (the full 20 MB file is reproducible, not committed)

## The one-record story (for the presentation)

A contractor's free-text inspection form (`data/seed/contractor_reports/report_004_inspection.txt`):

```
insp.date : 27.11.2022
findings -> connector latch broken; cosmetic scratches
severity code [urgent-safety]
followup req'd: N
```

1. **Ingestion (P1)** extracts a typed `maintenance` record: `event_type: INSPECTION`,
   `severity: SAFETY` (from "urgent-safety"), extraction confidence 0.95 — values kept verbatim.
2. **Validation (P2)** fixes `27.11.2022 → 2022-11-27` (logged in `fixes_applied`), flags
   `stale_report`, scores the record 0.85.
3. **Reconciliation (P3)** links it to its charger and assigns a stable `golden_id`.
4. **Reporting (P4)** puts it on the safety triage list — one of 10 SAFETY events the pipeline
   surfaced from free text, despite the contractor marking "followup req'd: N".

## Reproduce

```bash
pip install -r requirements.txt
mkdir -p data/raw && cp data/seed/*.gz data/raw/ && cp data/seed/contractor_reports/*.txt data/raw/
python -m src.platform.run --pipeline full --config src/platform/config.yaml --no-llm
```

Scale run (~124k records): generate with
`python -m src.ingestion.inject --in data/seed --out data/raw --seed 42 --scale 2500` first.
