from src.platform.manifest import Manifest, digest_params, digest_path, digest_paths


def _setup(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.txt").write_text("alpha", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    out.write_text('{"x": 1}\n', encoding="utf-8")
    return raw, out


def test_digest_changes_on_content_and_new_file(tmp_path):
    raw, _ = _setup(tmp_path)
    d1 = digest_path(raw)
    (raw / "a.txt").write_text("alpha CHANGED", encoding="utf-8")
    d2 = digest_path(raw)
    assert d1 != d2
    (raw / "b.txt").write_text("late-arriving file", encoding="utf-8")
    assert digest_path(raw) != d2


def test_digest_missing_path_is_stable(tmp_path):
    missing = tmp_path / "nope"
    assert digest_path(missing) == digest_path(missing)
    assert digest_path(missing) != digest_path(tmp_path)


def test_skip_then_invalidate_on_raw_change(tmp_path):
    """The A1/DoD scenario: unchanged → skip; late-arriving raw file → re-run."""
    raw, out = _setup(tmp_path)
    manifest = Manifest(tmp_path / "manifest.json")
    params = digest_params(["cmd", "--flag"])
    manifest.record("ingestion", digest_paths([raw]), params, [out], {"records_in": 1})
    manifest.save()

    reloaded = Manifest(tmp_path / "manifest.json")
    assert reloaded.should_skip("ingestion", digest_paths([raw]), params, [out])
    assert reloaded.last_metrics("ingestion") == {"records_in": 1}

    (raw / "late.csv").write_text("new data", encoding="utf-8")
    assert not reloaded.should_skip("ingestion", digest_paths([raw]), params, [out])


def test_no_skip_when_params_change_or_output_tampered(tmp_path):
    raw, out = _setup(tmp_path)
    manifest = Manifest(tmp_path / "manifest.json")
    inputs = digest_paths([raw])
    manifest.record("ingestion", inputs, digest_params(["cmd"]), [out], None)

    assert not manifest.should_skip("ingestion", inputs, digest_params(["cmd", "--limit", "5"]), [out])

    out.write_text("tampered", encoding="utf-8")
    assert not manifest.should_skip("ingestion", inputs, digest_params(["cmd"]), [out])

    out.unlink()
    assert not manifest.should_skip("ingestion", inputs, digest_params(["cmd"]), [out])


def test_corrupt_manifest_means_full_rerun(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    manifest = Manifest(path)
    assert not manifest.should_skip("ingestion", "d", "p", [])
