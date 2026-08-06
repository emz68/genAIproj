# Product Development Manager — EV Charger Data Platform

**Project:** AI-powered pipeline to clean, standardize, validate, reconcile, and report on messy EV-charger data at scale (Con Edison Use Case 3).
**Team:** Emlyn, Sanja, Yash, Emily, Anastasia — 5 people, 5 phases, all executed **simultaneously** on separate branches.
**This file is the single source of truth for how work is divided, how agents must behave, and how the branches merge.** Every agent working on this project must be given this file as context and must obey the Global Rules below. This document was adversarially reviewed against requirement coverage, task overlap, merge safety, and blind executability before being frozen.

---

## 1. Background

Con Edison runs EV-charger programs where chargers are installed and managed by external contractors. Data arrives through third-party integrators and is frequently **late, incomplete, inconsistent, and unstructured**, in **multiple formats from multiple sources**. Today, cleansing, reconciliation, structuring, and reporting are manual, causing delays that jeopardize strict **PSC (Public Service Commission) mandates** to report accurately and on time.

**The challenge:** design an AI-powered solution that

1. Handles unstructured / inconsistent inputs (NLP/LLM parsing of semi-structured data)
2. Identifies and fixes missing or incorrect data (validation pipelines)
3. Detects anomalies
4. Automatically reconciles across sources into a reliable **single source of truth**
5. Reports out — covering **safety, infrastructure health, operational awareness, and customer experience**

---

## 2. Global Rules (binding for every person and every agent)

### 2.0 Phase 0 — bootstrap (already done on `main`; humans only)

Before any branch work starts, `main` must contain (and now does): this file, `.gitignore` (covering `data/raw/`, `artifacts/`, `.env`, `__pycache__/`), a seed `requirements.txt`, `pyproject.toml` (pytest configured with `--import-mode=importlib` and `pythonpath=["."]`), `src/__init__.py`, and an empty `__init__.py` in each of the five module directories. The five branches `emlyn`, `sanja`, `yash`, `emily`, `anastasia` are created from this `main` by the **Integration Manager (a human)** and pushed. **Agents never create branches and never check out `main`.**

### 2.1 Git rules — read carefully

1. **One branch per person, named exactly after them** (lowercase): `emlyn`, `sanja`, `yash`, `emily`, `anastasia`. The branches already exist (Phase 0). Each agent starts with:
   ```bash
   git fetch origin && git checkout <yourname>
   ```
2. **Agents NEVER merge. Ever.** No agent may run `git merge`, `git rebase`, `git cherry-pick`, or `gh pr merge`, click a merge button, or otherwise combine branches. Agents never push to `main`, never force-push, never delete branches, never create additional branches, never fetch or read content from another person's branch, and never open PRs unless explicitly told to open a **draft** PR for visibility (still never merged by an agent). If a pull is ever needed, it is `git pull --ff-only` on the agent's own branch.
3. Agents commit and push **only to their own person's branch**, and touch **only the paths owned by their person** (§4 ownership map). Files owned by `main` (this file, root `README.md`, root `requirements.txt`, `.gitignore`, `pyproject.toml`, all seeded `__init__.py` files) must not be edited on any branch — propose changes in `docs/proposals/<yourname>.md` instead.
4. **A single human — the Integration Manager (Anastasia by default) — performs all merging manually at the end**, following §8. Nobody else merges anything. **Anastasia's agent builds merge tooling (A4, A5) but never executes §8; every §8 command is typed by the human.**
5. Commit early and often, messages prefixed by phase, e.g. `[P3] add cross-source reconciler for session records`.

### 2.2 Parallelism rules — why nobody waits on anybody

- The **canonical schema (§5) and the CLI contract (§6) are frozen**. Build against them, not against another person's code.
- Every module must run **standalone** using fixture files its owner creates in their own directory. If your input normally comes from another phase, hand-craft small fixtures conforming to §5 and develop against those.
- Because directory ownership is disjoint (§4) and all shared root files are frozen, the five branches merge into `main` **without conflicts by construction**.

### 2.3 Engineering conventions

