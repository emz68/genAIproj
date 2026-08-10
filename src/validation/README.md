# src/validation — Phase P2 · Validation & Cleaning (Sanja)

One record in, one record out. Consumes canonical JSONL (PDM §5.1–5.3, P1
raw-string form — the module's own fixtures during parallel development),
and emits:

- `validated.jsonl` — every line normalized to the **P2** targets: §5 enums
  (`J1772|CCS|CHAdeMO|NACS|OTHER`, `L1|L2|DCFC`, `ACTIVE|INACTIVE|MAINTENANCE|UNKNOWN`,
  `INSPECTION|REPAIR|FAULT|INSTALL|COMPLAINT|OTHER`, `INFO|MINOR|MAJOR|SAFETY`),
  ISO-8601 dates/datetimes with timezone, numeric types, clean address/zip.
- `validation_report.json` — exactly per PDM §5.5.

**Boundary rules honored (PDM §7):** no cross-record logic of any kind —
duplicate rows pass through untouched and unflagged (Yash owns dedup); no
raw-file parsing (Emlyn owns ingestion); staleness is per-record only (Yash
owns lag aggregates); derived metrics and health states are never computed
here.

## CLI (PDM §6)

```bash
python -m src.validation.run --in canonical.jsonl \
    --out artifacts/validated.jsonl --report artifacts/validation_report.json \
    [--no-llm] [--limit N] [--log-json] [--rules rules_override.yaml]
```

- `--no-llm` — deterministic synonym-table fallback; zero LLM calls.
- `--limit N` — emit at most N output records, then stop (0 = unlimited).
- `--log-json` — stderr log lines are JSON (`"log": true`); the final stderr
  line is `{"metrics": {records_in, records_out, llm_calls, llm_tokens}}`.
  On failure: nonzero exit and a final `{"error": true, "stage":
  "validation", "message", "detail"}` line.
- `--rules` — optional YAML override, deep-merged onto `rules.yaml`.
- Streaming: records are processed line-by-line; only counters accumulate,
  so memory stays flat on large inputs.

## How a record is processed

1. **Standardize (S2)** — dates → ISO with timezone (naive values get
   `America/New_York`, configurable), unit repair (W→kW, Wh→kWh via magnitude
   heuristics), enums via the YAML synonym tables, zip/state/address cleanup,
   whitespace/casing. Every change is logged in `quality.fixes_applied`.
2. **Derive missing (S3)** — `duration_min` from `start_time`/`end_time`;
   `level` from `power_kw` thresholds. Conservative only; nothing is invented.
3. **Fix contradictions (S4)** — `duration_min` is recomputed from timestamps
   when it contradicts them.
4. **LLM repair (S5)** — unresolved categoricals are offered to Claude
   (`ANTHROPIC_API_KEY`, never committed) when not `--no-llm`; replies are
   validated against the enum; any failure falls back to the deterministic
   path. Calls/tokens are reported in the metrics line.
5. **Rules (S1)** — the declarative catalog in `rules.yaml` produces the
   record's final stable issue codes.
6. **Model-safe finalization** — values the P2 models cannot hold (unparseable
   numbers/dates, enums without a catch-all member) are dropped and stashed
   as `raw_<field>` extras — nothing is lost.
7. **Score (S6, §5.0)** — P1 `quality.score` moves to
   `quality.extraction_confidence`; `score` is recomputed as
   `confidence − Σ(issue weights per unique code)`, clamped to [0, 1].
   Records below the quarantine threshold are counted in the report but
   **always emitted** — never dropped.

## Rules as data (`rules.yaml`)

Everything behavioral lives in the YAML: the territory bounding box (shipped
defaulted to **North Carolina**; swap by config alone if an official Con Ed
dataset lands), required fields per record type, numeric/range/enum/date/
order/duration/stale/bbox checks, the 7-day staleness threshold, unit-repair
magnitudes, derive thresholds, synonym tables, scoring weights and the
quarantine threshold. Changing behavior never requires touching code.

## Conventions worth knowing

- Dotted dates (`20.12.2022`) are day-first (contractor-report style);
  slashed dates are month-first; naive datetimes get the default timezone.
- Enum resolution: longer synonyms win within a list (`urgent-safety` beats
  `urgent`), table order decides between enums (SAFETY before MAJOR);
  unresolvable values map to `OTHER`/`UNKNOWN` where the enum has a catch-all,
  otherwise the value is flagged (`invalid_<field>`) and stashed as
  `raw_<field>`.
- `quality.issues` from ingestion are replaced, not merged; the extraction
  confidence carries the ingestion history forward.
- Required provenance fields come from P1; a record missing them is flagged
  (`missing_provenance_*`), marked `model_validation_failed`, and still
  emitted with a low score.

## Tests & fixtures

```bash
python -m pytest src/validation/tests -q      # from the repo root
```

- `fixtures/canonical_messy.jsonl` — 24 adversarial P1-style records (messy
  dates, W/Wh units, synonym chaos, impossible values, stale reports,
  out-of-territory coordinates, planted duplicates, missing provenance).
- `fixtures/expected_validated.jsonl` — the verified expected output.
- `fixtures/rules_override.yaml` — a minimal config override proving the
  territory is swapped by config alone.

Dependencies are declared in `requirements.txt` (unioned into the root file
by the Integration Manager at merge time, PDM §8 step 3).
