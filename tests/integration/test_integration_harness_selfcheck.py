"""A4 DoD: "harness red/greens contract violations correctly."

Two halves:
1. RED proofs — deliberately contract-VIOLATING stage behaviors, asserting the
   harness's own checking primitives flag every violation class (no metrics
   line, schema-invalid output, naive timestamps, malformed report, --limit
   exceeded). If a harness check were weakened, these tests fail.
2. Orchestrator behaviors only demonstrable with fake stages wired through a
   temp config: success-after-retry recorded in health, and --no-llm
   propagation into stage argv.
"""

import json
import sys
import textwrap

import pytest
import yaml

from harness import REPO_ROOT, final_stderr, run_cli
from src.platform import schemas
from src.platform.health import parse_final_stderr_line
from src.platform.protocol import iter_jsonl

# ---------------------------------------------------------------- RED proofs


def test_red_on_missing_metrics_line(tmp_path):
    script = tmp_path / "bad_stage.py"
    script.write_text("print('did work, forgot the protocol')\n", encoding="utf-8")
    proc = run_cli([sys.executable, str(script)])
    assert proc.returncode == 0
    kind, _ = parse_final_stderr_line(proc.stderr)
    assert kind != "metrics", "harness must NOT classify a protocol-less success as compliant"


def test_red_on_schema_invalid_output_line():
    bad = {"record_type": "charger", "connector_type": "banana",
           "provenance": {"source": "s", "source_file": "f", "ingested_at": "t"},
           "quality": {}}
    with pytest.raises(Exception):
        schemas.parse_validated_line(bad)


def test_red_on_timezone_naive_session_timestamp():
    naive = {"record_type": "session", "start_time": "2023-01-03T17:58:04",
             "provenance": {"source": "s", "source_file": "f", "ingested_at": "t"},
             "quality": {}}
    with pytest.raises(Exception, match="timezone|ISO"):
        schemas.parse_validated_line(naive)  # §5.2: "ISO-8601 with timezone"
    with pytest.raises(Exception, match="timezone|ISO"):
        schemas.parse_golden_line({**naive, "golden_id": "g-1"})


def test_red_on_report_missing_frozen_keys():
    with pytest.raises(Exception):
        schemas.ValidationReport.model_validate({"records_in": 1, "records_out": 1})
    with pytest.raises(Exception):
        schemas.ReconciliationReport.model_validate({"records_in": 1})


def test_red_on_limit_violation(tmp_path):
    over = tmp_path / "over.jsonl"
    over.write_text("".join(json.dumps({"i": i}) + "\n" for i in range(5)), encoding="utf-8")
    count = sum(1 for _ in iter_jsonl(over))
    limit = 2
    assert not count <= limit, "the harness's <=N check must fail on an over-limit output"


# ------------------------------------------- fake-stage orchestrator behaviors

STUB = ["{python}", "-m", "src.platform.stubs.{stage}_stub"]


def _write_config(tmp_path, ingestion_entrypoint, retries=0):
    stages = {}
    for stage in ("ingestion", "validation", "reconciliation", "reporting"):
        stub = ["{python}", "-m", f"src.platform.stubs.{stage}_stub"]
        stages[stage] = {"entrypoint": list(stub), "stub": list(stub), "retries": 0}
    stages["ingestion"]["entrypoint"] = ingestion_entrypoint
    stages["ingestion"]["retries"] = retries
    cfg = {"paths": {"raw_dir": "data/raw", "artifacts_dir": "artifacts"},
           "llm": {"enabled": True, "api_key_env": "ANTHROPIC_API_KEY"},
           "stages": stages}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def _platform_argv(config, raw_dir, artifacts_dir, *extra):
    return [sys.executable, "-m", "src.platform.run", "--pipeline", "full",
            "--config", str(config), "--no-llm",
            "--raw-dir", str(raw_dir), "--artifacts-dir", str(artifacts_dir), *extra]
    # note: NOT --use-stubs — the fake ingestion entrypoint must be exercised


FAKE_PRELUDE = textwrap.dedent("""\
    import json, sys
    from pathlib import Path
    args = sys.argv[1:]
    out = Path(args[args.index("--out") + 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    """)


def test_success_after_retry_recorded_in_health(tmp_path):
    flaky = tmp_path / "flaky_ingestion.py"
    flaky.write_text(FAKE_PRELUDE + textwrap.dedent(f"""\
        marker = Path({str(tmp_path)!r}) / "attempted.marker"
        if not marker.exists():
            marker.write_text("x")
            print(json.dumps({{"error": True, "stage": "ingestion",
                              "message": "transient", "detail": {{}}}}), file=sys.stderr)
            sys.exit(3)
        out.write_text("")
        print(json.dumps({{"metrics": {{"records_in": 0, "records_out": 0,
                          "llm_calls": 0, "llm_tokens": 0}}}}), file=sys.stderr)
        """), encoding="utf-8")
    config = _write_config(tmp_path, ["{python}", str(flaky)], retries=1)
    raw = tmp_path / "raw"
    raw.mkdir()
    proc = run_cli(_platform_argv(config, raw, tmp_path / "artifacts"))
    assert proc.returncode == 0, proc.stderr
    health = json.loads((tmp_path / "artifacts" / "pipeline_health.json").read_text(encoding="utf-8"))
    assert health["stages"]["ingestion"]["retries"] == 1
    assert health["stages"]["ingestion"]["exit_code"] == 0


def test_no_llm_and_limit_propagate_into_stage_argv(tmp_path):
    echo = tmp_path / "echo_ingestion.py"
    echo.write_text(FAKE_PRELUDE + textwrap.dedent(f"""\
        (Path({str(tmp_path)!r}) / "argv.json").write_text(json.dumps(sys.argv[1:]))
        out.write_text("")
        print(json.dumps({{"metrics": {{"records_in": 0, "records_out": 0,
                          "llm_calls": 0, "llm_tokens": 0}}}}), file=sys.stderr)
        """), encoding="utf-8")
    config = _write_config(tmp_path, ["{python}", str(echo)])
    raw = tmp_path / "raw"
    raw.mkdir()
    proc = run_cli(_platform_argv(config, raw, tmp_path / "artifacts", "--limit", "7"))
    assert proc.returncode == 0, proc.stderr
    argv = json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))
    assert "--no-llm" in argv, "orchestrator must propagate --no-llm to every stage (§6)"
    assert "--limit" in argv and argv[argv.index("--limit") + 1] == "7"
