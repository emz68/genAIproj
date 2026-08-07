"""Stage CLI tests (E4/E6): end-to-end runs, §6 flags + stderr protocol."""

import json
import os
import shutil
import subprocess
import sys

from src.ingestion.models import ChargerRecord, MaintenanceEvent, SessionRecord

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def _run_cli(args, env_extra=None, cwd=REPO_ROOT):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-m", "src.ingestion.run", *args],
        capture_output=True, text=True, cwd=cwd, env=env,
    )
    return proc


def _stage_dir(tmp_path, files=None):
    d = tmp_path / "raw"
    d.mkdir()
    for name in files or [
        "source_a_sample.csv",
        "source_b_sample.geojson",
        "report_complaint.txt",
        "report_digest.txt",
        "report_email.txt",
        "report_inspection.txt",
    ]:
        shutil.copy(os.path.join(FIXTURES, name) if os.path.exists(os.path.join(FIXTURES, name))
                    else os.path.join(FIXTURES, "contractor", name), d)
    return d


def _records(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TestEndToEnd:
    def test_full_run_over_all_fixtures(self, tmp_path):
        raw = _stage_dir(tmp_path)
        out = tmp_path / "canonical.jsonl"
        proc = _run_cli(["--in", str(raw), "--out", str(out), "--no-llm"])
        assert proc.returncode == 0, proc.stderr
        recs = _records(out)
        kinds = {}
        for r in recs:
            kinds[r["record_type"]] = kinds.get(r["record_type"], 0) + 1
        assert kinds == {"session": 3, "charger": 4, "maintenance": 5}
        # every record parses against its P1 model (raw-string shapes)
        for r in recs:
            model = {"session": SessionRecord, "charger": ChargerRecord, "maintenance": MaintenanceEvent}[r["record_type"]]
            parsed = model.model_validate(r)
            assert parsed.provenance.source in ("cary", "afdc", "contractor")
            assert parsed.provenance.source_file
            assert parsed.quality.score is not None

    def test_session_golden_matches_expected(self, tmp_path):
        """Golden comparison against the hand-computed expected JSONL."""
        raw = _stage_dir(tmp_path, files=["source_a_sample.csv"])
        out = tmp_path / "canonical.jsonl"
        proc = _run_cli(["--in", str(raw), "--out", str(out), "--no-llm"])
        assert proc.returncode == 0
        got = _records(out)
        with open(os.path.join(FIXTURES, "expected", "source_a_sample.expected.jsonl")) as fh:
            want = [json.loads(line) for line in fh if line.strip()]
        # normalize the dynamic timestamp
        for r in got:
            r["provenance"]["ingested_at"] = "TBD"
        assert got == want

    def test_files_processed_in_sorted_order(self, tmp_path):
        raw = _stage_dir(tmp_path, files=["report_digest.txt", "source_a_sample.csv"])
        out = tmp_path / "canonical.jsonl"
        proc = _run_cli(["--in", str(raw), "--out", str(out), "--no-llm"])
        assert proc.returncode == 0
        recs = _records(out)
        # sorted: report_digest.txt < source_a_sample.csv -> maintenance first
        assert recs[0]["record_type"] == "maintenance"
        assert recs[1]["record_type"] == "maintenance"  # digest holds 2 events
        assert recs[2]["record_type"] == "session"

    def test_limit_stops_after_n_records(self, tmp_path):
        raw = _stage_dir(tmp_path)
        out = tmp_path / "canonical.jsonl"
        proc = _run_cli(["--in", str(raw), "--out", str(out), "--no-llm", "--limit", "5"])
        assert proc.returncode == 0
        recs = _records(out)
        assert len(recs) == 5  # ≤ N, harness asserts <=, never ==... but here exactly 5 emitted
        # sorted order: the four contractor files sort before source_*
        # -> complaint(1) + digest(2) + email(1) + inspection(1) = 5 maintenance
        assert [r["record_type"] for r in recs] == ["maintenance"] * 5


class TestStderrProtocol:
    def test_metrics_line_on_success(self, tmp_path):
        raw = _stage_dir(tmp_path, files=["source_a_sample.csv"])
        out = tmp_path / "canonical.jsonl"
        proc = _run_cli(["--in", str(raw), "--out", str(out), "--no-llm", "--log-json"])
        assert proc.returncode == 0
        lines = [l for l in proc.stderr.splitlines() if l.strip()]
        last = json.loads(lines[-1])
        assert set(last.keys()) == {"metrics"}
        m = last["metrics"]
        assert m["records_in"] == 3
        assert m["records_out"] == 3
        assert m["llm_calls"] == 0
        assert m["llm_tokens"] == 0
        # intermediate lines are JSON log objects
        for line in lines[:-1]:
            obj = json.loads(line)
            assert obj["log"] is True

    def test_error_line_on_unknown_format(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "blob.dat").write_text("not a known format")
        out = tmp_path / "canonical.jsonl"
        proc = _run_cli(["--in", str(raw), "--out", str(out), "--no-llm", "--log-json"])
        assert proc.returncode == 1
        lines = [l for l in proc.stderr.splitlines() if l.strip()]
        last = json.loads(lines[-1])
        assert last["error"] is True
        assert last["stage"] == "ingestion"
        assert "message" in last

    def test_missing_api_key_fails_cleanly_without_no_llm(self, tmp_path):
        raw = _stage_dir(tmp_path, files=["report_email.txt"])
        out = tmp_path / "canonical.jsonl"
        proc = _run_cli(["--in", str(raw), "--out", str(out), "--log-json"])
        assert proc.returncode == 1
        last = json.loads([l for l in proc.stderr.splitlines() if l.strip()][-1])
        assert last["error"] is True
        assert "ANTHROPIC_API_KEY" in last["message"]

    def test_empty_input_dir_is_fatal(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        out = tmp_path / "canonical.jsonl"
        proc = _run_cli(["--in", str(raw), "--out", str(out), "--no-llm", "--log-json"])
        assert proc.returncode == 1
        last = json.loads([l for l in proc.stderr.splitlines() if l.strip()][-1])
        assert last["error"] is True


class TestMaintenanceAssembly:
    def test_verbatim_and_confidence(self, tmp_path):
        raw = _stage_dir(tmp_path, files=["report_email.txt"])
        out = tmp_path / "canonical.jsonl"
        proc = _run_cli(["--in", str(raw), "--out", str(out), "--no-llm"])
        assert proc.returncode == 0
        rec = _records(out)[0]
        assert rec["record_type"] == "maintenance"
        assert rec["event_date"] == "01.02.2023"
        assert rec["station_name"] == "TOWN OF CARY / DT DECK P2 (2)"
        assert rec["quality"]["score"] >= 0.8
        assert rec["provenance"]["source"] == "contractor"
        assert rec["provenance"]["raw_ref"] == "report_email.txt"