- Python 3.11+, `pandas`, `pydantic` (schema enforcement), `pytest`. LLM calls use the Claude API via `ANTHROPIC_API_KEY` from the environment — **never commit keys**. Every module that uses an LLM must have a `--no-llm` deterministic fallback so all tests run offline.
- **Dependencies:** root `requirements.txt` is frozen. Each module declares every third-party package it imports in its own `src/<module>/requirements.txt` (an owned path). Declaring these is part of every phase's Definition of Done; the Integration Manager unions them into the root file at merge time (§8 step 3).
- **Streaming, not whole-file loads:** every stage must process JSONL line-by-line / in chunks so the pipeline scales to large volumes; do not read entire datasets into memory.
- Every stage creates its own output directory (`mkdir -p`); never rely on committed placeholder dirs.
- Every phase ships: working code, unit tests (`src/<module>/tests/`, files named `test_<module>_*.py`), fixtures, and a `README.md` inside its module directory.
- All pipeline artifacts go to `artifacts/` (gitignored). Nothing under `artifacts/` or `data/raw/` is ever committed.

---

## 3. Team, Phases, and Agent Setup (decided)

Each person runs **one lead agent session** (e.g., Claude Code) checked out on their branch. The lead agent is the **only committer** on that branch — this avoids intra-branch conflicts. "Team" below means the lead delegates to specialist subagents and integrates their output itself; "Single agent" means one agent, no delegation.

| Person | Phase | Scope (one line) | Agent setup (decided) |
|---|---|---|---|
| **Emlyn** | P1 — Data & Ingestion | Acquire datasets, generate messy variants, parse all formats → canonical records | **Team** (lead + parser-builder + adversarial messy-input tester) — parsing breadth and adversarial input generation parallelize well |
| **Sanja** | P2 — Validation & Cleaning | Detect and fix missing/incorrect data; standardize; quality-score every record | **Team** (lead + rules-builder + test-writer) — the rule catalog and its adversarial test cases are naturally separable work |
| **Yash** | P3 — Reconciliation & Anomaly Detection | Dedup, cross-source reconciliation → golden records (single source of truth); anomalies, safety & health signals, derived metrics | **Team** (lead + reconciliation-builder + anomaly/statistics specialist) — matching and statistics are two deep, independent tracks |
| **Emily** | P4 — Reporting & Experience | PSC compliance reports, operational-awareness dashboard, safety triage and customer-experience views | **Single agent** — report design and rendering benefit from one cohesive context; the work is broad but not separable without churn |
| **Anastasia** | P5 — Platform & Integration Harness | Orchestrator, config, pipeline-health monitoring, integration harness, merge-readiness checks | **Single agent** — the contracts must live in one head; splitting them invites drift. (The human Anastasia is separately the Integration Manager at merge time.) |

### Kickoff prompt template (paste to each lead agent, filling in the name)

> You are the lead agent for **{PERSON}** on the EV Charger Data project. Read `PRODUCT_DEVELOPMENT_MANAGER.md` in the repo root and follow it exactly. You work ONLY on the existing branch `{person}` (`git fetch origin && git checkout {person}`) and ONLY inside the paths owned by {PERSON} in §4. Build the deliverables in §7 for your phase, honoring the schema (§5) and CLI contract (§6). You may spawn subagents only if §3 assigns you a team; you are the only committer either way. HARD RULES: never merge, rebase, or cherry-pick anything; never check out, push to, or read other branches or main; never create branches; never open or merge PRs; never edit files owned by main. Develop against your own fixtures. When your Definition of Done (§7) is met, push your final commits to `{person}` and stop.

---

## 4. Repository Layout & Ownership Map (disjoint by design)

```
genAIproj/
├── PRODUCT_DEVELOPMENT_MANAGER.md      ← frozen (main)
├── README.md, requirements.txt, .gitignore, pyproject.toml   ← frozen (main)
├── src/__init__.py and src/*/__init__.py                     ← frozen (main)
├── data/
│   ├── raw/            ← gitignored; downloaded/provided datasets land here
│   └── README.md       ← EMLYN: how to fetch/regenerate every dataset
├── src/
│   ├── ingestion/      ← EMLYN (P1): readers, LLM parsers, messiness injector, own requirements.txt, fixtures, tests
│   ├── validation/     ← SANJA (P2): rule engine, fixers, standardizers, own requirements.txt, fixtures, tests
│   ├── reconciliation/ ← YASH (P3): entity resolution, golden records, anomalies & metrics, own requirements.txt, fixtures, tests
│   ├── reporting/      ← EMILY (P4): PSC reports, dashboard, CX views, own requirements.txt, fixtures, tests
│   └── platform/       ← ANASTASIA (P5): orchestrator, config, health monitoring, stubs/, smoke.sh, own requirements.txt, fixtures, tests
├── tests/integration/  ← ANASTASIA (P5): contract harness (runs stage CLIs on fixtures)
├── docs/proposals/     ← anyone: <yourname>.md proposing changes to frozen things
└── artifacts/          ← gitignored; all pipeline outputs (each stage mkdir -p's what it needs)
```

