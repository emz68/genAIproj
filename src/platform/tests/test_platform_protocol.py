import json

import pytest

from src.platform.health import parse_final_stderr_line
from src.platform.protocol import iter_jsonl, open_maybe_gzip


def test_final_line_metrics_after_interleaved_logs():
    stderr = "\n".join([
        json.dumps({"log": True, "message": "reading file"}),
        "a plain warning line",
        json.dumps({"metrics": {"records_in": 3, "records_out": 5, "llm_calls": 0, "llm_tokens": 0}}),
        "",
    ])
    kind, payload = parse_final_stderr_line(stderr)
    assert kind == "metrics"
    assert payload["records_out"] == 5


def test_final_line_error_object():
    stderr = "some log\n" + json.dumps({"error": True, "stage": "validation", "message": "boom", "detail": {}})
    kind, payload = parse_final_stderr_line(stderr)
    assert kind == "error"
    assert payload["stage"] == "validation"


@pytest.mark.parametrize("stderr", ["", "plain text only", '{"log": true, "message": "not final"}'])
def test_final_line_missing(stderr):
    kind, _ = parse_final_stderr_line(stderr)
    assert kind == "missing"


def test_iter_jsonl_reports_line_number(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        list(iter_jsonl(path))


def test_iter_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": 1}\n\n{"b": 2}\n', encoding="utf-8")
    assert [obj for _, obj in iter_jsonl(path)] == [{"a": 1}, {"b": 2}]


def test_open_maybe_gzip_roundtrip(tmp_path):
    import gzip

    path = tmp_path / "x.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write('{"a": 1}\n')
    assert [obj for _, obj in iter_jsonl(path)] == [{"a": 1}]
    with open_maybe_gzip(path) as fh:
        assert fh.read().strip() == '{"a": 1}'
