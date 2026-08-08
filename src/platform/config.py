"""A2 — configuration, secrets convention, and preflight checks.

Config is YAML (src/platform/config.yaml by default). Stage entrypoints are
argv templates; the "{python}" placeholder resolves to the current
interpreter. Real §6 module paths are the default; --use-stubs switches to
the stub entrypoints (A1/A6).

The preflight is report-only for frozen files: it warns if .gitignore does
not cover data/raw/ and artifacts/ but never edits it (the file is owned by
main). If the LLM is enabled but the API key env var is absent, the run is
downgraded to --no-llm with a warning rather than failing mid-pipeline.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

STAGE_ORDER = ["ingestion", "validation", "reconciliation", "reporting"]

DEFAULT_CONFIG_PATH = Path("src/platform/config.yaml")

GITIGNORE_REQUIRED = ("data/raw/", "artifacts/")


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")


class StageConfig(_Base):
    entrypoint: list[str]
    stub: list[str]
    retries: int = 1  # additional attempts after the first failure


class PathsConfig(_Base):
    raw_dir: str = "data/raw"
    artifacts_dir: str = "artifacts"


class LLMConfig(_Base):
    enabled: bool = True
    api_key_env: str = "ANTHROPIC_API_KEY"


class PlatformConfig(_Base):
    paths: PathsConfig = PathsConfig()
    llm: LLMConfig = LLMConfig()
    stages: dict[str, StageConfig]


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> PlatformConfig:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    cfg = PlatformConfig.model_validate(data)
    missing = [s for s in STAGE_ORDER if s not in cfg.stages]
    if missing:
        raise ValueError(f"config is missing stage entries: {missing}")
    return cfg


def resolve_argv(template: list[str]) -> list[str]:
    return [sys.executable if part == "{python}" else part for part in template]


def stage_argv(cfg: PlatformConfig, stage: str, use_stubs: bool) -> list[str]:
    sc = cfg.stages[stage]
    return resolve_argv(sc.stub if use_stubs else sc.entrypoint)


def gitignore_covers(required: tuple[str, ...] = GITIGNORE_REQUIRED,
                     gitignore_path: Path | str = ".gitignore") -> tuple[bool, list[str]]:
    """Report-only check that the frozen .gitignore covers the required entries."""
    path = Path(gitignore_path)
    if not path.exists():
        return False, list(required)
    lines = {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()}
    missing = [entry for entry in required if entry not in lines and entry.rstrip("/") not in lines]
    return (not missing), missing


def preflight(cfg: PlatformConfig, no_llm: bool) -> dict:
    """Returns a report dict (attached to pipeline_health.json extras) plus warnings."""
    warnings: list[str] = []
    ok, missing = gitignore_covers()
    if not ok:
        warnings.append(
            f".gitignore does not cover {missing} — report-only, the file is frozen on main; "
            "raise it in docs/proposals/ (§2.1 rule 3)"
        )
    key_present = bool(os.environ.get(cfg.llm.api_key_env, "").strip())
    effective_no_llm = no_llm
    if cfg.llm.enabled and not no_llm and not key_present:
        warnings.append(
            f"LLM enabled but ${cfg.llm.api_key_env} is not set — downgrading run to --no-llm"
        )
        effective_no_llm = True
    return {
        "gitignore_ok": ok,
        "gitignore_missing": missing,
        "llm_key_present": key_present,
        "effective_no_llm": effective_no_llm,
        "warnings": warnings,
    }
