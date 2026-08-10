# Data sources & refresh instructions (P1 — Emlyn)

This project simulates the Con Edison Use Case 3 data situation: **three
complementary sources, three formats, overlapping entities**, arriving late,
inconsistent and messy. The committed baseline lives in `data/seed/` (frozen
on `main`); pipelines read from `data/raw/` (gitignored, never committed).

## Source A — charging sessions (structured CSV)

- **Live source:** Town of Cary, NC open data portal — `data.townofcary.org`,
  dataset **"Electric Vehicle Charging Stations"** (`electric-vehicle-charging-stations`).
  Refresh by downloading the dataset export (CSV) from the portal and saving
  it as `data/seed/ev_charging_sessions_cary.csv.gz`.
- **Shape:** semicolon-delimited, UTF-8 BOM, CRLF; header
  `start_date;station_name;charging_time_hh_mm_ss;energy_kwh;address_1;address_2;city;state_province;zip_postal_code`.
  `start_date` is ISO-8601 with timezone; `charging_time_hh_mm_ss` is a clock
  duration. No session/charger IDs — the station name + address are the
  reconciliation anchors for P3.
- **Count (committed seed):** 20,142 rows (2012–2023).

## Source B — station registry (API/GeoJSON)

- **Live source:** U.S. DOT/BTS ArcGIS mirror of NREL's Alternative Fuels Data
  Center station registry (NC electric stations). Refresh by exporting the
  NC electric-station layer as GeoJSON from the AFDC/BTS mirror and saving it
  as `data/seed/afdc_stations_nc_elec.geojson.gz`.
- **Shape:** `FeatureCollection` of `Point` features; ~80 properties per
  station. The P1 reader keys on `ev_charging_units` (a JSON-string list of
  EVSE dicts with per-connector `port_count`/`power_kw`/`charging_level`) to
  emit **one canonical charger record per physical port**. `open_date` /
  `date_last_confirmed` arrive as epoch milliseconds — passed through
  verbatim (normalization is P2's job).
- **Count (committed seed):** 2,062 stations. Overlaps Source A's chargers —
  this is what P3 reconciles.

## Source C — contractor reports (unstructured free text)

- **Seed:** 24 deterministic synthetic reports in `data/seed/contractor_reports/`
  (email, automated fault digest, inspection form, install report, 311
  complaint). They reference real Source A/B station names and plant the
  project's messiness: mixed date formats, unit notes ("NOTE units in W not
  kW"), severity vocab (`fyi` / `minor` / `MAJOR` / `urgent-safety` /
  `safety!!`), multi-event digests.
- **Regenerate/extend:** the messiness injector (E5) regenerates a
  seed-sized set and can extend it at arbitrary volume:

```bash
python -m src.ingestion.inject --in data/seed --out artifacts/injected --seed 42 --scale 1
```

  Deterministic: same `--seed` + same inputs ⇒ byte-identical output.
  `--scale N` multiplies synthetic volume (digest megafiles, ~1000 events
  each); `--scale 2500` yields ≥100k maintenance events for the §8 scale run.

## Populating the working directory

Pipelines read from `data/raw/` (gitignored). Initialize it from the seed:

```bash
mkdir -p data/raw && cp data/seed/*.gz data/raw/ && cp data/seed/contractor_reports/*.txt data/raw/
```

Readers handle `.gz` transparently (sniffed by magic bytes, not extension) —
do not decompress into the repo.

## Scale run (Integration Manager, §8 step 5)

```bash
# 1. generate >=100k records of messy data (deterministic)
python -m src.ingestion.inject --in data/seed --out artifacts/injected --seed 42 --scale 2500

# 2. swap into the working dir (late-arriving A batch included)
rm -rf data/raw && mkdir -p data/raw && cp artifacts/injected/* data/raw/

# 3. run the pipeline stages as usual — ingestion streams, memory stays flat
python -m src.ingestion.run --in data/raw --out artifacts/canonical.jsonl --no-llm
```

The injector's late-arriving batch (`ev_charging_sessions_cary_late.csv.gz`)
exercises the orchestrator's manifest invalidation (A1): changing
`data/raw/` contents forces re-runs of affected stages.

## Contract notes

- Territory: the default territory is **North Carolina** (Cary/Wake County
  for A, NC-wide for B). Territory never appears as a code constant — P2's
  bounding box lives in its YAML config and is swapped by config alone if an
  official Con Ed dataset lands (see PRODUCT_DEVELOPMENT_MANAGER.md §7 P2 S1).
- If organizers supply an official dataset, drop it in `data/raw/` — the
  pipeline contracts are dataset-agnostic; only the P1 readers change.
