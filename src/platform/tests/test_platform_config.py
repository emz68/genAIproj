import sys

from src.platform.config import (
    DEFAULT_CONFIG_PATH,
    STAGE_ORDER,
    gitignore_covers,
    load_config,
    preflight,
    stage_argv,
)


def test_default_config_loads_and_resolves_python():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    for stage in STAGE_ORDER:
        real = stage_argv(cfg, stage, use_stubs=False)
        stub = stage_argv(cfg, stage, use_stubs=True)
        assert real[0] == sys.executable and stub[0] == sys.executable
        assert real[1:3] == ["-m", f"src.{stage}.run"]
        assert stub[2].startswith("src.platform.stubs.")


def test_gitignore_covers_this_repo():
    ok, missing = gitignore_covers()
    assert ok, f"frozen .gitignore should cover data/raw/ and artifacts/, missing: {missing}"


def test_gitignore_check_reports_missing(tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text("artifacts/\n", encoding="utf-8")
    ok, missing = gitignore_covers(gitignore_path=gi)
    assert not ok and missing == ["data/raw/"]
    ok, missing = gitignore_covers(gitignore_path=tmp_path / "absent")
    assert not ok and len(missing) == 2


def test_preflight_forces_no_llm_without_key(monkeypatch):
    cfg = load_config(DEFAULT_CONFIG_PATH)
    monkeypatch.delenv(cfg.llm.api_key_env, raising=False)
    report = preflight(cfg, no_llm=False)
    assert report["effective_no_llm"] is True
    assert not report["llm_key_present"]
    assert any("downgrading" in w for w in report["warnings"])


def test_preflight_respects_explicit_no_llm(monkeypatch):
    cfg = load_config(DEFAULT_CONFIG_PATH)
    monkeypatch.delenv(cfg.llm.api_key_env, raising=False)
    report = preflight(cfg, no_llm=True)
    assert report["effective_no_llm"] is True
    assert not any("downgrading" in w for w in report["warnings"])


def test_preflight_honors_llm_disabled_in_config(monkeypatch):
    cfg = load_config(DEFAULT_CONFIG_PATH)
    monkeypatch.setenv(cfg.llm.api_key_env, "key-present")
    cfg.llm.enabled = False
    report = preflight(cfg, no_llm=False)
    assert report["effective_no_llm"] is True, "llm.enabled: false must force --no-llm"
    assert any("disabled by config" in w for w in report["warnings"])


def test_negative_retries_rejected():
    import pytest
    from pydantic import ValidationError

    from src.platform.config import StageConfig

    with pytest.raises(ValidationError):
        StageConfig(entrypoint=["x"], stub=["y"], retries=-1)
