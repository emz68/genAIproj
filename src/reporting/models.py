"""P4 — Reporting & Experience: pydantic models.

These models are re-declared locally from the frozen spec in
PRODUCT_DEVELOPMENT_MANAGER.md §5 (deliberate duplication — no cross-module
imports before the merge). Reporting consumes:

  * golden.jsonl  — §5.4 Golden records (P3 output): charger / session /
                    maintenance lines, plus the per-charger ``health`` and
                    ``metrics`` blocks that Yash computed (we RENDER, never
                    recompute).
  * validation_report.json      (§5.5, optional, Sanja)
  * reconciliation_report.json  (§5.5, optional, Yash)
  * pipeline_health.json        (§5.5, optional, Anastasia)

Per §5.0 semantics:
  * "absent == null" — every field is optional here and readers treat absent
    and null identically (models default to None and we render None as "N/A").
  * unknown extra fields are preserved and never rejected (extra="allow").
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _AllowExtra(BaseModel):
    """All models keep unknown fields; readers never reject unknown extras."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# §5.4 golden records
# ---------------------------------------------------------------------------


class Provenance(_AllowExtra):
    source: Optional[str] = None
    source_file: Optional[str] = None
    ingested_at: Optional[str] = None
    raw_ref: Optional[str] = None


class Quality(_AllowExtra):
    score: Optional[float] = None
    extraction_confidence: Optional[float] = None
    issues: List[str] = Field(default_factory=list)
    fixes_applied: List[str] = Field(default_factory=list)


class Address(_AllowExtra):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None


class MergedFrom(_AllowExtra):
    source: Optional[str] = None
    raw_ref: Optional[str] = None


class ConflictResolution(_AllowExtra):
    field: Optional[str] = None
    chosen: Any = None
    rejected: List[Any] = Field(default_factory=list)
    rule: Optional[str] = None


class Anomaly(_AllowExtra):
    type: Optional[str] = None
    severity: Optional[str] = None  # INFO | WARN | CRITICAL | SAFETY
    detail: Optional[str] = None
    evidence: List[str] = Field(default_factory=list)


class Health(_AllowExtra):
    """§5.4 — computed solely by Yash (P3/Y4); rendered verbatim here."""

    state: Optional[str] = None  # HEALTHY | DEGRADED | SUSPECT_OUTAGE | SAFETY_REVIEW
    since: Optional[str] = None
    evidence: List[str] = Field(default_factory=list)


class Metrics(_AllowExtra):
    """§5.4 — computed solely by Yash (P3/Y4); rendered verbatim here."""

    est_uptime_pct: Optional[float] = None
    fault_recurrence_count: int = 0
    reporting_lag_p50_days: Optional[float] = None
    reporting_lag_p95_days: Optional[float] = None


class GoldenRecord(_AllowExtra):
    """Base for all golden.jsonl lines. ``record_type`` is never rewritten."""

    record_type: Optional[str] = None
    golden_id: Optional[str] = None
    merged_from: List[MergedFrom] = Field(default_factory=list)
    conflicts_resolved: List[ConflictResolution] = Field(default_factory=list)
    anomalies: List[Anomaly] = Field(default_factory=list)
    provenance: Optional[Provenance] = None
    quality: Optional[Quality] = None


class GoldenCharger(GoldenRecord):
    """§5.1 shape + §5.4 additions, incl. health/metrics blocks."""

    charger_id: Optional[str] = None
    station_id: Optional[str] = None
    network: Optional[str] = None
    address: Optional[Address] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    connector_type: Optional[str] = None
    power_kw: Optional[float] = None
    level: Optional[str] = None
    status: Optional[str] = None
    install_date: Optional[str] = None
    health: Optional[Health] = None
    metrics: Optional[Metrics] = None


class GoldenSession(GoldenRecord):
    """§5.2 shape + §5.4 additions."""

    session_id: Optional[str] = None
    charger_id: Optional[str] = None
    station_id: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    energy_kwh: Optional[float] = None
    peak_kw: Optional[float] = None
    duration_min: Optional[float] = None
    fault_code: Optional[str] = None


class GoldenMaintenance(GoldenRecord):
    """§5.3 shape — passes through P3 deduped, gaining the resolved golden_id."""

    event_id: Optional[str] = None
    charger_id: Optional[str] = None
    station_id: Optional[str] = None
    event_date: Optional[str] = None
    event_type: Optional[str] = None
    severity: Optional[str] = None  # INFO | MINOR | MAJOR | SAFETY
    description: Optional[str] = None
    extracted_fields: Dict[str, Any] = Field(default_factory=dict)


def parse_golden_line(line: str) -> GoldenRecord:
    """Parse one JSONL line into the right §5.4 model by record_type.

    Unknown record_types are preserved as generic GoldenRecord (extra fields
    kept) rather than rejected — forward compatibility with schema evolution.
    """
    data = _loads(line)
    rt = data.get("record_type")
    if rt == "charger":
        return GoldenCharger.model_validate(data)
    if rt == "session":
        return GoldenSession.model_validate(data)
    if rt == "maintenance":
        return GoldenMaintenance.model_validate(data)
    return GoldenRecord.model_validate(data)


# ---------------------------------------------------------------------------
# §5.5 report files (optional inputs to the reporting stage)
# ---------------------------------------------------------------------------


class SourceValidationStats(_AllowExtra):
    records: int = 0
    avg_score: Optional[float] = None
    issues: Dict[str, int] = Field(default_factory=dict)


class ValidationReport(_AllowExtra):
    """validation_report.json — produced by Sanja (P2/S6)."""

    records_in: int = 0
    records_out: int = 0
    quarantined: int = 0
    per_source: Dict[str, SourceValidationStats] = Field(default_factory=dict)
    per_issue: Dict[str, int] = Field(default_factory=dict)


class SourceLag(_AllowExtra):
    p50: Optional[float] = None
    p95: Optional[float] = None


class ReconciliationReport(_AllowExtra):
    """reconciliation_report.json — produced by Yash (P3/Y5)."""

    records_in: int = 0
    golden_records_out: int = 0
    duplicates_removed: int = 0
    clusters: int = 0
    conflicts_resolved: int = 0
    anomalies: Dict[str, int] = Field(default_factory=dict)
    per_source_lag_days: Dict[str, SourceLag] = Field(default_factory=dict)


class StageHealth(_AllowExtra):
    records_in: int = 0
    records_out: int = 0
    duration_s: Optional[float] = None
    llm_calls: int = 0
    llm_tokens: int = 0
    exit_code: int = 0


class PipelineHealth(_AllowExtra):
    """pipeline_health.json — produced by Anastasia (P5/A3)."""

    stages: Dict[str, StageHealth] = Field(default_factory=dict)
    run_started: Optional[str] = None
    run_finished: Optional[str] = None


def _loads(line: str) -> dict:
    import json

    line = line.strip()
    if not line:
        return {}
    return json.loads(line)
