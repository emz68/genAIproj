"""A4 harness helpers.

The harness runs whatever entrypoint config.yaml names for each stage:
the real §6 modules once they are merged to the working tree, the A6 stubs
before that (auto-detected via find_spec). All subprocesses run from the
repo root, matching the frozen `python -m src.<stage>.run` commands.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "src" / "platform" / "fixtures"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.platform.config import load_config, stage_argv  # noqa: E402
from src.platform.health import parse_final_stderr_line  # noqa: E402

JSONL_STAGES = ("ingestion", "validation", "reconciliation")
ALL_STAGES = JSONL_STAGES + ("reporting",)


def real_module_present(stage: str) -> bool:
    return importlib.util.find_spec(f"src.{stage}.run") is not None


def auto_stage_argv(stage: str) -> list[str]:
    cfg = load_config(REPO_ROOT / "src" / "platform" / "config.yaml")
    return stage_argv(cfg, stage, use_stubs=not real_module_present(stage))


def run_cli(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, cwd=REPO_ROOT)


def final_stderr(proc: subprocess.CompletedProcess):
    return parse_final_stderr_line(proc.stderr)


def stage_io_args(stage: str, tmp_path: Path, in_path: Path) -> tuple[list[str], list[Path]]:
    """(io argv, output paths) for one stage run into tmp_path."""
    if stage == "ingestion":
        out = tmp_path / "canonical.jsonl"
        return ["--in", str(in_path), "--out", str(out)], [out]
    if stage == "validation":
        out, rep = tmp_path / "validated.jsonl", tmp_path / "validation_report.json"
        return ["--in", str(in_path), "--out", str(out), "--report", str(rep)], [out, rep]
    if stage == "reconciliation":
        out, rep = tmp_path / "golden.jsonl", tmp_path / "reconciliation_report.json"
        return ["--in", str(in_path), "--out", str(out), "--report", str(rep)], [out, rep]
    if stage == "reporting":
        out = tmp_path / "reports"
        return ["--in", str(in_path), "--out", str(out)], [out]
    raise ValueError(stage)


STAGE_FIXTURE_INPUT = {
    "ingestion": FIXTURES / "raw",
    "validation": FIXTURES / "canonical.jsonl",
    "reconciliation": FIXTURES / "validated.jsonl",
    "reporting": FIXTURES / "golden.jsonl",
}
