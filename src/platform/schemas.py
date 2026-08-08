"""Local pydantic re-declaration of the frozen §5 canonical schema.

Per §5 this module re-declares the contract locally (no cross-module imports
before the merge). Three tiers, per §5.0:

- Raw* (P1 output): enum/ISO-annotated fields are FREE STRINGS — ingestion
  passes source values through verbatim. Numeric fields tolerate raw strings.
- Validated* (P2 output): enums and ISO formats are enforced.
- Golden* (P3 output): Validated shape plus golden_id / merged_from /
  conflicts_resolved / anomalies, and for chargers health + metrics (§5.4).

All models allow and preserve unknown extra fields, and every "x|null" key
may be absent entirely (absent == null) — §5.0.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------- shared blocks

class Address(_Base):
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None


class Provenance(_Base):
    source: str
    source_file: str
    ingested_at: str
    raw_ref: str | None = None


class Quality(_Base):
    score: float | None = None
    extraction_confidence: float | None = None
    issues: list[str] = []
    fixes_applied: list[str] = []


RawNumber = float | int | str | None  # P1 may carry unparsed numerics verbatim


# ---------------------------------------------------------------- P1 raw models

class RawCharger(_Base):
    record_type: Literal["charger"]
    charger_id: str | None = None
    station_id: str | None = None
    network: str | None = None
    address: Address | None = None
    lat: RawNumber = None
    lon: RawNumber = None
    connector_type: str | None = None
    power_kw: RawNumber = None
    level: str | None = None
    status: str | None = None
    install_date: str | None = None
    provenance: Provenance
    quality: Quality


class RawSession(_Base):
    record_type: Literal["session"]
    session_id: str | None = None
    charger_id: str | None = None
    station_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    energy_kwh: RawNumber = None
    peak_kw: RawNumber = None
    duration_min: RawNumber = None
    fault_code: str | None = None
    provenance: Provenance
    quality: Quality


class RawMaintenance(_Base):
    record_type: Literal["maintenance"]
    event_id: str
    charger_id: str | None = None
    station_id: str | None = None
    event_date: str | None = None
    event_type: str | None = None
    severity: str | None = None
    description: str
    extracted_fields: dict[str, Any] = {}
    provenance: Provenance
    quality: Quality


# ------------------------------------------------------------- P2 target enums

Connector = Literal["J1772", "CCS", "CHAdeMO", "NACS", "OTHER"]
Level = Literal["L1", "L2", "DCFC"]
ChargerStatus = Literal["ACTIVE", "INACTIVE", "MAINTENANCE", "UNKNOWN"]
MaintSeverity = Literal["INFO", "MINOR", "MAJOR", "SAFETY"]
MaintEventType = Literal["INSPECTION", "REPAIR", "FAULT", "INSTALL", "COMPLAINT", "OTHER"]
AnomalySeverity = Literal["INFO", "WARN", "CRITICAL", "SAFETY"]
HealthState = Literal["HEALTHY", "DEGRADED", "SUSPECT_OUTAGE", "SAFETY_REVIEW"]


def _check_iso(value: str | None, kind: str) -> str | None:
    if value is None:
        return None
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        if kind == "date":
            date.fromisoformat(text)
        else:
            datetime.fromisoformat(text)
    except ValueError as e:
        raise ValueError(f"not ISO-8601 {kind}: {value!r}") from e
    return value


# ---------------------------------------------------------- P2 validated models

class ValidatedCharger(_Base):
    record_type: Literal["charger"]
    charger_id: str | None = None
    station_id: str | None = None
    network: str | None = None
    address: Address | None = None
    lat: float | None = None
    lon: float | None = None
    connector_type: Connector | None = None
    power_kw: float | None = None
    level: Level | None = None
    status: ChargerStatus | None = None
    install_date: str | None = None
    provenance: Provenance
    quality: Quality

    @field_validator("install_date")
    @classmethod
    def _iso_date(cls, v):
        return _check_iso(v, "date")


class ValidatedSession(_Base):
    record_type: Literal["session"]
    session_id: str | None = None
    charger_id: str | None = None
    station_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    energy_kwh: float | None = None
    peak_kw: float | None = None
    duration_min: float | None = None
    fault_code: str | None = None
    provenance: Provenance
    quality: Quality

    @field_validator("start_time", "end_time")
    @classmethod
    def _iso_dt(cls, v):
        return _check_iso(v, "datetime")


class ValidatedMaintenance(_Base):
    record_type: Literal["maintenance"]
    event_id: str
    charger_id: str | None = None
    station_id: str | None = None
    event_date: str | None = None
    event_type: MaintEventType | None = None
    severity: MaintSeverity | None = None
    description: str
    extracted_fields: dict[str, Any] = {}
    provenance: Provenance
    quality: Quality


# ------------------------------------------------------------- P3 golden models

class MergedFrom(_Base):
    source: str
    raw_ref: str | None = None


class ConflictResolved(_Base):
    field: str
    chosen: Any = None
    rejected: list[Any] = []
    rule: str


class Anomaly(_Base):
    type: str
    severity: AnomalySeverity
    detail: str
    evidence: list[str] = []


class Health(_Base):
    state: HealthState
    since: str | None = None
    evidence: list[str] = []


class ChargerMetrics(_Base):
    est_uptime_pct: float | None = None
    fault_recurrence_count: int = 0
    reporting_lag_p50_days: float | None = None
    reporting_lag_p95_days: float | None = None


class GoldenCharger(ValidatedCharger):
    golden_id: str
    merged_from: list[MergedFrom] = []
    conflicts_resolved: list[ConflictResolved] = []
    anomalies: list[Anomaly] = []
    health: Health
    metrics: ChargerMetrics


class GoldenSession(ValidatedSession):
    golden_id: str
    merged_from: list[MergedFrom] = []
    conflicts_resolved: list[ConflictResolved] = []
    anomalies: list[Anomaly] = []


class GoldenMaintenance(ValidatedMaintenance):
    golden_id: str


# ------------------------------------------------- §5.5 frozen report schemas

class PerSourceQuality(_Base):
    records: int
    avg_score: float
    issues: dict[str, int] = {}


class ValidationReport(_Base):
    records_in: int
    records_out: int
    quarantined: int
    per_source: dict[str, PerSourceQuality] = {}
    per_issue: dict[str, int] = {}


class LagStats(_Base):
    p50: float | None = None
    p95: float | None = None


class ReconciliationReport(_Base):
    records_in: int
    golden_records_out: int
    duplicates_removed: int
    clusters: int
    conflicts_resolved: int
    anomalies: dict[str, int] = {}
    per_source_lag_days: dict[str, LagStats] = {}


class StageHealth(_Base):
    records_in: int
    records_out: int
    duration_s: float
    llm_calls: int
    llm_tokens: int
    exit_code: int


class PipelineHealth(_Base):
    stages: dict[str, StageHealth]
    run_started: str
    run_finished: str


# ------------------------------------------------------------------ dispatchers

_RAW = {"charger": RawCharger, "session": RawSession, "maintenance": RawMaintenance}
_VALIDATED = {"charger": ValidatedCharger, "session": ValidatedSession, "maintenance": ValidatedMaintenance}
_GOLDEN = {"charger": GoldenCharger, "session": GoldenSession, "maintenance": GoldenMaintenance}


def _dispatch(models: dict, obj: dict):
    rt = obj.get("record_type")
    model = models.get(rt)
    if model is None:
        raise ValueError(f"unknown record_type: {rt!r}")
    return model.model_validate(obj)


def parse_canonical_line(obj: dict):
    return _dispatch(_RAW, obj)


def parse_validated_line(obj: dict):
    return _dispatch(_VALIDATED, obj)


def parse_golden_line(obj: dict):
    return _dispatch(_GOLDEN, obj)
