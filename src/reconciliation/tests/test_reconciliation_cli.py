"""CLI contract tests (§6): flags, exit codes, stderr protocol.

The stage is executed as a real subprocess (``python -m
src.reconciliation.run``) against the committed fixtures — this is the
integration surface Anastasia's harness will use.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
IN = FIXTURES / "validated.jsonl"


def _run_cli(*args, expect=0):
    proc = subprocess.run(
        [sys.executable, "-m", "src.reconciliation.run", *args],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[3],
    )
    assert proc.returncode == expect, (
        f"exit {proc.returncode} (expected {expect})\nstderr: {proc.stderr[-800:]}"
    )
    return proc


def test_full_run_success_and_metrics_line(tmp_path):
    out = tmp_path / "golden.jsonl"
    rep = tmp_path / "report.json"
    proc = _run_cli("--in", str(IN), "--out", str(out), "--report", str(rep), "--no-llm")
    assert out.exists() and out.stat().st_size > 0
    assert rep.exists()
    last = proc.stderr.strip().splitlines()[-1]
    metrics = json.loads(last)["metrics"]
    assert metrics == {"records_in": 33, "records_out": 29, "llm_calls": 0, "llm_tokens": 0}
    report = json.loads(rep.read_text())
    assert report["golden_records_out"] == 29


def test_log_json_protocol(tmp_path):
    out = tmp_path / "golden.jsonl"
    rep = tmp_path / "report.json"
    proc = _run_cli("--in", str(IN), "--out", str(out), "--report", str(rep), "--log-json")
    lines = [json.loads(l) for l in proc.stderr.strip().splitlines()]
    assert all(l.get("log") is True for l in lines[:-1])  # all but last are log lines
    assert lines[-1]["metrics"]["records_in"] == 33  # final line is metrics
    assert any(l.get("event") == "started" for l in lines)


def test_limit_flag(tmp_path):
    out = tmp_path / "golden.jsonl"
    rep = tmp_path / "report.json"
    proc = _run_cli("--in", str(IN), "--out", str(out), "--report", str(rep), "--limit", "10")
    n = sum(1 for _ in out.open())
    assert n <= 10  # harness asserts ≤ N, never == N
    last = json.loads(proc.stderr.strip().splitlines()[-1])["metrics"]
    assert last["records_out"] == n
    assert last["records_out"] <= 10


def test_limit_large_emits_all(tmp_path):
    out = tmp_path / "golden.jsonl"
    rep = tmp_path / "report.json"
    _run_cli("--in", str(IN), "--out", str(out), "--report", str(rep), "--limit", "10000")
    n = sum(1 for _ in out.open())
    assert n == 29


def test_error_protocol_missing_input(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "src.reconciliation.run",
         "--in", str(tmp_path / "nope.jsonl"),
         "--out", str(tmp_path / "x.jsonl"), "--report", str(tmp_path / "r.json")],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[3],
    )
    assert proc.returncode != 0
    last = proc.stderr.strip().splitlines()[-1]
    err = json.loads(last)
    assert err["error"] is True
    assert err["stage"] == "reconciliation"
    assert "not found" in err["message"]


def test_error_protocol_bad_json(tmp_path):
    bad = tmp_path / "bad.jsonl"
    # Line 1 must be a fully valid record so the parser reaches line 2.
    bad.write_text(
        '{"record_type": "charger", "provenance": {"source": "x"}, "quality": {}}\nnot json\n'
    )
    proc = subprocess.run(
        [sys.executable, "-m", "src.reconciliation.run",
         "--in", str(bad), "--out", str(tmp_path / "x.jsonl"),
         "--report", str(tmp_path / "r.json")],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[3],
    )
    assert proc.returncode != 0
    err = json.loads(proc.stderr.strip().splitlines()[-1])
    assert err["error"] is True
    assert "invalid JSON" in err["message"]


def test_mkdir_p_for_output_dirs(tmp_path):
    out = tmp_path / "deep" / "nested" / "golden.jsonl"
    rep = tmp_path / "deep" / "nested" / "report.json"
    _run_cli("--in", str(IN), "--out", str(out), "--report", str(rep))
    assert out.exists()
