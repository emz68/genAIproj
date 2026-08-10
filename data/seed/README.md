# Seed dataset (frozen on `main` — do not edit on branches)

The committed project dataset, per `PRODUCT_DEVELOPMENT_MANAGER.md` §7 P1.

## The official dataset (Con Edison territory — primary since 2026-08-10)

| File | Source | What it is |
|---|---|---|
| `nyc_ev_charging_municipal_lots_garages.csv.gz` | NYC Open Data / NYC DOT, "Electric Vehicle (EV) Charging Data — Municipal Lots and Garages" | **Source A (official)** — 252,589 real charging sessions (2021–2026) at 162 charge boxes across 24 municipal garages: MM/DD/YYYY dates, time-of-day connect/disconnect (sessions cross midnight), decimal-string durations, literal `"NULL"` markers, session status + invalidity taxonomy. Quoted comma CSV. |
| `afdc_stations_ny_elec.geojson.gz` | U.S. DOT/BTS ArcGIS mirror of NREL's Alternative Fuels Data Center registry | **Source B (official)** — 5,740 public electric charging stations in New York State (GeoJSON): names, coordinates, connectors, networks. What P3 reconciles the NYC sessions against. |
| `contractor_reports_nyc/*.txt` | Synthetic (deterministic, seed 7) | **Source C (official)** — 14 messy contractor reports referencing the real municipal garages and charge-box IDs. |

## The pilot dataset (North Carolina — kept for fixtures, tests, and the injector)

| File | Source | What it is |
|---|---|---|
| `ev_charging_sessions_cary.csv.gz` | Town of Cary, NC open data portal | 20,142 real sessions (2012–2023), semicolon CSV, UTF-8 BOM. |
| `afdc_stations_nc_elec.geojson.gz` | BTS/AFDC mirror | 2,062 NC electric stations (GeoJSON). |
| `contractor_reports/*.txt` | Synthetic (seed 42) | 24 messy contractor reports referencing the NC stations. |

Licensing: NYC DOT and BTS/AFDC data are U.S. government open data; Cary per the Town of Cary portal terms; contractor reports are synthetic.

## Populating the working directory

Pipelines read from `data/raw/` (gitignored). For the **official Con Ed-territory run**:

```bash
mkdir -p data/raw
cp data/seed/nyc_ev_charging_municipal_lots_garages.csv.gz data/seed/afdc_stations_ny_elec.geojson.gz data/raw/
cp data/seed/contractor_reports_nyc/*.txt data/raw/
```

The platform config points validation at the Con Ed NY territory override
(`src/platform/territory_coned_ny.yaml`); the NC pilot set uses the module default instead.

Readers must handle `.gz` transparently (see §7 E2) — do not decompress into the repo.

Refreshing from the live sources and generating messy variants at scale is Emlyn's job — see `data/README.md` (E1) on the `emlyn` branch.
