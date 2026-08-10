"""CLI contract tests (PDM §6) — src/validation/run.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.validation.models import build_record

_ROOT = Path(__file__).parent.parent.parent.parent
_FIXTURES = _ROOT / "src" / "validation" / "fixtures"
_INPUT = _FIXTURES / "canonical_messy.jsonl"


def _clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def _run_cli(*args: str, tmp_path: Path):
    out = tmp_path / "validated.jsonl"
    report = tmp_path / "validation_report.json"
    result = subprocess.run(
        [sys.executable, "-m", "src.validation.run", "--in", str(_INPUT),
         "--out", str(out), "--report", str(report), *args],
        capture_output=True, text=True, env=_clean_env(), cwd=_ROOT,
    )
    return result, out, report


def test_happy_path_contract(tmp_path):
    result, out, report = _run_cli("--no-llm", tmp_path=tmp_path)
    assert result.returncode == 0
    lines = out.read_text().splitlines()
    assert len(lines) == 24                      # every record emitted, none dropped
    # Output parses against the P2 models except the 2 flagged degenerates.
    rejected = 0
    for line in lines:
        try:
            build_record(json.loads(line))
        except Exception:
            rejected += 1
    assert rejected == 2
    # Report matches §5.5 exactly.
    data = json.loads(report.read_text())
    assert set(data) == {"records_in", "records_out", "quarantined", "per_source", "per_issue"}
    assert data["records_in"] == 24
    assert data["records_out"] == 24
    assert data["quarantined"] == 1
    assert data["per_issue"]["impossible_energy_kwh"] == 2
    assert data["per_issue"]["stale_report"] == 3
    assert set(data["per_source"]) == {"afdc", "cary", "contractor_reports", "unknown"}
    assert data["per_source"]["cary"]["records"] == 13
    assert 0.0 <= data["per_source"]["cary"]["avg_score"] <= 1.0


def test_limit_stops_early(tmp_path):
    result, out, report = _run_cli("--no-llm", "--limit", "5", tmp_path=tmp_path)
    assert result.returncode == 0
    assert len(out.read_text().splitlines()) == 5
    assert json.loads(report.read_text())["records_out"] == 5
    assert json.loads(report.read_text())["records_in"] == 5


def test_log_json_protocol(tmp_path):
    result, _, _ = _run_cli("--no-llm", "--log-json", tmp_path=tmp_path)
    stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
    final = json.loads(stderr_lines[-1])
    assert set(final) == {"metrics"}
    assert final["metrics"]["llm_calls"] == 0     # --no-llm ⇒ zeros
    assert final["metrics"]["llm_tokens"] == 0
    assert final["metrics"]["records_in"] == 24
    assert final["metrics"]["records_out"] == 24
    for line in stderr_lines[:-1]:                # every other line is a log object
        obj = json.loads(line)
        assert obj.get("log") is True


def test_missing_input_fails_with_error_protocol(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "src.validation.run", "--in", str(tmp_path / "nope.jsonl"),
         "--out", str(tmp_path / "o.jsonl"), "--report", str(tmp_path / "r.json")],
        capture_output=True, text=True, env=_clean_env(), cwd=_ROOT,
    )
    assert result.returncode != 0
    final = json.loads(result.stderr.strip().splitlines()[-1])
    assert final["error"] is True
    assert final["stage"] == "validation"
    assert "not found" in final["message"]


def test_rules_override_flag(tmp_path):
    result, out, _ = _run_cli("--no-llm", "--rules", str(_FIXTURES / "rules_override.yaml"),
                              tmp_path=tmp_path)
    assert result.returncode == 0
    report_data = json.loads((tmp_path / "validation_report.json").read_text())
    # With the NYC bbox, the 5 NC chargers leave the territory and the 1 NYC
    # charger enters it: exactly 5 outside_territory issues.
    assert report_data["per_issue"]["outside_territory"] == 5


def test_duplicates_emitted_identically(tmp_path):
    result, out, _ = _run_cli("--no-llm", tmp_path=tmp_path)
    assert result.returncode == 0
    lines = out.read_text().splitlines()
    assert lines[4] == lines[5]                   # the planted duplicate pair
    assert "duplicate" not in out.read_text()     # no duplicate flag anywhere


def test_stdout_is_quiet_with_log_json(tmp_path):
    result, _, _ = _run_cli("--no-llm", "--log-json", tmp_path=tmp_path)
    assert result.stdout.strip() == ""


def test_output_matches_expected_fixture(tmp_path):
    """Full-pipeline regression: output must equal the hand-verified
    expected fixture line-for-line (JSON equality, order-insensitive)."""
    result, out, _ = _run_cli("--no-llm", tmp_path=tmp_path)
    assert result.returncode == 0
    actual = [json.loads(line) for line in out.read_text().splitlines()]
    expected = [
        json.loads(line)
        for line in (_FIXTURES / "expected_validated.jsonl").read_text().splitlines()
    ]
    assert actual == expected
