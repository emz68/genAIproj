# src/ingestion — P1 Data & Ingestion (Emlyn)

Ingests the three project sources (messy, multi-format, overlapping) and
emits **canonical JSONL** conforming to the frozen schema
(PRODUCT_DEVELOPMENT_MANAGER.md §5.1–5.3) with P1 semantics (§5.0): all
source values pass through **verbatim as free strings** — no date, unit, or
enum normalization. Field *placement* is this module's job; value
*normalization* is P2's.

## CLI (frozen contract §6)

```bash
python -m src.ingestion.run --in data/raw/ --out artifacts/canonical.jsonl --no-llm
```

Flags: `--no-llm` (deterministic regex fallback — no API calls, all tests
offline), `--limit N` (emit at most N output records, files processed in
sorted-name order), `--log-json` (§6 stderr protocol: JSON log lines, final
line `{"metrics": {...}}` on success / `{"error": true, ...}` + nonzero exit
on failure). Every stage `mkdir -p`'s its own output directory. Processing is
streaming — one record written per line, nothing held in memory (§2.3).

## File map

| File | Role |
|---|---|
| `models.py` | P1 pydantic models (§5.1–5.3), `extra="allow"`, free-string values |
| `readers.py` | format auto-detection + streaming readers (CSV/GeoJSON/text, `.gz` by magic bytes) |
| `extract.py` | Source C maintenance-event extraction: Claude path (E3) + regex `--no-llm` fallback |
| `run.py` | stage CLI: assembles canonical records, provenance, quality, §6 protocol |
| `inject.py` | messiness injector (E5): regenerate/extend C, degrade A/B, `--seed`/`--scale` |
| `fixtures/` | small samples of every input format + expected canonical output |
| `tests/` | unit tests (offline) — `test_ingestion_*.py` |

## Design decisions (read before touching)

- **One charger record per physical port (Source B).** The GeoJSON
  `ev_charging_units` structure (JSON-string list of EVSE dicts with
  per-connector `port_count`/`power_kw`) is expanded per port; stations
  without it yield one record carrying the raw `ev_connector_types` list.
  Ports get a deterministic `charger_id` (`<station>#<unit>#<connector>#<port>`);
  `station_id` = the AFDC feature `id` (string).
- **Derived `session_id` (Source A).** Source A has no session IDs, so P1
  derives `cary-<sha1(source_file:line)[:12]>` — deterministic, stable across
  re-runs, and unique enough for P3 dedup. Documented here so nobody mistakes
  it for a source value. Station name + address columns are preserved as
  extra fields (verbatim) because P3 reconciles A→B on them.
- **Maintenance events.** One file can yield many events (fault digests list
  one entry per station). `event_id` = `maint-<sha1(filename, index)>`.
  `event_type`/`severity` are chosen as *extraction* decisions to the nearest
  canonical label (`sev=fyi` → INFO, `safety!!` → SAFETY); every extracted
  value itself stays verbatim. `extracted_fields` carries whatever labeled
  facts the report has (rating, count, notes, routed_to, …). Severity and
  type vocabularies per §5.3.
- **Confidence → `quality.score`.** Ingestion writes `score` = parsing /
  extraction confidence (§5.0); `extraction_confidence` stays null until P2
  moves the value there. Structured CSV/GeoJSON rows score 1.0 (0.7 when
  `malformed_row`); regex-extracted maintenance events score 0.4–0.95 by a
  deterministic heuristic; LLM events use the model's reported confidence.
- **LLM path.** `parse_maintenance_llm` uses the anthropic SDK
  (`ANTHROPIC_API_KEY` from env, model overridable via `ANTHROPIC_MODEL`).
  On any LLM failure the dispatcher falls back to the regex path and tags
  the records with the `llm_fallback` issue. `--no-llm` never touches the
  network. `llm_calls`/`llm_tokens` are reported in the §6 metrics line.
- **Injector output** goes to `artifacts/injected/` (gitignored) — never
  directly into `data/raw/`; the Integration Manager swaps it in for the
  scale run (§8 step 5, see `data/README.md`).

## Dev setup & tests

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r src/ingestion/requirements.txt
.venv/bin/python -m pytest src/ingestion -q     # offline
```

Definition of Done: `python -m src.ingestion.run --in data/raw
--out artifacts/canonical.jsonl --no-llm` produces P1-schema-valid JSONL
covering all three sources; §6 flags/stderr protocol honored; tests pass
offline; module requirements.txt complete.
