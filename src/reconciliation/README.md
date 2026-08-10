# src/reconciliation — P3 Reconciliation & Anomaly Detection (Yash)

Consumes **validated JSONL** (P2 output; own fixtures during development),
emits **`golden.jsonl`** — the single source of truth (§5.4) — plus
**`reconciliation_report.json`** (§5.5). Owns **all dedup**, all
cross-record/statistical anomaly detection, and **every derived metric**
(health states, uptime, fault recurrence, lag aggregates). Emily renders;
she never recomputes (§7 boundary rules).

## CLI (frozen contract §6)

```bash
python -m src.reconciliation.run --in <validated.jsonl> \
    --out artifacts/golden.jsonl --report artifacts/reconciliation_report.json
```

Flags: `--no-llm` (accepted for §6 compatibility — reconciliation is
deterministic and never calls an LLM), `--limit N` (emit at most N golden
records total), `--log-json` (§6 stderr protocol). Input is streamed
line-by-line; output is written one record per line. Every stage `mkdir -p`'s
its own output directory. On failure: nonzero exit + final stderr line
`{"error": true, "stage": "reconciliation", "message": ..., "detail": ...}`.
On success: final stderr line `{"metrics": {...}}`.

## File map

| File | Role |
|---|---|
| `models.py` | P3 pydantic models: lax input models (§5.1–5.3) + golden output models (§5.4), `extra="allow"` everywhere |
| `matching.py` | normalization, name similarity, haversine, source-priority ranking |
| `resolve.py` | **Y1** entity resolution: blocking + fuzzy matching, charger clustering, session/event association, exact+fuzzy dedup |
| `reconcile.py` | **Y2** golden records: survivorship conflict resolution, deterministic `golden_id`, supersede semantics |
| `anomalies.py` | **Y3** anomaly detection (duplicate billing, energy outliers, utilization cliffs, concurrent sessions, lag distributions) |
| `health.py` | **Y4** health states (`HEALTHY/DEGRADED/SUSPECT_OUTAGE/SAFETY_REVIEW`) + metrics (`est_uptime_pct`, `fault_recurrence_count`, lag p50/p95) |
| `report.py` | **Y5** `reconciliation_report.json` builder (§5.5) |
| `run.py` | stage CLI, §6 protocol |
| `dates.py` | ISO-8601 parsing, lag days, nearest-rank percentiles, IQR fences |
| `fixtures/` | committed known-truth fixtures + deterministic builder (`make_fixtures.py`) |
| `tests/` | unit tests (offline) — `test_reconciliation_*.py` |

## Design decisions (read before touching)

- **Chargers are clustered per physical port.** P1 emits one record per port;
  two ports of one station are distinct entities. `charger_id` equality
  discriminates ports; `station_id` equality merges single-port stations;
  name+geo+address fuzzy rules catch cross-source duplicates (A sessions vs B
  registry).
- **Entity key order** for `golden_id`: `charger_id` → `station_id` → name+
  geo of the primary member. Keys are stable identities, so re-running over a
  superset (late-arriving files) keeps the same `golden_id` and never
  duplicates (Y2 re-run semantics; tested).
- **Survivorship** ranks members by (source_rank, ingested_at, quality.score,
  record order): registry (`afdc`/`bts`/`nrel`/`dot`) > municipal (`cary`) >
  contractor > unknown. Field-level conflicts log `chosen`/`rejected`/`rule`
  into `conflicts_resolved`.
- **Session dedup** within one charger cluster (or unresolved group keyed by
  station name): exact `session_id`, or fuzzy (start ≤ 15 min, energy ≤
  max(0.5 kWh, 10%), duration ≤ 15%). Fuzzy merges of records with *distinct*
  session_ids are double-billing evidence → `duplicate_billing` anomaly on the
  golden session (WARN; CRITICAL when merged energy ≥ 30 kWh).
- **Utilization cliff = trailing silence.** A charger that had ≥ 3 sessions
  and then none for ≥ 30 days *at the end of the global observation period*
  is a suspected outage (`utilization_cliff`, CRITICAL) unless a MAJOR/SAFETY
  maintenance event explains the silence. Internal gaps are not cliffs
  (sporadic use is normal). Same rule drives `est_uptime_pct` =
  1 − (silent days / global period days), so SUSPECT_OUTAGE ⇔ uptime < 1 by
  construction.
- **Energy outliers** need ≥ 5 sessions on the charger; fence = Q3 + 3·IQR
  (MAD-based robust z > 5 fallback on degenerate spreads).
- **Concurrency** is per port: each charger cluster *is* one port, so any
  overlapping [start, end) pair with energy ≥ 0.5 kWh is impossible →
  CRITICAL.
- **SAFETY anomalies** (drive SAFETY_REVIEW): `power_over_rated` (peak >
  rated × 1.1) and `unresolved_safety_event` (a SAFETY maintenance event with
  no later REPAIR on the same port).
- **Health priority**: SAFETY_REVIEW > SUSPECT_OUTAGE > DEGRADED > HEALTHY.
  `health.since` = earliest ISO date in the deciding anomalies' details;
  `health.evidence` = their detail strings.
- **Lag** = `ingested_at − event/session date`, clamped ≥ 0; per-source p50/
  p95 go to the report, per-charger p50/p95 into `metrics`.
- `duplicates_removed` = records absorbed by clustering/dedup =
  `records_in − golden_records_out` (consistency asserted in tests).

## Fixtures (Y5)

`fixtures/validated.jsonl` plants every detector with known truth:
- A1 (Cary Town Hall L2): utilization cliff + energy outlier (42.7 kWh) +
  duplicate-billing pair + exact session duplicate
- A2 (Cary Town Hall CCS): concurrent sessions + repeated fault code E-42
- B1 (Raleigh Municipal): conflicting registry update (7.2 kW/MAINTENANCE
  wins by freshness), power over rated, unresolved SAFETY event
- C1 (Cary DT Deck): clean control; SAFETY event resolved by a later REPAIR;
  duplicate maintenance event → dedup
- Unknown-station session + event pass through unresolved

`fixtures/validated_late.jsonl` = base + late-arriving records (new session
for A1, brand-new station ST-4001, extra event) — exercises the Y2 supersede
semantics. Regenerate byte-identically with:

```bash
python -m src.reconciliation.fixtures.make_fixtures
```

## Dev setup & tests

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r src/reconciliation/requirements.txt
.venv/bin/python -m pytest src/reconciliation -q     # offline
```

Definition of Done: fixtures with planted duplicates/conflicts/anomalies
yield correct golden records (all plants caught, maintenance passed through,
health/metrics populated); §6 protocol honored; tests pass offline; module
requirements.txt complete.
