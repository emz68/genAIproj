"""P3 pydantic models — re-declared locally from the frozen schema (§5).

Input models are deliberately lax (``extra="allow"``, free strings): P3
consumes P2-validated JSONL and must never reject a record for carrying
unknown fields (§5.0 — downstream stages add fields, upstream may attach raw
payload extras). Golden models enforce the §5.4 additions (golden_id,
merged_from, conflicts_resolved, anomalies, health, metrics).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class Lax(BaseModel):
    """Base model: preserve unknown fields, tolerate anything."""

    model_config = ConfigDict(extra="allow")


class Address(Lax):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None


class Provenance(Lax):
    source: str
    source_file: Optional[str] = None
    ingested_at: Optional[str] = None
    raw_ref: Optional[str] = None


class Quality(Lax):
    score: Optional[float] = None
    extraction_confidence: Optional[float] = None
    issues: List[str] = Field(default_factory=list)
    fixes_applied: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Input record models (§5.1–5.3, P2-normalized values)
# ---------------------------------------------------------------------------


class ChargerRecord(Lax):
    record_type: str = "charger"
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
    provenance: Provenance
    quality: Quality = Field(default_factory=Quality)


class SessionRecord(Lax):
    record_type: str = "session"
    session_id: Optional[str] = None
    charger_id: Optional[str] = None
    station_id: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    energy_kwh: Optional[float] = None
    peak_kw: Optional[float] = None
    duration_min: Optional[float] = None
    fault_code: Optional[str] = None
    provenance: Provenance
    quality: Quality = Field(default_factory=Quality)


class MaintenanceRecord(Lax):
    record_type: str = "maintenance"
    event_id: str
    charger_id: Optional[str] = None
    station_id: Optional[str] = None
    event_date: Optional[str] = None
    event_type: Optional[str] = None
    severity: Optional[str] = None
    description: str = ""
    extracted_fields: Dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
    quality: Quality = Field(default_factory=Quality)


def parse_record(data: Dict[str, Any]) -> BaseModel:
    """Dispatch an input line to its record model. Unknown types are fatal
    (the §6 contract requires every line to parse)."""
    rt = data.get("record_type")
    if rt == "charger":
        return ChargerRecord(**data)
    if rt == "session":
        return SessionRecord(**data)
    if rt == "maintenance":
        return MaintenanceRecord(**data)
    raise ValueError(f"unknown record_type: {rt!r}")


# ---------------------------------------------------------------------------
# Golden output models (§5.4)
# ---------------------------------------------------------------------------


class MergedFrom(Lax):
    source: str
    raw_ref: Optional[str] = None


class ConflictResolution(Lax):
    field: str
    chosen: Any
    rejected: List[Any] = Field(default_factory=list)
    rule: str


class Anomaly(Lax):
    type: str
    severity: str  # INFO | WARN | CRITICAL | SAFETY
    detail: str
    evidence: List[str] = Field(default_factory=list)


class Health(Lax):
    state: str = "HEALTHY"  # HEALTHY | DEGRADED | SUSPECT_OUTAGE | SAFETY_REVIEW
    since: Optional[str] = None
    evidence: List[str] = Field(default_factory=list)


class Metrics(Lax):
    est_uptime_pct: Optional[float] = None
    fault_recurrence_count: int = 0
    reporting_lag_p50_days: Optional[float] = None
    reporting_lag_p95_days: Optional[float] = None


class GoldenCharger(ChargerRecord):
    golden_id: str
    merged_from: List[MergedFrom] = Field(default_factory=list)
    conflicts_resolved: List[ConflictResolution] = Field(default_factory=list)
    anomalies: List[Anomaly] = Field(default_factory=list)
    health: Health = Field(default_factory=lambda: Health())
    metrics: Metrics = Field(default_factory=lambda: Metrics())


class GoldenSession(SessionRecord):
    golden_id: str
    merged_from: List[MergedFrom] = Field(default_factory=list)
    conflicts_resolved: List[ConflictResolution] = Field(default_factory=list)
    anomalies: List[Anomaly] = Field(default_factory=list)


class GoldenMaintenance(MaintenanceRecord):
    golden_id: str
