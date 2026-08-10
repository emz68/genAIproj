# src/platform — P5: Platform & Integration Harness (Anastasia)

The glue that turns the five §6 stage CLIs into one pipeline, plus the
contract harness and the §8 merge tooling. Built against fixtures and stubs
only — no imports from the other four modules (§2.2).

## Running the pipeline

```bash
python -m src.platform.run --pipeline full --config src/platform/config.yaml
```

Useful flags: `--use-stubs` (run the A6 stubs instead of the real modules —
required before the merge), `--no-llm`, `--limit N`, `--log-json` (all three
propagate to every stage per §6), `--no-resume` (ignore the manifest),
`--raw-dir` / `--artifacts-dir` (path overrides for tests). Run from the
repo root.

## What's here

| Piece | Task | Notes |
|---|---|---|
| `run.py`, `orchestrator.py` | A1 | Subprocess execution of the §6 commands, output→input wiring (incl. reporting's three `--*-report` flags), retries, per-stage timing |
| `manifest.py` | A1 | Resumability: a stage is skipped only if its inputs digest, argv digest, and recorded outputs are all unchanged. Ingestion's input digest covers all of `data/raw/`, so **late-arriving files invalidate ingestion and everything downstream** |
| `config.py`, `config.yaml` | A2 | Entrypoint templates (`{python}` → current interpreter), `ANTHROPIC_API_KEY` handling (missing key ⇒ warn + downgrade to `--no-llm`), report-only `.gitignore` check |
| `health.py` | A3 | Consumes each stage's final-stderr metrics/error line (§6 protocol) into `artifacts/pipeline_health.json` (§5.5 shape, rewritten after every stage so the reporting stage receives a valid snapshot) + console summary |
| `../../tests/integration/` | A4 | Contract harness: flags, exit codes, final-line protocol, schema-valid outputs, broken-input behavior, e2e + resumability. Auto-detects real modules vs stubs (`find_spec("src.<stage>.run")`) |
| `merge_readiness.py` | A5 | `python -m src.platform.merge_readiness --all` — verifies each branch touches only its §4-owned paths. The human's §8 step-2 gate |
| `stubs/` | A6 | Trivial §6-compliant pass-through stages so everything above runs end-to-end pre-merge. Confined to `src/platform/stubs/` |
| `smoke.sh` | A4 | `src/platform/smoke.sh` — tests + end-to-end run (stubs auto-selected if the real modules aren't merged yet) |
| `schemas.py` | — | Local re-declaration of §5 (raw / validated / golden tiers, §5.5 report shapes) per the no-cross-module-imports rule |

## Platform's own §6 compliance

`run.py` is itself a stage: exit 0 with a final stderr `{"metrics": ...}`
line (records_in = ingestion's, records_out = reconciliation's, LLM totals
summed) or nonzero with a final `{"error": true, "stage": "platform", ...}`
line whose `detail.failed_stage` / `detail.stage_error` carry the §6 error
object of the stage that failed.

## For the Integration Manager (human — §2.1 rule 4)

Merge-time sequence per §8: `merge_readiness --all` → union module
`requirements.txt` files → merge in order → `src/platform/smoke.sh` after
each merge. This tooling is built by the agent but **executed only by you**.
