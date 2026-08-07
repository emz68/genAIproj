"""Unit tests for quality scoring (S6) — src/validation/."""

from __future__ import annotations

from src.validation.rules import Issue, Rules
from src.validation.scoring import (
    compute_score,
    extraction_confidence_from,
    is_quarantined,
)

_SCORING = Rules.load().scoring()


def _issue(code: str, severity: str = "MINOR") -> Issue:
    return Issue(code=code, severity=severity, message=code)


def test_clean_record_keeps_extraction_confidence():
    score = compute_score(0.9, [], _SCORING)
    assert score == 0.9


def test_no_confidence_defaults_to_one():
    assert compute_score(None, [], _SCORING) == 1.0


def test_major_issue_penalty():
    score = compute_score(1.0, [_issue("impossible_energy_kwh", "MAJOR")], _SCORING)
    assert score == 0.75


def test_minor_issue_penalty():
    score = compute_score(0.9, [_issue("stale_report", "MINOR")], _SCORING)
    assert score == 0.8


def test_safety_issue_penalty():
    score = compute_score(1.0, [_issue("safety_review", "SAFETY")], _SCORING)
    assert score == 0.5


def test_penalty_per_unique_code_not_per_instance():
    issues = [
        Issue(code="coordinate_out_of_range", severity="MAJOR", message="lat", field="lat"),
        Issue(code="coordinate_out_of_range", severity="MAJOR", message="lon", field="lon"),
    ]
    assert compute_score(1.0, issues, _SCORING) == 0.75


def test_unknown_severity_uses_default_weight():
    score = compute_score(1.0, [_issue("mystery", "PURPLE")], _SCORING)
    assert score == 0.9  # default_weight 0.1


def test_score_clamped_at_zero():
    issues = [_issue(f"i{n}", "SAFETY") for n in range(5)]
    assert compute_score(0.3, issues, _SCORING) == 0.0


def test_score_rounded_to_config_digits():
    score = compute_score(0.7, [_issue("minor1", "MINOR"), _issue("minor2", "MINOR")], _SCORING)
    assert score == 0.5


def test_quarantine_threshold():
    assert is_quarantined(0.39, _SCORING)
    assert not is_quarantined(0.4, _SCORING)
    assert not is_quarantined(0.9, _SCORING)


def test_extraction_confidence_from_p1_score():
    assert extraction_confidence_from({"score": 0.85}) == 0.85
    assert extraction_confidence_from({"score": "0.85"}) is None  # non-numeric
    assert extraction_confidence_from({}) is None
    assert extraction_confidence_from(None) is None
