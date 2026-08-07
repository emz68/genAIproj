"""P2 (normalized) canonical schema models — src/validation/.

Re-declared locally from PRODUCT_DEVELOPMENT_MANAGER.md §5.1–§5.3.  The
schema is frozen; "each module re-declares pydantic models locally from this
spec (no cross-module imports before the merge — duplication here is
deliberate)" (§5).  These models enforce the post-validation (P2) enums and
ISO formats; ingestion (P1) output is raw-string and is normalized first.

Conventions (PDM §5.0):
- ``extra="allow"`` everywhere: unknown/forward fields are preserved, never
  rejected.
- ``None`` values are omitted on dump ("writers should omit rather than emit
  nulls"); readers must treat absent and null identically.
- ``quality.score`` lifecycle: ingestion writes an initial score = parsing
  confidence; validation moves that value to ``quality.extraction_confidence``
  and recomputes ``score`` as the issue-severity-weighted final value.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Frozen format contracts (§5.1–§5.3 P2 targets)
# ---------------------------------------------------------------------------

ISO_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
ISO_DATETIME_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})$"

ISO_DATE = Annotated[str | None, Field(pattern=ISO_DATE_RE)]
ISO_DATETIME = Annotated[str | None, Field(pattern=ISO_DATETIME_RE)]

ConnectorType = Literal["J1772", "CCS", "CHAdeMO", "NACS", "OTHER"]
Level = Literal["L1", "L2", "DCFC"]
ChargerStatus = Literal["ACTIVE", "INACTIVE", "MAINTENANCE", "UNKNOWN"]
EventType = Literal["INSPECTION", "REPAIR", "FAULT", "INSTALL", "COMPLAINT", "OTHER"]
MaintenanceSeverity = Literal["INFO", "MINOR", "MAJOR", "SAFETY"]


class Provenance(BaseModel):
    """§5.1 provenance block — required for every record type."""

    model_config = ConfigDict(extra="allow")

    source: str
    source_file: str
    ingested_at: str
    raw_ref: str | None = None


class Quality(BaseModel):
    """§5.1 quality block — lifecycle per §5.0 (see module docstring)."""

    model_config = ConfigDict(extra="allow")

    score: float | None = None
    extraction_confidence: float | None = None
    issues: list[str] = Field(default_factory=list)
    fixes_applied: list[str] = Field(default_factory=list)


class Address(BaseModel):
    """§5.1 address block."""

    model_config = ConfigDict(extra="allow")

    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None


class _RecordBase(BaseModel):
    """Shared behavior: preserve extras, omit nulls when dumped."""

    model_config = ConfigDict(extra="allow")

    def dump(self) -> dict[str, Any]:
        """JSON-ready dict; null fields omitted (PDM §5.0)."""
        return self.model_dump(exclude_none=True, mode="json")


class ChargerRecord(_RecordBase):
    """§5.1 — one physical charger port, P2-normalized."""

    record_type: Literal["charger"]
    charger_id: str | None = None
    station_id: str | None = None
    network: str | None = None
    address: Address | None = None
    lat: float | None = None
    lon: float | None = None
    connector_type: ConnectorType | None = None
    power_kw: float | None = None
    level: Level | None = None
    status: ChargerStatus | None = None
    install_date: ISO_DATE = None
    provenance: Provenance
    quality: Quality


class SessionRecord(_RecordBase):
    """§5.2 — one charging session, P2-normalized."""

    record_type: Literal["session"]
    session_id: str | None = None
    charger_id: str | None = None
    station_id: str | None = None
    start_time: ISO_DATETIME = None
    end_time: ISO_DATETIME = None
    energy_kwh: float | None = None
    peak_kw: float | None = None
    duration_min: float | None = None
    fault_code: str | None = None
    provenance: Provenance
    quality: Quality


class MaintenanceEvent(_RecordBase):
    """§5.3 — parsed from unstructured contractor reports, P2-normalized."""

    record_type: Literal["maintenance"]
    event_id: str
    charger_id: str | None = None
    station_id: str | None = None
    event_date: ISO_DATE = None
    event_type: EventType | None = None
    severity: MaintenanceSeverity | None = None
    description: str
    extracted_fields: dict[str, Any] | None = None
    provenance: Provenance
    quality: Quality


RECORD_TYPES: dict[str, type[_RecordBase]] = {
    "charger": ChargerRecord,
    "session": SessionRecord,
    "maintenance": MaintenanceEvent,
}


def build_record(data: dict[str, Any]) -> _RecordBase:
    """Build the P2 model for a record dict.

    Raises ``ValueError`` for an unknown ``record_type``; the pipeline
    treats that as an unrecoverable structural problem (PDM guarantees only
    the three canonical types, §5.4).
    """
    record_type = str(data.get("record_type") or "")
    model = RECORD_TYPES.get(record_type)
    if model is None:
        raise ValueError(f"unknown record_type: {record_type!r}")
    return model.model_validate(data)
