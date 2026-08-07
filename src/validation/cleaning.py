"""Per-record cleaning pipeline (S3, S4, S5, S6) — src/validation/.

One record in, one record out — no cross-record logic of any kind (PDM §7
boundary rules: duplicate rows pass through untouched and unflagged; Yash
owns every comparison between records).

Per-record order of operations:
1. Standardize (S2): dates, units, enums, address/zip, whitespace/casing.
2. Derive missing values where safe (S3): duration from timestamps, level
   from power_kw — each recorded in ``fixes_applied``.
3. Fix internal contradictions (S4): recompute ``duration_min`` when it
   contradicts ``end_time − start_time``.
4. LLM-assisted repair of unresolved categoricals (S5) when enabled;
   deterministic synonym fallback otherwise.
5. Final rule pass (S1): the surviving issues are the record's issues.
6. Fields the P2 models cannot represent (unparseable numbers, dates,
   unresolvable enums without a catch-all) are dropped and stashed as
   ``raw_<field>`` extras so no information is lost.
7. Score (S6): P1 ``quality.score`` → ``quality.extraction_confidence``,
   recomputed issue-weighted ``score``; quarantine decision (report-only).
"""

from __future__ import annotations

from typing import Any

from .llm import repair_enums_with_llm
from .models import build_record
from .rules import Issue, Rules
from .scoring import compute_score, extraction_confidence_from, is_quarantined
from .standardizers import Standardizer

# Issue codes whose field the P2 models cannot represent once flagged:
# the value is dropped and stashed as raw_<field> (extra key, preserved).
# provenance.ingested_at is intentionally absent: the provenance block is
# required and a free string there is still representable.
_INVALID_FIELD_BY_CODE: dict[str, str] = {
    "invalid_energy_kwh": "energy_kwh",
    "invalid_peak_kw": "peak_kw",
    "invalid_duration_min": "duration_min",
    "invalid_power_kw": "power_kw",
    "invalid_latitude": "lat",
    "invalid_longitude": "lon",
    "invalid_install_date": "install_date",
    "invalid_start_time": "start_time",
    "invalid_end_time": "end_time",
    "invalid_event_date": "event_date",
    "invalid_level": "level",
    "invalid_severity": "severity",
}

_ENUM_FIELDS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "charger": ("connector_type", "level", "status"),
    "maintenance": ("event_type", "severity"),
}


