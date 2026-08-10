"""Standardizers (S2) — src/validation/.

Value normalization toward the P2 targets of PDM §5.1–§5.3:
- dates → ISO-8601 with timezone (naive datetimes get the configured default
  timezone, ``America/New_York``); date-only fields → ``YYYY-MM-DD``
- unit repair (W→kW, Wh→kWh) via magnitude heuristics, thresholds from YAML
- connector / level / status / event_type / severity → §5 enums via the
  YAML synonym tables (deterministic ``--no-llm`` path); unresolvable values
  map to ``OTHER`` / ``UNKNOWN`` where the enum provides them, otherwise the
  value is left for the rules to flag
- address / zip cleanup and whitespace / casing cleanup on short fields

Every change is recorded as a ``fixes_applied`` entry
(``action:field:old→new``).  Values that cannot be standardized are left
unchanged — the rule engine flags them and the pipeline stashes the raw
value before dropping it.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as dateutil_parser

# Explicit formats tried before the lenient parser.  Dotted dates are
# day-first (contractor-report convention, e.g. "01.02.2023"); slashed and
# dashed dates are month-first / ISO.
_EXPLICIT_DATE_FORMATS = (
    "%d.%m.%Y",
    "%d.%m.%y",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d",
    "%Y/%m/%d",
)

_STATE_NAMES = {
    "north carolina": "NC", "new york": "NY", "new jersey": "NJ",
    "california": "CA", "texas": "TX", "florida": "FL", "virginia": "VA",
    "south carolina": "SC", "georgia": "GA", "pennsylvania": "PA",
    "massachusetts": "MA", "illinois": "IL", "ohio": "OH", "maryland": "MD",
}

_NUMERIC_FIELDS = {
    "charger": ("power_kw", "lat", "lon"),
    "session": ("energy_kwh", "peak_kw", "duration_min"),
}

_DATE_FIELDS = {
    "charger": ("install_date",),
    "maintenance": ("event_date",),
}

_DATETIME_FIELDS = {
    "session": ("start_time", "end_time"),
    "all": ("provenance.ingested_at",),
}

_ENUM_FIELDS = {
    "charger": ("connector_type", "level", "status"),
    "maintenance": ("event_type", "severity"),
}

# Enum fields whose enum has an explicit catch-all member.
_FALLBACK_ENUM = {"connector_type": "OTHER", "status": "UNKNOWN", "event_type": "OTHER"}

# Short free-text fields that get trim + whitespace-collapse (not description,
# which is paragraph text).
_WHITESPACE_FIELDS = (
    "charger_id", "station_id", "network", "session_id", "fault_code",
    "event_id", "event_type", "severity", "connector_type", "level", "status",
    "street", "city", "state", "zip", "source", "source_file", "raw_ref",
)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _word_regex(synonym: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(synonym)}(?![a-z0-9])")


class Standardizer:
    """Applies P2 standardization to one record.  Thread-safe once built."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.default_tz = ZoneInfo(
            config.get("standardize", {}).get("default_timezone", "America/New_York")
        )
        self._synonym_regexes: dict[str, list[tuple[str, re.Pattern]]] = {}
        for field, table in config.get("synonyms", {}).items():
            compiled = []
            for enum_name, synonyms in table.items():
                # Longer / more specific synonyms win within an enum's list
                # ("urgent-safety" must beat "urgent"); table order decides
                # between enums (SAFETY before MAJOR).
                for synonym in sorted(synonyms, key=len, reverse=True):
                    compiled.append((enum_name, _word_regex(_collapse(synonym.lower()))))
            self._synonym_regexes[field] = compiled

    # -- public API ---------------------------------------------------------

    def standardize_record(self, record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Return ``(record, fixes)``; the record dict is copied and edited."""
        record = dict(record)
        fixes: list[str] = []
        record_type = str(record.get("record_type") or "")

        self._standardize_whitespace(record, fixes)
        self._standardize_provenance(record, fixes)
        if record_type == "charger":
            self._standardize_address(record, fixes)
        self._standardize_numerics(record, record_type, fixes)
        self._standardize_dates(record, record_type, fixes)
        self._standardize_enums(record, record_type, fixes)
        return record, fixes

    # -- internals ----------------------------------------------------------

    def _set(self, record: dict, dotted: str, new_value: Any, fixes: list[str], action: str):
        parts = dotted.split(".")
        container = record
        for part in parts[:-1]:
            container = container.setdefault(part, {})
        old = container.get(parts[-1])
        if old == new_value:
            return
        container[parts[-1]] = new_value
        fixes.append(f"{action}:{dotted}:{old!r}→{new_value!r}")

    def _get(self, record: dict, dotted: str) -> Any:
        container: Any = record
        for part in dotted.split("."):
            if not isinstance(container, dict) or part not in container:
                return None
            container = container[part]
        return container

    def _standardize_whitespace(self, record: dict, fixes: list[str]) -> None:
        address = record.get("address")
        if isinstance(address, dict):
            for field in ("street", "city", "state", "zip"):
                self._clean_string_field(record, f"address.{field}", fixes)
        for field in _WHITESPACE_FIELDS:
            self._clean_string_field(record, field, fixes)
        # Free-text description: trim outer whitespace only (paragraph text).
        description = record.get("description")
        if isinstance(description, str):
            cleaned = description.strip()
            if cleaned != description:
                self._set(record, "description", cleaned, fixes, "trim")

    def _clean_string_field(self, record: dict, dotted: str, fixes: list[str]) -> None:
        value = self._get(record, dotted)
        if not isinstance(value, str):
            return
        cleaned = _collapse(value)
        if cleaned == "":
            if value.strip() != "":
                self._set(record, dotted, None, fixes, "drop_blank")
            return
        if cleaned != value:
            self._set(record, dotted, cleaned, fixes, "trim")

    def _standardize_provenance(self, record: dict, fixes: list[str]) -> None:
        provenance = record.get("provenance")
        if not isinstance(provenance, dict):
            return
        for field in ("source", "source_file", "raw_ref"):
            value = provenance.get(field)
            if isinstance(value, str):
                cleaned = _collapse(value)
                if cleaned != value:
                    self._set(record, f"provenance.{field}", cleaned, fixes, "trim")

    def _standardize_address(self, record: dict, fixes: list[str]) -> None:
        address = record.get("address")
        if not isinstance(address, dict):
            return
        # Zip: keep the first 5-digit run (drop +4 / trailing letters).
        zip_value = address.get("zip")
        if isinstance(zip_value, str):
            match = re.search(r"\b\d{5}\b", zip_value)
            if match:
                cleaned = match.group(0)
                if cleaned != zip_value.strip():
                    self._set(record, "address.zip", cleaned, fixes, "zip")
            elif zip_value.strip():
                self._set(record, "address.zip", None, fixes, "zip_invalid")
        # State: uppercase; expand known full names to the 2-letter code.
        state = address.get("state")
        if isinstance(state, str):
            cleaned = _collapse(state)
            lowered = cleaned.lower()
            if lowered in _STATE_NAMES:
                cleaned = _STATE_NAMES[lowered]
            elif re.fullmatch(r"[a-z]{2}", cleaned, re.IGNORECASE):
                cleaned = cleaned.upper()
            if cleaned != state:
                self._set(record, "address.state", cleaned, fixes, "case")

    def _standardize_numerics(self, record: dict, record_type: str, fixes: list[str]) -> None:
        for field in _NUMERIC_FIELDS.get(record_type, ()):
            value = self._get(record, field)
            if value is None or isinstance(value, bool):
                continue
            number = value
            if isinstance(value, str):
                try:
                    number = float(value.strip())
                except (TypeError, ValueError):
                    continue  # rules flag invalid_<field>
                if not math.isfinite(number):
                    continue
                if number != value:
                    self._set(record, field, number, fixes, "coerce")
            repaired = self._repair_units(record, field, float(number), fixes)
            if repaired is not None:
                self._set(record, field, repaired, fixes, "unit_repair")

    def _repair_units(self, record: dict, field: str, value: float, fixes: list[str]) -> float | None:
        spec = self.config.get("unit_repair", {}).get(field)
        if not spec:
            return None
        threshold = spec.get("watts_if_above", spec.get("wh_if_above"))
        max_plausible = spec.get("max_plausible", float("inf"))
        if threshold is not None and value > float(threshold) and value <= float(max_plausible):
            return value / 1000.0
        return None

    def _standardize_dates(self, record: dict, record_type: str, fixes: list[str]) -> None:
        for field in _DATE_FIELDS.get(record_type, ()):
            value = self._get(record, field)
            if not isinstance(value, str) or not value.strip():
                continue
            parsed = self._parse_datetime(value)
            if parsed is None:
                continue  # rules flag invalid_<field>
            iso = parsed.date().isoformat()
            if iso != value.strip():
                self._set(record, field, iso, fixes, "date")
        for field in _DATETIME_FIELDS.get(record_type, ()) + _DATETIME_FIELDS.get("all", ()):
            value = self._get(record, field)
            if not isinstance(value, str) or not value.strip():
                continue
            parsed = self._parse_datetime(value)
            if parsed is None:
                continue
            iso = parsed.isoformat()
            if iso != value.strip():
                self._set(record, field, iso, fixes, "date")

    def _parse_datetime(self, value: str) -> datetime | None:
        text = value.strip()
        for fmt in _EXPLICIT_DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            parsed = dateutil_parser.parse(text, fuzzy=False)
        except (ValueError, OverflowError, TypeError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=self.default_tz)
        return parsed

    def _standardize_enums(self, record: dict, record_type: str, fixes: list[str]) -> None:
        for field in _ENUM_FIELDS.get(record_type, ()):
            value = self._get(record, field)
            if not isinstance(value, str) or not value.strip():
                continue
            normalized = self._match_enum(field, value)
            if normalized is not None:
                if normalized != value:
                    self._set(record, field, normalized, fixes, "enum")
                continue
            fallback = _FALLBACK_ENUM.get(field)
            if fallback is not None:
                self._set(record, field, fallback, fixes, "enum_fallback")
            # else: leave value; rules flag invalid_<field>

    def _match_enum(self, field: str, value: str) -> str | None:
        text = _collapse(value.lower())
        for enum_name, regex in self._synonym_regexes.get(field, []):
            if regex.search(text):
                return enum_name
        return None
