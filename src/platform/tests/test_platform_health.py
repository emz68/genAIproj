import json

from src.platform.health import HealthWriter
from src.platform.schemas import PipelineHealth


def test_health_file_is_5_5_valid_and_tracks_extras(tmp_path):
    path = tmp_path / "pipeline_health.json"
    hw = HealthWriter(path)
    hw.set_extra("preflight", {"gitignore_ok": True})
    hw.record_stage("ingestion", exit_code=0, duration_s=1.234,
                    metrics={"records_in": 4, "records_out": 7, "llm_calls": 2, "llm_tokens": 900})
    hw.record_stage("validation", exit_code=0, duration_s=0.5, metrics=None, skipped=True)
    hw.record_stage("reconciliation", exit_code=3, duration_s=0.1, retries=1,
                    error={"error": True, "stage": "reconciliation", "message": "boom", "detail": {}})
    hw.write()

    doc = json.loads(path.read_text(encoding="utf-8"))
    PipelineHealth.model_validate(doc)  # frozen §5.5 shape
    assert doc["stages"]["ingestion"]["llm_tokens"] == 900
    assert doc["stages"]["validation"]["skipped"] is True
    assert doc["stages"]["reconciliation"]["exit_code"] == 3
    assert doc["stages"]["reconciliation"]["retries"] == 1
    assert doc["preflight"]["gitignore_ok"] is True
    assert doc["run_started"] and doc["run_finished"]


def test_summary_metrics_aggregates_ends_of_pipe(tmp_path):
    hw = HealthWriter(tmp_path / "h.json")
    hw.record_stage("ingestion", exit_code=0, duration_s=1,
                    metrics={"records_in": 10, "records_out": 20, "llm_calls": 1, "llm_tokens": 100})
    hw.record_stage("reconciliation", exit_code=0, duration_s=1,
                    metrics={"records_in": 20, "records_out": 18, "llm_calls": 0, "llm_tokens": 0})
    m = hw.summary_metrics()
    assert m == {"records_in": 10, "records_out": 18, "llm_calls": 1, "llm_tokens": 100}


def test_missing_metrics_recorded_as_zeros(tmp_path):
    hw = HealthWriter(tmp_path / "h.json")
    hw.record_stage("ingestion", exit_code=0, duration_s=0.2, metrics=None)
    stage = hw.stage("ingestion")
    assert stage["records_in"] == 0 and stage["llm_tokens"] == 0
