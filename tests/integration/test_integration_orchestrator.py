"""A1/A3 end-to-end: the orchestrator over stubs, resumability, late-arriving
data invalidation, and the platform's own §6 protocol compliance."""

import json
import shutil
import sys

import pytest

from harness import FIXTURES, REPO_ROOT, final_stderr, run_cli
from src.platform.schemas import PipelineHealth


def _platform_argv(raw_dir, artifacts_dir, *extra):
    return [
        sys.executable, "-m", "src.platform.run",
        "--pipeline", "full",
        "--config", str(REPO_ROOT / "src" / "platform" / "config.yaml"),
        "--use-stubs", "--no-llm",
        "--raw-dir", str(raw_dir),
        "--artifacts-dir", str(artifacts_dir),
        *extra,
    ]


@pytest.fixture
def workdir(tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw", raw)
    return raw, tmp_path / "artifacts"


def _health(artifacts_dir) -> dict:
    doc = json.loads((artifacts_dir / "pipeline_health.json").read_text(encoding="utf-8"))
    PipelineHealth.model_validate(doc)  # §5.5 shape, every time we look at it
    return doc


def test_full_pipeline_then_skip_then_late_data_invalidation(workdir):
    raw, artifacts = workdir

    # Run 1: everything runs, all artifacts appear.
    proc = run_cli(_platform_argv(raw, artifacts))
    assert proc.returncode == 0, proc.stderr
    kind, metrics = final_stderr(proc)
    assert kind == "metrics" and metrics["records_in"] > 0
    for name in ("canonical.jsonl", "validated.jsonl", "validation_report.json",
                 "golden.jsonl", "reconciliation_report.json", "pipeline_health.json",
                 "manifest.json"):
        assert (artifacts / name).exists(), f"missing {name}"
    reports = artifacts / "reports"
    assert reports.is_dir() and any(reports.iterdir())
    health = _health(artifacts)
    assert set(health["stages"]) == {"ingestion", "validation", "reconciliation", "reporting"}
    assert all(not s["skipped"] for s in health["stages"].values())
    assert all(s["exit_code"] == 0 for s in health["stages"].values())

    # Run 2: nothing changed → every stage skips via the manifest.
    proc = run_cli(_platform_argv(raw, artifacts))
    assert proc.returncode == 0, proc.stderr
    health = _health(artifacts)
    assert all(s["skipped"] for s in health["stages"].values()), health["stages"]
    kind, metrics = final_stderr(proc)
    assert kind == "metrics" and metrics["records_in"] > 0  # skipped stages keep their metrics

    # Run 3: a late-arriving raw file invalidates ingestion and the chain re-runs.
    (raw / "late_arriving_report.txt").write_text(
        "LATE FILE: charger by the library, breaker trip x3, sev=MAJOR", encoding="utf-8")
    proc = run_cli(_platform_argv(raw, artifacts))
    assert proc.returncode == 0, proc.stderr
    health = _health(artifacts)
    assert not health["stages"]["ingestion"]["skipped"], "late raw data must re-run ingestion"
    assert not health["stages"]["validation"]["skipped"], "downstream must re-run on new upstream output"
    assert health["stages"]["ingestion"]["records_in"] == 5  # 4 fixture files + 1 late


def test_no_resume_forces_full_rerun(workdir):
    raw, artifacts = workdir
    assert run_cli(_platform_argv(raw, artifacts)).returncode == 0
    proc = run_cli(_platform_argv(raw, artifacts, "--no-resume"))
    assert proc.returncode == 0, proc.stderr
    health = _health(artifacts)
    assert all(not s["skipped"] for s in health["stages"].values())


def test_failure_surfaces_stage_error_and_health(tmp_path):
    proc = run_cli(_platform_argv(tmp_path / "missing-raw", tmp_path / "artifacts"))
    assert proc.returncode != 0
    kind, error = final_stderr(proc)
    assert kind == "error"
    assert error["stage"] == "platform"
    assert error["detail"]["failed_stage"] == "ingestion"
    assert error["detail"]["stage_error"]["stage"] == "ingestion"
    health = json.loads((tmp_path / "artifacts" / "pipeline_health.json").read_text(encoding="utf-8"))
    assert health["stages"]["ingestion"]["exit_code"] != 0
    assert "validation" not in health["stages"], "pipeline must stop at the failed stage"


def test_limit_propagates_to_stages(workdir):
    raw, artifacts = workdir
    proc = run_cli(_platform_argv(raw, artifacts, "--limit", "3"))
    assert proc.returncode == 0, proc.stderr
    count = sum(1 for ln in (artifacts / "canonical.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
    assert count <= 3


def test_all_three_report_inputs_reach_reporting_stage(workdir):
    """A1 requires wiring ALL of --validation-report, --reconciliation-report,
    and --pipeline-health into the reporting stage; if any were dropped, its
    lineage section would render 'data unavailable'."""
    raw, artifacts = workdir
    proc = run_cli(_platform_argv(raw, artifacts))
    assert proc.returncode == 0, proc.stderr
    psc = (artifacts / "reports" / "psc_report.html").read_text(encoding="utf-8").lower()
    assert "data unavailable" not in psc, \
        "a report input was not wired through to the reporting stage"
    assert "validation" in psc and "reconciliation" in psc and "pipeline health" in psc


def test_platform_log_json_protocol(workdir):
    """The platform CLI is itself a §6 stage: under --log-json every non-final
    stderr line must be a JSON object with log:true, final line = metrics."""
    raw, artifacts = workdir
    proc = run_cli(_platform_argv(raw, artifacts, "--log-json"))
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    assert lines
    for ln in lines[:-1]:
        obj = json.loads(ln)
        assert obj.get("log") is True, f"non-final stderr line lacks log:true — {ln[:80]}"
    assert "metrics" in json.loads(lines[-1])
