"""Declarative rule engine (S1, S4) — src/validation/.

Loads the rule catalog from ``rules.yaml`` (or a deep-merged override) and
evaluates a single record against it.  Rules are data: adding a rule or
changing a severity/bbox/threshold never requires code changes.

Issue model: every issue is ``{code, severity, message, field}`` with a
stable snake_case ``code``.  No cross-record logic lives here (PDM §7
boundary rules: Sanja judges records one at a time; duplicates pass through
untouched and unflagged).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from .models import ISO_DATE_RE, ISO_DATETIME_RE

_RULES_YAML = Path(__file__).with_name("rules.yaml")


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base`` (dicts merge, other
    values replace).  Used for ``--rules`` override files."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class Rules:
    """Loaded rule catalog with typed accessors."""

    config: dict[str, Any]
    rules: list[dict[str, Any]] = field(default_factory=list)
    _by_id: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, override_path: str | Path | None = None) -> "Rules":
        config = yaml.safe_load(_RULES_YAML.read_text(encoding="utf-8")) or {}
        if override_path:
            override = yaml.safe_load(Path(override_path).read_text(encoding="utf-8")) or {}
            config = deep_merge(config, override)
        rules = list(config.get("rules", []))
        by_id: dict[str, dict[str, Any]] = {}
        for rule in rules:
            by_id.setdefault(rule["id"], rule)
        return cls(config=config, rules=rules, _by_id=by_id)

    # -- config accessors ---------------------------------------------------

    def get_rule(self, rule_id: str) -> dict[str, Any]:
        return self._by_id[rule_id]

    def bbox(self) -> dict[str, float]:
        return self.config["territory"]["bbox"]

    def stale_max_days(self) -> int:
        return int(self.config["stale"]["max_days"])

    def duration_tolerance_min(self) -> float:
        return float(self.get_rule("duration_mismatch").get("tolerance_min", 5))

    def scoring(self) -> dict[str, Any]:
        return self.config["scoring"]

    def required_fields(self, record_type: str) -> list[str]:
        return list(self.config["required_fields"].get(record_type, []))

    def enum_members(self, enum_name: str) -> list[str]:
        return list(self.config["enums"].get(enum_name, []))

    # -- evaluation ---------------------------------------------------------

    def evaluate(self, record: dict[str, Any]) -> list[Issue]:
        """Evaluate one record against every applicable rule."""
        record_type = str(record.get("record_type") or "")
        issues: list[Issue] = []
        for rule in self.rules:
            applies_to = rule.get("record_type")
            if applies_to and applies_to != record_type:
                continue
            check = str(rule.get("check") or "")
            evaluator = _EVALUATORS.get(check)
            if evaluator is None:
                raise KeyError(f"unknown rule check: {check!r}")
            issues.extend(evaluator(self, record, rule))
        return issues


# ---------------------------------------------------------------------------
# Evaluators.  Each returns a list of Issue for one rule.
# ---------------------------------------------------------------------------


def _get_path(data: dict, dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        try:
            result = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    return result if math.isfinite(result) else None


def _parse_datetime(value: Any) -> datetime | None:
    """Parse ISO-8601 (with or without tz) leniently; None on failure."""
    if value is None:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _check_required(rules: Rules, record: dict, rule: dict) -> list[Issue]:
    record_type = str(record.get("record_type") or "")
    issues = []
    for dotted in rules.required_fields(record_type):
        if _get_path(record, dotted) is None:
            code = "missing_" + dotted.replace(".", "_")
            issues.append(
                Issue(code=code, severity=rule["severity"], field=dotted,
                      message=f"required field is missing: {dotted}")
            )
    return issues


def _check_numeric(rules: Rules, record: dict, rule: dict) -> list[Issue]:
    field = rule["field"]
    value = _get_path(record, field)
    if value is None:
        return []
    if _to_float(value) is None:
        return [Issue(code=rule["id"], severity=rule["severity"], field=field,
                      message=f"field is not numeric: {field}={value!r}")]
    return []


def _check_range(rules: Rules, record: dict, rule: dict) -> list[Issue]:
    field = rule["field"]
    value = _to_float(_get_path(record, field))
    if value is None:
        return []
    if "min" in rule and value < float(rule["min"]):
        return [Issue(code=rule["id"], severity=rule["severity"], field=field,
                      message=f"{field}={value} below minimum {rule['min']}")]
    if "max" in rule and value > float(rule["max"]):
        return [Issue(code=rule["id"], severity=rule["severity"], field=field,
                      message=f"{field}={value} above maximum {rule['max']}")]
    return []


def _check_enum(rules: Rules, record: dict, rule: dict) -> list[Issue]:
    field = rule["field"]
    value = _get_path(record, field)
    if value is None:
        return []
    members = rules.enum_members(field)
    if str(value) not in members:
        return [Issue(code=rule["id"], severity=rule["severity"], field=field,
                      message=f"{field}={value!r} is not a valid {field} value")]
    return []


def _check_date(rules: Rules, record: dict, rule: dict) -> list[Issue]:
    field = rule["field"]
    value = _get_path(record, field)
    if value is None:
        return []
    if not re.fullmatch(ISO_DATE_RE, str(value)):
        return [Issue(code=rule["id"], severity=rule["severity"], field=field,
                      message=f"{field}={value!r} is not an ISO-8601 date")]
    return []


def _check_datetime(rules: Rules, record: dict, rule: dict) -> list[Issue]:
    field = rule["field"]
    value = _get_path(record, field)
    if value is None:
        return []
    if not re.fullmatch(ISO_DATETIME_RE, str(value)):
        return [Issue(code=rule["id"], severity=rule["severity"], field=field,
                      message=f"{field}={value!r} is not ISO-8601 with timezone")]
    return []


def _check_order(rules: Rules, record: dict, rule: dict) -> list[Issue]:
    earlier = _parse_datetime(_get_path(record, rule["earlier"]))
    later = _parse_datetime(_get_path(record, rule["later"]))
    if earlier is None or later is None:
        return []
    if later <= earlier:
        return [Issue(code=rule["id"], severity=rule["severity"], field=rule["later"],
                      message=f"{rule['later']} is not after {rule['earlier']}")]
    return []


def _check_duration_matches(rules: Rules, record: dict, rule: dict) -> list[Issue]:
    start = _parse_datetime(_get_path(record, "start_time"))
    end = _parse_datetime(_get_path(record, "end_time"))
    duration = _to_float(_get_path(record, "duration_min"))
    if start is None or end is None or duration is None:
        return []
    if end <= start:
        return []  # end_before_start owns that case; don't double-report
    computed = (end - start).total_seconds() / 60.0
    tolerance = float(rule.get("tolerance_min", rules.duration_tolerance_min()))
    if abs(duration - computed) > tolerance:
        return [Issue(code=rule["id"], severity=rule["severity"], field="duration_min",
                      message=f"duration_min={duration} does not match end−start "
                              f"({computed:.2f} min, tolerance {tolerance} min)")]
    return []


def _check_bbox(rules: Rules, record: dict, rule: dict) -> list[Issue]:
    lat = _to_float(_get_path(record, "lat"))
    lon = _to_float(_get_path(record, "lon"))
    if lat is None or lon is None:
        return []
    box = rules.bbox()
    inside = (
        box["min_lat"] <= lat <= box["max_lat"]
        and box["min_lon"] <= lon <= box["max_lon"]
    )
    if not inside:
        return [Issue(code=rule["id"], severity=rule["severity"], field="lat/lon",
                      message=f"({lat}, {lon}) outside territory {box}")]
    return []


def _check_stale(rules: Rules, record: dict, rule: dict) -> list[Issue]:
    """Per-record staleness (S4): ingested_at − event/session date > 7 days.

    Sessions use end_time (fallback start_time); maintenance events use
    event_date; charger records have no event/session/report date and are
    never flagged.  Lag aggregates are Yash's (P3), not Sanja's.
    """
    record_type = str(record.get("record_type") or "")
    if record_type == "session":
        event = _parse_datetime(_get_path(record, "end_time")) or _parse_datetime(
            _get_path(record, "start_time")
        )
    elif record_type == "maintenance":
        event = _parse_datetime(_get_path(record, "event_date"))
    else:
        return []
    if event is None:
        return []
    ingested = _parse_datetime(_get_path(record, "provenance.ingested_at"))
    if ingested is None:
        return []
    # Normalize tz-awareness: date-only values (event_date) are naive; treat
    # them as UTC so the subtraction is well-defined.
    if event.tzinfo is None:
        event = event.replace(tzinfo=timezone.utc)
    if ingested.tzinfo is None:
        ingested = ingested.replace(tzinfo=timezone.utc)
    max_days = rules.stale_max_days()
    if ingested - event > timedelta(days=max_days):
        return [Issue(code=rule["id"], severity=rule["severity"], field="provenance.ingested_at",
                      message=f"report ingested {ingested.isoformat()} but event dated "
                              f"{event.isoformat()}: > {max_days} days stale")]
    return []


_EVALUATORS: dict[str, Callable[[Rules, dict, dict], list[Issue]]] = {
    "required": _check_required,
    "numeric": _check_numeric,
    "range": _check_range,
    "enum": _check_enum,
    "date": _check_date,
    "datetime": _check_datetime,
    "order": _check_order,
    "duration_matches": _check_duration_matches,
    "bbox": _check_bbox,
    "stale": _check_stale,
}