Each person owns their directories **exclusively** — including fixtures, tests, and READMEs. No exceptions, no root-level additions. Anastasia's stubs live at `src/platform/stubs/` (never inside other modules), and her smoke entrypoint is `src/platform/smoke.sh` — there is no root `Makefile` or `scripts/`.

---

## 5. Canonical Schema (FROZEN — the contract that makes parallel work possible)

All inter-phase data is **JSONL** (one JSON object per line), UTF-8. Each module re-declares pydantic models locally from this spec (no cross-module imports before the merge — duplication here is deliberate).

### 5.0 Schema semantics — read before modeling

- **Raw at P1, normalized at P2.** Enum and ISO-format annotations below describe the **post-validation (P2) target**, not ingestion output. Emlyn's models type those fields as free strings and he passes source values through **verbatim** (`connector_type: "chgpt LVL2 dual"`, `start_time: "3/4/24 2pm"` are valid P1 output). Emlyn never converts dates, units, connector types, or statuses — field *placement* is his job; value *normalization* is Sanja's. Sanja's output models enforce the enums/formats.
- **Absent == null.** A `"x|null"` key may be absent entirely; readers must treat absent and null identically. Writers should omit rather than emit nulls.
- **Unknown extra fields must be preserved and never rejected** (pydantic `extra="allow"` everywhere). Downstream stages add fields; upstream may attach raw payload extras.
- **`quality.score` lifecycle:** ingestion writes an initial score = parsing/extraction confidence; validation moves that value to `quality.extraction_confidence` and recomputes `score` as the issue-severity-weighted final value (0.0–1.0). Reconciliation reads but never writes `score`.
- `issues` use stable snake_case codes (e.g. `missing_energy_kwh`, `impossible_duration`, `stale_report`).
- The `MaintenanceEvent.severity` vocabulary (`INFO|MINOR|MAJOR|SAFETY`) and the anomaly `severity` vocabulary (`INFO|WARN|CRITICAL|SAFETY`) are **intentionally distinct** enums.

### 5.1 `ChargerRecord` — one physical charger port

```json
{
  "record_type": "charger",
  "charger_id": "string|null",        // stable ID if known, else null
  "station_id": "string|null",
  "network": "string|null",           // e.g. ChargePoint, contractor name
  "address": {"street": "string|null", "city": "string|null", "state": "string|null", "zip": "string|null"},
  "lat": "float|null", "lon": "float|null",
  "connector_type": "string|null",    // P2 target enum: J1772 | CCS | CHAdeMO | NACS | OTHER
  "power_kw": "float|null",
  "level": "string|null",             // P2 target enum: L1 | L2 | DCFC
  "status": "string|null",            // P2 target enum: ACTIVE | INACTIVE | MAINTENANCE | UNKNOWN
  "install_date": "string|null",      // P2 target: ISO-8601 date
  "provenance": {"source": "string", "source_file": "string", "ingested_at": "string", "raw_ref": "string|null"},
  "quality": {"score": "float|null", "extraction_confidence": "float|null", "issues": ["string"], "fixes_applied": ["string"]}
}
```

### 5.2 `SessionRecord` — one charging session

```json
{
  "record_type": "session",
  "session_id": "string|null",
  "charger_id": "string|null",
  "station_id": "string|null",
  "start_time": "string|null",        // P2 target: ISO-8601 with timezone
  "end_time": "string|null",
  "energy_kwh": "float|null",
  "peak_kw": "float|null",
  "duration_min": "float|null",
  "fault_code": "string|null",
  "provenance": { "...as 5.1..." },
  "quality": { "...as 5.1..." }
}
```

### 5.3 `MaintenanceEvent` — parsed from unstructured contractor reports

```json
{
  "record_type": "maintenance",
  "event_id": "string",
  "charger_id": "string|null",
  "station_id": "string|null",
  "event_date": "string|null",
  "event_type": "string|null",        // INSPECTION | REPAIR | FAULT | INSTALL | COMPLAINT | OTHER
  "severity": "string|null",          // INFO | MINOR | MAJOR | SAFETY
  "description": "string",
  "extracted_fields": {},             // free-form key/values the LLM pulled out
  "provenance": { "..." }, "quality": { "..." }
}
```

