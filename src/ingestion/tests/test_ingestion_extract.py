"""Maintenance extraction tests (E3): regex --no-llm path + LLM path + fallback."""

import json

import pytest

from src.ingestion import extract

FIXTURES = extract.os.path.join(extract.os.path.dirname(__file__), "..", "fixtures", "contractor")


def _text(name: str) -> str:
    with open(extract.os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


class TestRegexPath:
    def test_email(self):
        events = extract.parse_maintenance_regex(_text("report_email.txt"), "report_email.txt")
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "REPAIR"  # latch broken
        assert ev["severity"] == "SAFETY"  # urgent-safety
        assert ev["station_name"] == "TOWN OF CARY / DT DECK P2 (2)"
        assert ev["event_date"] == "01.02.2023"  # verbatim, never converted
        assert ev["extracted_fields"]["power_rating"] == "50 kW DCFC"
        assert ev["extracted_fields"]["address"] == "113 Walnut St 27511"
        assert ev["confidence"] >= 0.8

    def test_digest_multiple_events(self):
        events = extract.parse_maintenance_regex(_text("report_digest.txt"), "report_digest.txt")
        assert len(events) == 2
        ev0, ev1 = events
        assert ev0["event_type"] == "FAULT"
        assert ev0["severity"] == "INFO"  # sev=fyi
        assert ev0["station_name"] == "TOWN OF CARY / P5_DTCARYDECKE"
        assert ev0["event_date"] == "2023-10-01"  # period ending
        assert ev0["extracted_fields"]["count"] == "3"
        assert ev1["severity"] == "MINOR"  # sev=minor
        assert ev1["extracted_fields"]["notes"] == "units in W not kW on this feed"
        assert ev0["event_id"] != ev1["event_id"]

    def test_inspection(self):
        events = extract.parse_maintenance_regex(_text("report_inspection.txt"), "report_inspection.txt")
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "INSPECTION"
        assert ev["severity"] == "SAFETY"  # severity code [safety!!]
        assert ev["station_name"] == "TOWN OF CARY / P3_DTCARYDECKE"
        assert ev["event_date"] == "2022-02-08"
        assert ev["extracted_fields"]["power_rating"] == "6600 W"  # unit note preserved
        assert ev["extracted_fields"]["technician"] == "D.K."

    def test_complaint(self):
        events = extract.parse_maintenance_regex(_text("report_complaint.txt"), "report_complaint.txt")
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "COMPLAINT"
        assert ev["severity"] == "MINOR"
        assert ev["event_date"] == "2021-09-25"
        assert ev["extracted_fields"]["location"] == "119 E Park St"

    def test_event_id_deterministic(self):
        a = extract.parse_maintenance_regex(_text("report_digest.txt"), "report_digest.txt")
        b = extract.parse_maintenance_regex(_text("report_digest.txt"), "report_digest.txt")
        assert [e["event_id"] for e in a] == [e["event_id"] for e in b]
        assert all(e["event_id"].startswith("maint-") for e in a)

    def test_severity_vocabulary(self):
        events = extract.parse_maintenance_regex(_text("report_email.txt"), "x.txt")
        assert events[0]["severity"] in extract.SEVERITIES
        for e in extract.parse_maintenance_regex(_text("report_digest.txt"), "x.txt"):
            assert e["severity"] in extract.SEVERITIES


class TestLlmPath:
    class FakeClient:
        """Exposes client.messages.create(...) like the anthropic SDK."""

        def __init__(self, payload, usage=None):
            self.payload = payload
            self.usage = usage or {"input_tokens": 10, "output_tokens": 5}
            self.calls = 0

        class _Messages:
            def __init__(self, owner):
                self.owner = owner

            def create(self, **kwargs):
                self.owner.calls += 1
                self.owner.last_kwargs = kwargs

                class _Content:
                    def __init__(self, text):
                        self.text = text

                class _Resp:
                    def __init__(self, content, usage):
                        self.content = content
                        self.usage = type("U", (), usage)()

                return _Resp([_Content(self.owner.payload)], self.owner.usage)

        @property
        def messages(self):
            return self._Messages(self)

    def test_llm_extracts_and_counts_tokens(self):
        payload = json.dumps(
            [{
                "event_type": "FAULT",
                "severity": "MAJOR",
                "event_date": "24.01.2023",
                "station_name": "TOWN OF CARY / P6_DTCARYDECKE",
                "description": "display flicker, unit resets",
                "extracted_fields": {"count": "9"},
                "confidence": 0.95,
            }]
        )
        client = self.FakeClient(payload)
        stats = {"llm_calls": 0, "llm_tokens": 0}
        events = extract.parse_maintenance_llm(
            "some report text", "report.txt", client, stats=stats
        )
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "FAULT"
        assert ev["severity"] == "MAJOR"
        assert ev["event_date"] == "24.01.2023"
        assert ev["confidence"] == 0.95
        assert stats["llm_calls"] == 1
        assert stats["llm_tokens"] == 15
        assert client.calls == 1
        assert "system" in client.last_kwargs

    def test_parse_llm_json_strips_fences(self):
        raw = '```json\n[{"event_type": "FAULT", "confidence": 0.8}]\n```'
        data = extract._parse_llm_json(raw)
        assert data[0]["event_type"] == "FAULT"

    def test_parse_llm_json_accepts_single_object(self):
        data = extract._parse_llm_json('{"event_type": "FAULT", "confidence": 0.8}')
        assert isinstance(data, list) and len(data) == 1

    def test_dispatch_falls_back_on_llm_failure(self):
        class Boom:
            def messages_create(self, **kwargs):
                raise RuntimeError("api down")

        events, issue = extract.parse_maintenance(
            _text("report_email.txt"), "report_email.txt", use_llm=True, client=Boom()
        )
        assert issue == "llm_fallback"
        assert events[0]["event_type"] == "REPAIR"  # regex result

    def test_dispatch_requires_client_when_llm_requested(self):
        with pytest.raises(RuntimeError):
            extract.parse_maintenance(
                "text", "f.txt", use_llm=True, client=None
            )

    def test_no_llm_dispatch_never_touches_network(self):
        events, issue = extract.parse_maintenance(
            _text("report_digest.txt"), "report_digest.txt", use_llm=False
        )
        assert issue is None
        assert len(events) == 2