class CleaningPipeline:
    """Stateful per-run pipeline; carries LLM call/token counters."""

    def __init__(self, rules: Rules, llm_enabled: bool = False):
        self.rules = rules
        self.standardizer = Standardizer(rules.config)
        self.llm_enabled = llm_enabled
        self.llm_calls = 0
        self.llm_tokens = 0

    # -- public -------------------------------------------------------------

    def process(self, raw: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
        """Clean one record.  Returns ``(output_dict, quarantined)``.

        ``None`` means the record has an unknown ``record_type`` and cannot
        be represented by the P2 models; the caller logs and skips it.
        """
        if str(raw.get("record_type") or "") not in ("charger", "session", "maintenance"):
            return None, False

        record, fixes = self.standardizer.standardize_record(raw)
        self._derive_missing(record, fixes)
        self._fix_duration_contradiction(record, fixes)
        self._llm_repair_categoricals(record, fixes)

        issues = self.rules.evaluate(record)
        self._stash_unrepresentable(record, issues)

        confidence = extraction_confidence_from(record.get("quality"))
        score = compute_score(confidence, issues, self.rules.scoring())
        quarantined = is_quarantined(score, self.rules.scoring())

        prior_fixes = record.get("quality") or {}
        prior_fixes_list = prior_fixes.get("fixes_applied")
        if not isinstance(prior_fixes_list, list):
            prior_fixes_list = []
        quality_out: dict[str, Any] = {
            "score": score,
            "issues": [issue.code for issue in issues],
            "fixes_applied": list(prior_fixes_list) + fixes,
        }
        if confidence is not None:
            quality_out["extraction_confidence"] = round(confidence, 4)
        record["quality"] = quality_out

        try:
            model = build_record(record)
            return model.dump(), quarantined
        except Exception as exc:  # pragma: no cover — standardizers guarantee validity
            # A record the P2 models cannot represent (e.g. a provenance
            # field the rules already flagged as missing) is still emitted —
            # never dropped — with a marker issue and its computed score.
            record["quality"] = quality_out
            record["quality"]["issues"] = record["quality"]["issues"] + ["model_validation_failed"]
            return record, quarantined

    # -- S3: derive missing values conservatively ---------------------------

    def _derive_missing(self, record: dict[str, Any], fixes: list[str]) -> None:
        record_type = str(record.get("record_type") or "")
        if record_type == "session":
            self._derive_duration(record, fixes)
        elif record_type == "charger":
            self._derive_level(record, fixes)

    def _derive_duration(self, record: dict[str, Any], fixes: list[str]) -> None:
        if record.get("duration_min") is not None:
            return
        minutes = self._computed_duration_min(record)
        if minutes is None:
            return
        record["duration_min"] = minutes
        fixes.append(f"derive:duration_min:computed from start_time/end_time ({minutes:.2f} min)")

    def _derive_level(self, record: dict[str, Any], fixes: list[str]) -> None:
        if record.get("level") is not None:
            return
        power = record.get("power_kw")
        if not isinstance(power, (int, float)):
            return
        thresholds = self.rules.config.get("derive", {}).get("level_from_power_kw", {})
        l1_max = float(thresholds.get("L1_max_kw", 1.9))
        l2_max = float(thresholds.get("L2_max_kw", 19.2))
        if power <= l1_max:
            level = "L1"
        elif power <= l2_max:
            level = "L2"
        else:
            level = "DCFC"
        record["level"] = level
        fixes.append(f"derive:level:{level} from power_kw={power}")

    # -- S4: fix internal contradictions ------------------------------------

    def _fix_duration_contradiction(self, record: dict[str, Any], fixes: list[str]) -> None:
        """duration_min ≈ end − start; recompute from timestamps when it
        contradicts them (timestamps are the authoritative record)."""
        duration = record.get("duration_min")
        if not isinstance(duration, (int, float)):
            return
        minutes = self._computed_duration_min(record)
        if minutes is None:
            return
        tolerance = self.rules.duration_tolerance_min()
        if abs(duration - minutes) > tolerance:
            fixes.append(
                f"fix:duration_min:recomputed from timestamps ({duration:g} → {minutes:.2f} min)"
            )
            record["duration_min"] = minutes

    def _computed_duration_min(self, record: dict[str, Any]) -> float | None:
        from .rules import _parse_datetime

        start = _parse_datetime(record.get("start_time"))
        end = _parse_datetime(record.get("end_time"))
        if start is None or end is None or end <= start:
            return None
        return round((end - start).total_seconds() / 60.0, 2)

    # -- S5: LLM repair of unresolved categoricals --------------------------

    def _llm_repair_categoricals(self, record: dict[str, Any], fixes: list[str]) -> None:
        record_type = str(record.get("record_type") or "")
        unresolved: dict[str, str] = {}
        allowed: dict[str, list[str]] = {}
        for field in _ENUM_FIELDS_BY_TYPE.get(record_type, ()):
            value = record.get(field)
            if value is None or str(value) in self.rules.enum_members(field):
                continue
            unresolved[field] = str(value)
            allowed[field] = self.rules.enum_members(field)
        if not unresolved:
            return
        if not self.llm_enabled:
            return  # deterministic path: rules flag invalid_<field>
        repairs, calls, tokens = repair_enums_with_llm(unresolved, allowed)
        self.llm_calls += calls
        self.llm_tokens += tokens
        for field, enum_value in repairs.items():
            old = record.get(field)
            record[field] = enum_value
            fixes.append(f"llm:{field}:{old!r}→{enum_value}")

    # -- model-safe finalization -------------------------------------------

    def _stash_unrepresentable(self, record: dict[str, Any], issues: list[Issue]) -> None:
        """Drop fields the P2 models cannot hold and stash raw_<field>."""
        for issue in issues:
            field = _INVALID_FIELD_BY_CODE.get(issue.code)
            if field is None:
                continue
            value = record.get(field)
            if value is None:
                continue
            record[f"raw_{field}"] = value
            record[field] = None