### 5.4 Golden records (P3 output — the single source of truth)

`golden.jsonl` contains **three kinds of lines**, all with `record_type` **unchanged** (`charger` / `session` / `maintenance`):

- **Golden charger & session records**: the §5.1/§5.2 shape plus:
  ```json
  "golden_id": "string",                       // deterministic stable hash of the resolved entity key
  "merged_from": [{"source": "string", "raw_ref": "string|null"}],
  "conflicts_resolved": [{"field": "string", "chosen": "...", "rejected": ["..."], "rule": "string"}],
  "anomalies": [{"type": "string", "severity": "INFO|WARN|CRITICAL|SAFETY", "detail": "string", "evidence": ["event_id|session_id"]}]
  ```
- **Golden charger records additionally carry** (computed by Yash, rendered — never recomputed — by Emily):
  ```json
  "health": {"state": "HEALTHY|DEGRADED|SUSPECT_OUTAGE|SAFETY_REVIEW", "since": "ISO-8601|null", "evidence": ["string"]},
  "metrics": {"est_uptime_pct": "float|null",     // estimated availability: 1 − (suspected-outage days / days in period),
                                                   // outage days inferred from session gaps + maintenance windows
              "fault_recurrence_count": "int",
              "reporting_lag_p50_days": "float|null", "reporting_lag_p95_days": "float|null"}
  ```
- **Maintenance events pass through** (deduped), gaining the `golden_id` of their resolved charger — Emily's safety view depends on them being present.

### 5.5 Report-file schemas (FROZEN — minimal keys; producers may add, never remove)

- `validation_report.json` (Sanja): `{"records_in": n, "records_out": n, "quarantined": n, "per_source": {"<source>": {"records": n, "avg_score": f, "issues": {"<code>": n}}}, "per_issue": {"<code>": n}}`
- `reconciliation_report.json` (Yash): `{"records_in": n, "golden_records_out": n, "duplicates_removed": n, "clusters": n, "conflicts_resolved": n, "anomalies": {"<type>": n}, "per_source_lag_days": {"<source>": {"p50": f, "p95": f}}}`
- `pipeline_health.json` (Anastasia): `{"stages": {"<stage>": {"records_in": n, "records_out": n, "duration_s": f, "llm_calls": n, "llm_tokens": n, "exit_code": n}}, "run_started": "ISO-8601", "run_finished": "ISO-8601"}`

### 5.6 Schema change process

The schema is frozen for the parallel phase. If it truly blocks you, write `docs/proposals/<yourname>.md`; the Integration Manager arbitrates (and may apply an accepted change to `main` for all branches to rebase onto — a human action). Never change it unilaterally.

---

## 6. CLI Contract (FROZEN)

Every stage is an independent CLI. This is the only integration surface.

```bash
python -m src.ingestion.run      --in data/raw/            --out artifacts/canonical.jsonl
python -m src.validation.run     --in <canonical.jsonl>    --out artifacts/validated.jsonl   --report artifacts/validation_report.json
python -m src.reconciliation.run --in <validated.jsonl>    --out artifacts/golden.jsonl      --report artifacts/reconciliation_report.json
python -m src.reporting.run      --in <golden.jsonl>       --out artifacts/reports/ \
        [--validation-report <path>] [--reconciliation-report <path>] [--pipeline-health <path>]   # all optional; sections render "data unavailable" when absent
python -m src.platform.run --pipeline full --config src/platform/config.yaml   # orchestrates the above, wiring every --out/--report to the next stage's inputs
```

### Per-stage contract table

| Stage | Input | Output | "Valid output" means |
|---|---|---|---|
| ingestion | directory of raw files | JSONL file | every line parses against §5.1–5.3 P1 (raw-string) models |
| validation | JSONL file | JSONL file + report JSON | every line parses against P2 (normalized) models; report matches §5.5 |
| reconciliation | JSONL file | JSONL file + report JSON | every line parses against §5.4 models; report matches §5.5 |
| reporting | JSONL file (+ optional report JSONs) | directory of CSV/HTML | the expected file set exists and is non-empty |
| platform | config | orchestrated run + `pipeline_health.json` | health file matches §5.5 |

