"""CLI + §6 protocol tests: run ``python -m src.reporting.run`` as a subprocess.

Covers: success metrics line, --limit cap, --log-json log lines, failure
protocol (missing input / missing optional report), data-unavailable run
without report flags, restated-report mode end-to-end.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent.parent
FIXTURES = ROOT / "src" / "reporting" / "fixtures"
GOLDEN = FIXTURES / "golden.jsonl"
EXPECTED_FILES = [
    "psc_compliance_report.csv",
    "psc_compliance_report.html",
    "dashboard.html",
    "safety_view.html",
    "customer_experience.html",
]


def run_cli(*args, cwd: Path = ROOT):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # tests must be offline/deterministic
    return subprocess.run(
        [sys.executable, "-m", "src.reporting.run", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def last_stderr_line(result) -> str:
    lines = [l for l in result.stderr.splitlines() if l.strip()]
    return lines[-1] if lines else ""


@pytest.fixture()
def out_dir(tmp_path):
    return tmp_path / "reports"


def test_success_metrics_and_outputs(out_dir):
    res = run_cli(
        "--in", str(GOLDEN),
        "--out", str(out_dir),
        "--validation-report", str(FIXTURES / "validation_report.json"),
        "--reconciliation-report", str(FIXTURES / "reconciliation_report.json"),
        "--pipeline-health", str(FIXTURES / "pipeline_health.json"),
        "--no-llm",
        "--log-json",
        "--report-date", "2026-08-06",
    )
    assert res.returncode == 0, res.stderr
    # all five output files exist and are non-empty
    for name in EXPECTED_FILES:
        p = out_dir / name
        assert p.exists(), name
        assert p.stat().st_size > 0, name
    # final stderr line is the metrics JSON
    final = json.loads(last_stderr_line(res))
    assert set(final) == {"metrics"}
    m = final["metrics"]
    assert m["records_in"] == 18
    assert m["records_out"] == 18
    assert m["llm_calls"] == 0
    assert m["llm_tokens"] == 0
    # log lines are JSON with "log": true
    logs = [json.loads(l) for l in res.stderr.splitlines() if l.strip()]
    assert all(l["log"] is True for l in logs[:-1])


def test_limit_caps_records(out_dir):
    res = run_cli("--in", str(GOLDEN), "--out", str(out_dir), "--no-llm", "--limit", "10", "--log-json")
    assert res.returncode == 0, res.stderr
    m = json.loads(last_stderr_line(res))["metrics"]
    assert m["records_in"] == 10
    assert m["records_out"] == 10


def test_missing_input_fails_with_error_protocol(out_dir):
    res = run_cli("--in", str(out_dir / "nope.jsonl"), "--out", str(out_dir), "--no-llm", "--log-json")
    assert res.returncode != 0
    err = json.loads(last_stderr_line(res))
    assert err["error"] is True
    assert err["stage"] == "reporting"
    assert "message" in err


def test_missing_optional_report_fails(out_dir):
    res = run_cli(
        "--in", str(GOLDEN), "--out", str(out_dir),
        "--validation-report", str(out_dir / "missing.json"),
        "--no-llm", "--log-json",
    )
    assert res.returncode != 0
    err = json.loads(last_stderr_line(res))
    assert err["error"] is True
    assert "validation-report" in err["message"]


def test_data_unavailable_without_report_flags(out_dir):
    res = run_cli("--in", str(GOLDEN), "--out", str(out_dir), "--no-llm", "--report-date", "2026-08-06")
    assert res.returncode == 0, res.stderr
    dash = (out_dir / "dashboard.html").read_text(encoding="utf-8")
    assert "league table data unavailable" in dash
    assert "latency tracker data unavailable" in dash
    psc = (out_dir / "psc_compliance_report.html").read_text(encoding="utf-8")
    assert "No lineage data available" in psc


def test_restated_report_end_to_end(out_dir):
    res = run_cli(
        "--in", str(GOLDEN), "--out", str(out_dir),
        "--validation-report", str(FIXTURES / "validation_report.json"),
        "--restates", "2026-07-01",
        "--prior-kpis", str(FIXTURES / "prior_kpis.json"),
        "--no-llm",
        "--report-date", "2026-08-06",
    )
    assert res.returncode == 0, res.stderr
    psc_html = (out_dir / "psc_compliance_report.html").read_text(encoding="utf-8")
    assert "RESTATES report of 2026-07-01" in psc_html
    psc_csv = (out_dir / "psc_compliance_report.csv").read_text(encoding="utf-8")
    assert "RESTATEMENT" in psc_csv
    assert "+20.00" in psc_csv  # energy delta


def test_report_date_rendered(out_dir):
    res = run_cli("--in", str(GOLDEN), "--out", str(out_dir), "--no-llm", "--report-date", "2026-08-06")
    assert res.returncode == 0, res.stderr
    psc = (out_dir / "psc_compliance_report.html").read_text(encoding="utf-8")
    assert "2026-08-06" in psc


def test_plain_log_without_log_json(out_dir):
    res = run_cli("--in", str(GOLDEN), "--out", str(out_dir), "--no-llm")
    assert res.returncode == 0, res.stderr
    assert "[reporting] stage started" in res.stderr  # human-readable log lines
    # without --log-json there are no JSON "log" lines; the metrics line is
    # still emitted (harmless — it is the §6 protocol shape)
    assert '{"log": true' not in res.stderr
    assert '{"metrics"' in res.stderr


def test_unknown_extra_flag_rejected():
    res = run_cli("--in", str(GOLDEN), "--bogus-flag")
    assert res.returncode != 0
