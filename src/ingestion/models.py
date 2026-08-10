"""P1 (ingestion) canonical record models.

Frozen schema: PRODUCT_DEVELOPMENT_MANAGER.md §5.1-5.3.
P1 semantics (§5.0):
- All source-value fields are free strings, passed through **verbatim** - no
  date/unit/enum normalization (that is P2's job). A value like
  ``connector_type="chgpt LVL2 dual"`` or ``start_time="3/4/24 2pm"`` is valid
  P1 output.
- Unknown extra fields are preserved, never rejected (``extra="allow"``).
  Downstream stages add fields; upstream sources may attach raw payload extras.
- Absent == null: writers omit null keys rather than emitting nulls.
- ``quality.score`` at ingestion time = parsing/extraction confidence (§5.0);
  ``extraction_confidence`` stays null until P2 moves the value there.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Provenance(BaseModel):
    """Where a canonical record came from (schema §5.1)."""

    model_config = ConfigDict(extra="allow")

    source: str
    source_file: str
    ingested_at: str
    raw_ref: Optional[str] = None


class Quality(BaseModel):
    """Parsing/extraction quality (schema §5.1, lifecycle per §5.0)."""

    model_config = ConfigDict(extra="allow")

    score: Optional[float] = None
    extraction_confidence: Optional[float] = None
    issues: List[str] = Field(default_factory=list)
    fixes_applied: List[str] = Field(default_factory=list)


class ChargerRecord(BaseModel):
    """One physical charger port (schema §5.1)."""

    model_config = ConfigDict(extra="allow")

    record_type: Literal["charger"] = "charger"
    charger_id: Optional[str] = None
    station_id: Optional[str] = None
    network: Optional[str] = None
    address: Optional[Dict[str, Optional[str]]] = None
    lat: Optional[str] = None
    lon: Optional[str] = None
    connector_type: Optional[str] = None
    power_kw: Optional[str] = None
    level: Optional[str] = None
    status: Optional[str] = None
    install_date: Optional[str] = None
    provenance: Provenance
    quality: Quality


class SessionRecord(BaseModel):
    """One charging session (schema §5.2)."""

    model_config = ConfigDict(extra="allow")

    record_type: Literal["session"] = "session"
    session_id: Optional[str] = None
    charger_id: Optional[str] = None
    station_id: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    energy_kwh: Optional[str] = None
    peak_kw: Optional[str] = None
    duration_min: Optional[str] = None
    fault_code: Optional[str] = None
    provenance: Provenance
    quality: Quality


class MaintenanceEvent(BaseModel):
    """One maintenance event parsed from unstructured contractor reports (schema §5.3)."""

    model_config = ConfigDict(extra="allow")

    record_type: Literal["maintenance"] = "maintenance"
    event_id: str
    charger_id: Optional[str] = None
    station_id: Optional[str] = None
    event_date: Optional[str] = None
    event_type: Optional[str] = None
    severity: Optional[str] = None
    description: str
    extracted_fields: Dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
    quality: Quality


CanonicalRecord = ChargerRecord | SessionRecord | MaintenanceEvent