### Flags and process protocol (all stages)

- `--no-llm` (deterministic fallback), `--limit N`, `--log-json`. The orchestrator **propagates** `--no-llm`/`--limit` to every stage it runs.
- `--limit N` = emit at most N output records **total** (across all record types), processing input files in sorted-name order, then stop. Harness asserts ≤ N, never == N.
- **stderr protocol:** with `--log-json`, log lines are JSON objects containing `"log": true`. On **success**, the final stderr line is `{"metrics": {"records_in": n, "records_out": n, "llm_calls": n, "llm_tokens": n}}` (zeros when `--no-llm`). On **failure**, exit nonzero and the final stderr line is `{"error": true, "stage": "<module>", "message": "string", "detail": {}}`. This is how A3 collects health data without any code imports.
- Every stage `mkdir -p`'s its own output directory.
- During parallel development each stage is fed its owner's own fixtures, never live upstream output.

---

## 7. Phase Task Lists (no overlap) and Definitions of Done

**Boundary rules that resolve every known seam — binding:**

- **Emlyn parses, Sanja fixes.** Emlyn never normalizes values (§5.0). Sanja never re-parses raw files.
- **Sanja judges records one at a time; Yash judges records against each other.** Sanja performs **no cross-record comparison of any kind — including exact-duplicate detection**. Duplicate rows pass through validation untouched and unflagged, because Y3's duplicate-billing detection needs to see them.
- **Yash computes every derived metric** (uptime, fault recurrence, reporting lag aggregates, health states) into §5.4 fields. **Emily renders; she computes no availability, latency, or quality math herself.** Her lineage/league-table numbers come from the §5.5 report files, which she receives via her CLI flags.
- **Per-record staleness (Sanja) vs lag aggregates (Yash):** Sanja flags `stale_report` on any record where `provenance.ingested_at` − event/session/report date > **7 days** (fixed threshold). Yash computes per-source lag distributions. Emily's latency tracker renders Yash's figures only.
- **Pipeline health (Anastasia) is about the system; charger health (Yash) is about the fleet.**

### P1 — Emlyn: Data & Ingestion (`src/ingestion/`, `data/`)

**Dataset (gap identified → filled here).** The repo contained **no dataset**. P1 establishes one from three complementary sources recreating the Con Edison situation (multi-source, multi-format, overlapping entities):

1. **Source A — charging sessions (structured CSV):** City of Palo Alto open-data "EV Charging Station Usage" export (or any equivalent public per-session CSV). If organizers supply an official dataset, drop it in `data/raw/` — contracts are dataset-agnostic; only P1 readers change.
2. **Source B — station registry (API/JSON):** NREL AFDC Alternative Fuel Station Locator export covering the same region as Source A, giving a second overlapping description of the same chargers (what P3 reconciles against).
3. **Source C — contractor reports (unstructured, synthetic):** a `messiness_injector` module that (a) generates realistic free-text/semi-structured maintenance & install reports (emails, CSVs with merged headers, PDF-like text) referencing Source A/B chargers, and (b) degrades copies of A/B: missing fields, unit swaps, date-format chaos, duplicated rows, conflicting values, late-arriving files. Deterministic via `--seed`; a `--scale N` flag multiplies synthetic volume for the §8 scale run.

**Geography note (binding):** the pipeline's default territory is **the Source A region (Palo Alto / Santa Clara County)**, not Con Ed's. Territory appears nowhere in code as a constant — see S1.

**Tasks**

- E1. `data/README.md`: exact fetch/regeneration instructions for A, B, C.
- E2. Format readers: CSV (incl. malformed/merged-header), JSON/API dump, free text — auto-detecting file type in `data/raw/`.
- E3. **LLM parsing layer**: Claude-based extraction of `MaintenanceEvent`s (and embedded charger facts) from Source C text, with a regex/heuristic `--no-llm` fallback; extraction confidence written to `quality.score` per §5.0.
- E4. Structural normalization to canonical JSONL: field placement, provenance, one record per line. **Values pass through verbatim per §5.0 — no date/unit/enum conversion.**
- E5. Messiness injector with `--seed` and `--scale` as described above.
- E6. Fixtures (`src/ingestion/fixtures/`): small samples of every input format + expected canonical output; unit tests for every reader and the `--no-llm` path; `src/ingestion/requirements.txt`.

