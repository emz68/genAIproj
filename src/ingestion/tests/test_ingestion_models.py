"""P1 schema-model tests: free-string verbatim values, extra="allow", §5.0."""

import pytest
from pydantic import ValidationError

from src.ingestion.models import (
    ChargerRecord,
    MaintenanceEvent,
    Provenance,
    Quality,
    SessionRecord,
)


def _prov(**kw) -> dict:
    base = {"source": "cary", "source_file": "f.csv", "ingested_at": "2026-08-07T00:00:00+00:00"}
    base.update(kw)
    return base


class TestSchemaSemantics:
    def test_session_values_are_free_strings_verbatim(self):
        """§5.0: messy source values are valid P1 output, never normalized."""
        rec = SessionRecord(
            start_time="3/4/24 2pm",
            energy_kwh="8.463",
            duration_min="01:22:28",
            provenance=Provenance(**_prov()),
            quality=Quality(score=1.0),
        )
        assert rec.start_time == "3/4/24 2pm"
        assert rec.energy_kwh == "8.463"  # string, not float
        assert rec.record_type == "session"

    def test_charger_connector_type_verbatim(self):
        rec = ChargerRecord(
            connector_type="chgpt LVL2 dual",
            status="E",
            install_date="1287100800000",
            provenance=Provenance(**_prov(source="afdc")),
            quality=Quality(score=1.0),
        )
        assert rec.connector_type == "chgpt LVL2 dual"
        assert rec.install_date == "1287100800000"

    def test_unknown_extra_fields_preserved(self):
        """§5.0: extra="allow" — downstream/source extras must never be rejected."""
        rec = SessionRecord(
            station_name="TOWN OF CARY / DT DECK P2 (2)",
            address_1="113 Walnut St",
            weird_extra={"nested": [1, 2]},
            provenance=Provenance(**_prov()),
            quality=Quality(score=1.0),
        )
        assert rec.station_name == "TOWN OF CARY / DT DECK P2 (2)"
        assert rec.weird_extra == {"nested": [1, 2]}

    def test_absent_fields_default_null(self):
        rec = SessionRecord(provenance=Provenance(**_prov()), quality=Quality(score=1.0))
        assert rec.session_id is None
        assert rec.charger_id is None
        assert rec.end_time is None

    def test_record_type_literal_enforced(self):
        with pytest.raises(ValidationError):
            SessionRecord(
                record_type="charger",
                provenance=Provenance(**_prov()),
                quality=Quality(score=1.0),
            )

    def test_provenance_and_quality_required(self):
        with pytest.raises(ValidationError):
            SessionRecord(record_type="session")

    def test_maintenance_event_shape(self):
        ev = MaintenanceEvent(
            event_id="maint-abc123",
            event_type="FAULT",
            severity="SAFETY",
            description="breaker trip x3",
            extracted_fields={"count": "3", "notes": "units in W not kW"},
            provenance=Provenance(**_prov(source="contractor")),
            quality=Quality(score=0.9),
        )
        assert ev.record_type == "maintenance"
        assert ev.extracted_fields["count"] == "3"

    def test_quality_defaults(self):
        rec = SessionRecord(provenance=Provenance(**_prov()), quality=Quality())
        assert rec.quality.score is None
        assert rec.quality.issues == []
        assert rec.quality.fixes_applied == []
