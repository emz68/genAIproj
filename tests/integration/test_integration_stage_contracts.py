"""A4 — per-stage §6 contract assertions, run against whatever entrypoint is
present (stubs pre-merge, real modules post-merge).

Per §7 A4 the ingestion assertions cover flag/exit/protocol behavior and
schema-validity of whatever it emits — never accept/reject verdicts on
specific raw files (input formats are Emlyn's design space).
"""

import json

import pytest

from harness import (
    ALL_STAGES,
    FIXTURES,
    JSONL_STAGES,
    STAGE_FIXTURE_INPUT,
    auto_stage_argv,
    final_stderr,
    real_module_present,
    run_cli,
    stage_io_args,
)
from src.platform import schemas
from src.platform.protocol import iter_jsonl

PARSERS = {
    "ingestion": schemas.parse_canonical_line,
    "validation": schemas.parse_validated_line,
    "reconciliation": schemas.parse_golden_line,
}
REPORT_MODELS = {
    "validation": schemas.ValidationReport,
    "reconciliation": schemas.ReconciliationReport,
}
METRIC_KEYS = {"records_in", "records_out", "llm_calls", "llm_tokens"}


def _run_stage(stage, tmp_path, extra=(), in_path=None):
    io_args, outputs = stage_io_args(stage, tmp_path, in_path or STAGE_FIXTURE_INPUT[stage])
    proc = run_cli(auto_stage_argv(stage) + io_args + ["--no-llm", *extra])
    return proc, outputs


@pytest.mark.parametrize("stage", ALL_STAGES)
def test_success_exit_code_and_metrics_line(stage, tmp_path):
    proc, _ = _run_stage(stage, tmp_path)
    assert proc.returncode == 0, proc.stderr
    kind, metrics = final_stderr(proc)
    assert kind == "metrics", f"final stderr line must be metrics, got: {proc.stderr.splitlines()[-1:]}"
    assert METRIC_KEYS <= set(metrics)
    assert all(isinstance(metrics[k], int) for k in METRIC_KEYS)
    # §6: "(zeros when --no-llm)" — and every harness run passes --no-llm
    assert metrics["llm_calls"] == 0 and metrics["llm_tokens"] == 0


@pytest.mark.parametrize("stage", JSONL_STAGES)
def test_output_lines_are_schema_valid(stage, tmp_path):
    proc, outputs = _run_stage(stage, tmp_path)
    assert proc.returncode == 0, proc.stderr
    lines = list(iter_jsonl(outputs[0]))
    # §7 A4: no accept/reject verdicts on raw formats — the non-emptiness
    # assertion applies to the stub only, never to Emlyn's real ingestion.
    if stage != "ingestion" or not real_module_present("ingestion"):
        assert lines, "stage emitted no records from a non-empty fixture"
    for lineno, obj in lines:
        PARSERS[stage](obj)  # raises on contract violation
    if stage in REPORT_MODELS:
        report = json.loads(outputs[1].read_text(encoding="utf-8"))
        REPORT_MODELS[stage].model_validate(report)


def test_reporting_writes_expected_nonempty_file_set(tmp_path):
    proc, outputs = _run_stage("reporting", tmp_path)
    assert proc.returncode == 0, proc.stderr
    reports_dir = outputs[0]
    produced = {p.name for p in reports_dir.iterdir() if p.is_file()}
    assert produced, "reporting produced no files"
    assert any(p.suffix == ".csv" for p in reports_dir.iterdir()), "no CSV report"
    assert any(p.suffix == ".html" for p in reports_dir.iterdir()), "no HTML report"
    for p in reports_dir.iterdir():
        assert p.stat().st_size > 0, f"{p.name} is empty"


def test_reporting_renders_with_and_without_optional_reports(tmp_path):
    with_reports = tmp_path / "with"
    io_args, _ = stage_io_args("reporting", with_reports, STAGE_FIXTURE_INPUT["reporting"])
    proc = run_cli(auto_stage_argv("reporting") + io_args + [
        "--no-llm",
        "--validation-report", str(FIXTURES / "validation_report.json"),
        "--reconciliation-report", str(FIXTURES / "reconciliation_report.json"),
    ])
    assert proc.returncode == 0, proc.stderr
    without = tmp_path / "without"
    proc2, _ = _run_stage("reporting", without)
    assert proc2.returncode == 0, proc2.stderr


@pytest.mark.parametrize("stage", JSONL_STAGES)
def test_limit_caps_total_output_records(stage, tmp_path):
    proc, outputs = _run_stage(stage, tmp_path, extra=("--limit", "2"))
    assert proc.returncode == 0, proc.stderr
    count = sum(1 for _ in iter_jsonl(outputs[0]))
    assert count <= 2  # §6: assert <= N, never == N


@pytest.mark.parametrize("stage", ALL_STAGES)
def test_log_json_lines_are_structured(stage, tmp_path):
    proc, _ = _run_stage(stage, tmp_path, extra=("--log-json",))
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    assert lines
    for ln in lines[:-1]:
        obj = json.loads(ln)  # every non-final line must be JSON…
        assert obj.get("log") is True, f"non-final stderr line lacks log:true — {ln[:80]}"
    assert "metrics" in json.loads(lines[-1])


@pytest.mark.parametrize("stage", JSONL_STAGES[1:] + ("reporting",))
def test_broken_jsonl_input_fails_with_error_object(stage, tmp_path):
    proc, _ = _run_stage(stage, tmp_path, in_path=FIXTURES / "broken" / "not_json.jsonl")
    assert proc.returncode != 0
    kind, error = final_stderr(proc)
    assert kind == "error", f"final stderr line must be a §6 error object, got: {proc.stderr[-200:]}"
    assert error["error"] is True and error["stage"] and error["message"]


def test_ingestion_missing_input_dir_fails_with_error_object(tmp_path):
    proc, _ = _run_stage("ingestion", tmp_path, in_path=tmp_path / "does-not-exist")
    assert proc.returncode != 0
    kind, error = final_stderr(proc)
    assert kind == "error"
    assert error["stage"] == "ingestion"


def test_unexpected_crash_still_emits_error_object(tmp_path):
    """§6 requires the error object on EVERY failure path — here, --out
    pointing at an existing regular file (mkdir will raise)."""
    blocker = tmp_path / "reports"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    io_args, _ = stage_io_args("reporting", tmp_path, STAGE_FIXTURE_INPUT["reporting"])
    proc = run_cli(auto_stage_argv("reporting") + io_args + ["--no-llm"])
    assert proc.returncode != 0
    kind, error = final_stderr(proc)
    assert kind == "error", f"crash must end in a §6 error object, got: {proc.stderr[-300:]}"
    assert error["stage"] == "reporting"


def test_validation_quarantines_schema_invalid_records(tmp_path):
    proc, outputs = _run_stage("validation", tmp_path, in_path=FIXTURES / "broken" / "bad_schema.jsonl")
    assert proc.returncode == 0, proc.stderr  # graceful: quarantine, don't crash
    report = json.loads(outputs[1].read_text(encoding="utf-8"))
    schemas.ValidationReport.model_validate(report)
    assert report["quarantined"] >= 1
    assert report["records_in"] == 3