**DoD:** `python -m src.ingestion.run --in data/raw --out artifacts/canonical.jsonl --no-llm` produces P1-schema-valid JSONL covering all three sources; §6 flags/stderr protocol honored; tests pass offline; module requirements.txt complete.

### P2 — Sanja: Validation & Cleaning (`src/validation/`)

Consumes canonical JSONL (her **own fixtures** during development), emits `validated.jsonl` + `validation_report.json` (§5.5).

**Tasks**

- S1. Declarative **rule engine** (rules as data — YAML in her module): required fields per `record_type`, type/range checks (`energy_kwh ≥ 0`, `power_kw ≤ 400`, `end_time > start_time`), enum membership after normalization, cross-field checks (`duration_min` ≈ `end − start`), and a **territory bounding-box check whose coordinates live in the YAML config**, shipped defaulted to the Source A region (Palo Alto / Santa Clara County) and swapped by config alone if an official Con Ed dataset lands.
- S2. **Standardizers**: dates → ISO-8601 with timezone, unit repair (W→kW, Wh→kWh via magnitude heuristics), connector/level/status normalization to §5 target enums, address/zip cleanup, whitespace/casing.
- S3. **Missing-data handling**: derive when possible (duration from timestamps, level from power_kw), impute conservatively where safe (recording `fixes_applied`), else flag with an `issues` code — never drop a record; unfixable records are quarantined in the report but still emitted with a low score. **No cross-record logic; duplicates pass through untouched (§7 boundary rules).**
- S4. **Incorrect-data detection & fixing** at single-record level: impossible values, out-of-range, internal contradictions; `stale_report` flagging per the 7-day rule.
- S5. LLM-assisted repair (`--no-llm` fallback) for messy categorical/free-text fields ("chgpt LVL2 dual" → connector/level enums), logged in `fixes_applied`.
- S6. Quality scoring per §5.0 (preserving `extraction_confidence`); `validation_report.json` exactly per §5.5; fixtures + adversarial unit tests; `src/validation/requirements.txt`.

**DoD:** validation on her fixtures fixes the fixable, quarantines the rest, emits P2-schema-valid output + §5.5-conformant report; §6 protocol honored; tests pass offline; module requirements.txt complete.

### P3 — Yash: Reconciliation & Anomaly Detection (`src/reconciliation/`)

Consumes validated JSONL (own fixtures during development), emits `golden.jsonl` + `reconciliation_report.json` (§5.5). Builds the **single source of truth**.

**Tasks**

- Y1. **Entity resolution**: blocking + fuzzy matching (IDs, geo-distance, address/name similarity) clustering records that describe the same physical charger/station across sources; exact + fuzzy **dedup of sessions** (sole owner of all dedup, per boundary rules).
- Y2. **Conflict resolution → golden records**: field-level survivorship (source priority, freshness, quality-score-weighted vote), every decision logged in `conflicts_resolved`; deterministic `golden_id`. **Re-run semantics: re-running over a superset of inputs (late-arriving files) must supersede prior golden records — same entities keep the same `golden_id`; never duplicate.** Maintenance events pass through deduped with their resolved `golden_id` (§5.4).
- Y3. **Anomaly detection** (cross-record/statistical): duplicate-billing patterns, per-charger energy outliers (robust z-score/IQR), utilization cliffs (silent charger = suspected outage), impossible concurrent sessions on one port, per-source reporting-lag distributions (→ report + `metrics`).
- Y4. **Safety, health & derived metrics** (sole computer of all of these, per boundary rules): `SAFETY` anomalies (unresolved SAFETY maintenance events, repeated fault codes, power draw above rated `power_kw`); per-charger `health` state; `metrics` block per §5.4 including `est_uptime_pct` exactly as defined there.
- Y5. `reconciliation_report.json` per §5.5; fixtures with known-truth clusters (incl. maintenance events and planted duplicates/conflicts/anomalies); precision/recall tests for matching; `src/reconciliation/requirements.txt`.

**DoD:** fixtures with planted duplicates/conflicts/anomalies yield correct golden records (all plants caught, maintenance passed through, health/metrics populated); §6 protocol honored; tests pass offline; module requirements.txt complete.

### P4 — Emily: Reporting & Experience (`src/reporting/`)

Consumes golden JSONL **plus the three optional §5.5 report files** (all via her §6 CLI flags; her fixtures include hand-written examples of all four inputs), emits `artifacts/reports/`.

**Tasks**

