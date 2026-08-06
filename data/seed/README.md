# Seed dataset (frozen on `main` — do not edit on branches)

The committed project dataset, per `PRODUCT_DEVELOPMENT_MANAGER.md` §7 P1. Three sources describing an overlapping set of EV chargers, in three formats, reproducing the Con Edison multi-source/multi-format situation. Downloaded 2026-08-06.

| File | Source | What it is |
|---|---|---|
| `ev_charging_sessions_cary.csv.gz` | Town of Cary, NC open data portal (`data.townofcary.org`, dataset `electric-vehicle-charging-stations`) | **Source A** — 20,142 real charging sessions (2012–2023): start timestamp, station name, charging time, energy kWh, address. Semicolon-delimited, UTF-8 BOM. |
| `afdc_stations_nc_elec.geojson.gz` | U.S. DOT/BTS ArcGIS mirror of NREL's Alternative Fuels Data Center station registry | **Source B** — 2,062 public electric charging stations in North Carolina (GeoJSON): names, coordinates, connector types, EVSE counts, networks, open dates. Overlaps Source A's chargers → what P3 reconciles against. |
| `contractor_reports/*.txt` | Synthetic (deterministic, seed 42) | **Source C** — 24 messy semi-structured maintenance/install/fault/complaint reports referencing real Source A/B stations. Seed *examples* of the format; Emlyn's messiness injector (E5) must be able to regenerate and extend this set at scale. |

Licensing: Source A per the Town of Cary open data portal terms; Source B is U.S. Government open data (public domain); Source C is synthetic.

## Populating the working directory

Pipelines read from `data/raw/` (gitignored). Initialize it from the seed:

```bash
mkdir -p data/raw && cp data/seed/*.gz data/raw/ && cp data/seed/contractor_reports/*.txt data/raw/
```

Readers must handle `.gz` transparently (see §7 E2) — do not decompress into the repo.

Refreshing from the live sources and generating messy variants at scale is Emlyn's job — see `data/README.md` (E1) on the `emlyn` branch.