- M1. **PSC compliance report** (CSV + HTML): program KPIs — chargers deployed/active, energy delivered, `est_uptime_pct` (rendered from §5.4 metrics, never recomputed), sessions, data-completeness % — plus a **data-lineage appendix** rendered from the §5.5 report files (per-stage record counts, quality-score distribution, quarantine summary) so "accurate and on time" is demonstrable. Include a **restated-report mode**: when golden data supersedes a prior period's run, the report carries a "restates report of <date>" banner and a delta section.
- M2. **Operational awareness dashboard** (static HTML): fleet status grid/map, anomaly feed by severity, per-contractor data-quality league table (from `validation_report.json`), reporting-latency tracker (rendering Yash's per-source lag figures only).
- M3. **Safety & infrastructure-health view**: triage list from `health` states, SAFETY anomalies, and pass-through maintenance evidence (what, where, since when, evidence ids).
- M4. **Customer-experience view**: availability/reliability by location from §5.4 metrics, chronic-failure sites from `fault_recurrence_count`, plain-language LLM summaries (`--no-llm` template fallback).
- M5. Correctness tests: fixtures with hand-computed expected KPIs; snapshot tests for HTML; graceful "data unavailable" rendering when optional report files are absent; `src/reporting/requirements.txt`.

**DoD:** one command renders all four outputs from her fixtures with hand-verifiable numbers, with and without the optional report files; §6 protocol honored; tests pass offline; module requirements.txt complete.

### P5 — Anastasia: Platform & Integration Harness (`src/platform/`, `tests/integration/`)

Builds the glue that makes five modules a pipeline **after** merge — developed now against stubs and fixtures, importing nobody's unmerged code.

**Tasks**

- A1. **Orchestrator**: executes the §6 stage commands as subprocesses in order, wiring outputs to inputs (including reporting's three `--*-report` flags); propagates `--no-llm`/`--limit`; per-stage timing and retries; resumability via an artifact manifest **that is invalidated whenever `data/raw/` contents change** (late-arriving files force re-runs of affected stages — never silently skipped). **Stage entrypoints come from `config.yaml`, defaulting to the real §6 module paths, overridable to stubs via `--use-stubs`.**
- A2. **Config & secrets**: `config.yaml` (paths, entrypoints, LLM on/off, limits); env-var handling for `ANTHROPIC_API_KEY`; a check that verifies `.gitignore` covers `data/raw/` and `artifacts/` (report-only — the file itself is frozen).
- A3. **Pipeline health monitoring**: consume each stage's final stderr `metrics`/`error` line (§6 protocol) into `pipeline_health.json` per §5.5 + console summary with failure alerts.
- A4. **Integration harness** (`tests/integration/`): runs every stage entrypoint (stubs pre-merge, real modules post-merge) against her own contract fixtures, asserting the §6 per-stage contract table — flags honored, exit codes and final-stderr-line protocol correct, output valid per the table. For ingestion, assert flag/exit/protocol behavior and schema-validity of whatever is emitted — **not** accept/reject verdicts on specific raw files (input formats are Emlyn's design space). Includes `src/platform/smoke.sh` for the Integration Manager.
- A5. **Merge-readiness checker**: verifies `git diff --name-only main...<branch>` touches only that owner's §4 paths — the gate used in §8.
- A6. **Stubs** at `src/platform/stubs/<stage>_stub.py`: trivial pass-through implementations of each §6 stage so the orchestrator and harness run end-to-end pre-merge via `--use-stubs`. **Never create any file under the other four `src/` modules.**

**DoD:** orchestrator runs the full pipeline over stubs + fixtures; harness red/greens contract violations correctly; merge-readiness checker works on this repo; manifest invalidation on raw-data change demonstrated in a test; §6 protocol honored; tests pass offline; module requirements.txt complete.

---

## 8. Merge Plan (human-only, at the end)

Performed manually by the **Integration Manager (a person, not an agent — see §2.1 rule 4)**; default: Anastasia.

1. Freeze: each person confirms DoD met and final push done.
2. For each branch, run the merge-readiness checker (A5). A branch touching foreign paths is fixed **on that branch by its owner** before merging.
3. Union the five `src/<module>/requirements.txt` files into root `requirements.txt` (one commit on `main`); `pip install -r requirements.txt` into a fresh venv.
4. Merge order (all orders are conflict-free given §4; this one fails fastest):
   `anastasia` → `emlyn` → `sanja` → `yash` → `emily`, each via
   ```bash
   git checkout main && git merge --no-ff <branch>
   ```
   After the `anastasia` merge, run the harness in `--use-stubs` mode (real modules aren't on `main` yet); after each subsequent merge, run that module's unit tests plus the harness against the real modules merged so far (stubs for the rest).
5. After all five: run the end-to-end pipeline on real entrypoints — first `--no-llm --limit 500`, then full, then the **scale run**: regenerate data with `--scale` (≥100k records) and confirm streaming behavior (§2.3) keeps memory flat. Eyeball the PSC report and dashboard. Push `main`.
6. Post-merge punch list is triaged by the Integration Manager back to owners, who fix on fresh short-lived branches under the same rules.

---

## 9. Requirements Traceability (challenge → owner)

| Challenge requirement | Owner(s) — concrete tasks |
|---|---|
| Handle unstructured / inconsistent inputs (NLP/LLM parsing) | Emlyn (E2–E4) |
| Identify & fix missing/incorrect data; validation pipelines | Sanja (S1–S5) |
| Anomaly detection | Yash (Y3) |
| Automated reconciliation across sources / single source of truth | Yash (Y1–Y2) |
| Accurate, on-time PSC reporting | Emily (M1) rendering §5.5 lineage from Sanja (S6), Yash (Y5), Anastasia (A3) — wired via reporting CLI flags |
| Late-arriving data | Emlyn generates (E5) · Anastasia re-runs (A1 manifest invalidation) · Yash supersedes (Y2) · Emily restates (M1) |
| Safety | Yash detects (Y4) → Emily renders (M3) |
| Infrastructure health | Fleet: Yash (Y4) → Emily (M3); pipeline: Anastasia (A3) |
| Operational awareness | Emily (M2) from Yash's metrics + §5.5 reports |
| Customer experience | Emily (M4) from §5.4 metrics |
| Multiple formats & sources | Emlyn (E1–E2, 3 sources) |
| Scale & repeatability | Streaming mandate (§2.3, all owners) · seeded injector with `--scale` (E5) · resumability (A1) · scale run (§8 step 5) |

## 10. Gaps Identified and Filled

| Gap | Resolution |
|---|---|
| **No dataset in repo** | 3-source dataset (public sessions CSV + public station registry + synthetic messy contractor reports); official dataset can drop in later — only P1 readers adapt |
| **Repo had no scaffold; frozen root files didn't exist** | Phase 0 (§2.0): `.gitignore`, `requirements.txt`, `pyproject.toml`, package `__init__.py`s seeded on `main` before branching; branches pre-created by a human |
| Public data is clean; messy data is the premise | Deterministic messiness injector (E5) with `--seed`/`--scale` |
| Parallel phases normally block on each other | Frozen schema (§5) + frozen CLI (§6) + frozen §5.5 report shapes + per-module fixtures |
| Merge conflicts across 5 branches | Disjoint ownership (§4), frozen shared files, stubs confined to `src/platform/stubs/`, merge-readiness checker (A5) |
| Two people could build the same thing at the seams | Binding boundary rules (§7): raw-at-P1/normalized-at-P2, all dedup and all derived metrics owned by Yash, Emily renders only |
| Agents merging/pushing where they shouldn't | §2.1 hard rules (incl. no cherry-pick, no branch creation, no reading other branches), human-only §8, Anastasia's agent barred from executing §8 |
| Dependencies frozen with no path to declare them | Per-module `requirements.txt` in every DoD, unioned by the human at §8 step 3 |
| Late-arriving data generated but never handled | A1 manifest invalidation + Y2 supersede semantics + M1 restated-report mode |
| "Scale" claimed but unbacked | Streaming mandate (§2.3) + E5 `--scale` + §8 scale run with flat-memory check |
| Uptime KPI had no producer | `est_uptime_pct` defined in §5.4, computed solely by Yash (Y4), rendered by Emily |
| LLM cost/usage invisible to monitoring | §6 stderr metrics protocol consumed by A3 |
| Secrets/keys hygiene | Env-var-only keys; gitignore seeded in Phase 0, verified by A2 |
| Schema will eventually need changes | Proposal process (§5.6) arbitrated by the human Integration Manager |

---

*Questions or blockers → write `docs/proposals/<yourname>.md` on your branch; the Integration Manager arbitrates. Never resolve cross-phase disputes by editing someone else's directory.*
