"""A1 — the pipeline orchestrator.

Executes the §6 stage CLIs as subprocesses in order, wiring each stage's
outputs to the next stage's inputs (including the reporting stage's three
optional --*-report flags), propagating --no-llm/--limit/--log-json,
retrying per config, and skipping unchanged stages via the resumability
manifest (invalidated whenever data/raw/ contents change — see manifest.py).

Must be run from the repository root (the frozen §6 commands are `python -m
src.<stage>.run`, which resolve against the CWD).
"""

from __future__ import annotations

import subprocess
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from src.platform import protocol
from src.platform.config import STAGE_ORDER, PlatformConfig, preflight, stage_argv
from src.platform.health import HealthWriter, parse_final_stderr_line
from src.platform.manifest import Manifest, digest_params, digest_paths


@dataclass
class RunOptions:
    use_stubs: bool = False
    no_llm: bool = False
    limit: int | None = None
    log_json: bool = False
    resume: bool = True
    raw_dir: str | None = None        # overrides config.paths.raw_dir
    artifacts_dir: str | None = None  # overrides config.paths.artifacts_dir


@dataclass
class StagePlan:
    name: str
    argv: list[str]
    inputs: list[Path]
    outputs: list[Path]


def build_plan(cfg: PlatformConfig, opts: RunOptions) -> tuple[list[StagePlan], Path, Path]:
    raw = Path(opts.raw_dir or cfg.paths.raw_dir)
    art = Path(opts.artifacts_dir or cfg.paths.artifacts_dir)
    canonical = art / "canonical.jsonl"
    validated = art / "validated.jsonl"
    vreport = art / "validation_report.json"
    golden = art / "golden.jsonl"
    rreport = art / "reconciliation_report.json"
    reports_dir = art / "reports"
    health_path = art / "pipeline_health.json"
    manifest_path = art / "manifest.json"

    common: list[str] = []
    if opts.no_llm:
        common.append("--no-llm")
    if opts.limit is not None:
        common += ["--limit", str(opts.limit)]
    if opts.log_json:
        common.append("--log-json")

    def argv(stage: str, *io_args: str) -> list[str]:
        return stage_argv(cfg, stage, opts.use_stubs) + list(io_args) + common

    plan = [
        StagePlan("ingestion",
                  argv("ingestion", "--in", str(raw), "--out", str(canonical)),
                  inputs=[raw], outputs=[canonical]),
        StagePlan("validation",
                  argv("validation", "--in", str(canonical), "--out", str(validated),
                       "--report", str(vreport)),
                  inputs=[canonical], outputs=[validated, vreport]),
        StagePlan("reconciliation",
                  argv("reconciliation", "--in", str(validated), "--out", str(golden),
                       "--report", str(rreport)),
                  inputs=[validated], outputs=[golden, rreport]),
        StagePlan("reporting",
                  argv("reporting", "--in", str(golden), "--out", str(reports_dir),
                       "--validation-report", str(vreport),
                       "--reconciliation-report", str(rreport),
                       "--pipeline-health", str(health_path)),
                  # health_path changes every run (timestamps) — deliberately
                  # excluded from the digest so it can't defeat resumability.
                  inputs=[golden, vreport, rreport], outputs=[reports_dir]),
    ]
    return plan, health_path, manifest_path


def _run_once(argv: list[str]) -> tuple[int, str]:
    """Run one stage attempt, streaming stderr with a bounded tail (§2.3:
    memory must stay flat however much the stage logs)."""
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True)
    tail: deque[str] = deque(maxlen=200)
    assert proc.stderr is not None
    for line in proc.stderr:
        if line.strip():
            tail.append(line.rstrip("\n"))
    return proc.wait(), "\n".join(tail)


def _run_stage(plan: StagePlan, retries: int, log) -> tuple[int, str, int]:
    """Returns (exit_code, stderr_tail, retries_used)."""
    attempts = max(1, retries + 1)
    exit_code, stderr_tail = 1, ""
    for attempt in range(1, attempts + 1):
        exit_code, stderr_tail = _run_once(plan.argv)
        if exit_code == 0:
            return 0, stderr_tail, attempt - 1
        log(f"stage {plan.name} attempt {attempt}/{attempts} failed (exit {exit_code})",
            stage=plan.name, attempt=attempt, exit_code=exit_code)
    return exit_code, stderr_tail, attempts - 1


def run_pipeline(cfg: PlatformConfig, opts: RunOptions) -> int:
    def log(message: str, **fields):
        protocol.log(message, log_json=opts.log_json, **fields)

    pf = preflight(cfg, opts.no_llm)
    for warning in pf["warnings"]:
        log(f"preflight: {warning}")
    opts.no_llm = pf["effective_no_llm"]

    plan, health_path, manifest_path = build_plan(cfg, opts)
    health = HealthWriter(health_path)
    health.set_extra("preflight", {k: v for k, v in pf.items() if k != "warnings"})
    health.set_extra("use_stubs", opts.use_stubs)
    manifest = Manifest(manifest_path)

    for stage in plan:
        assert stage.name in STAGE_ORDER
        inputs_digest = digest_paths(stage.inputs)
        params_digest = digest_params(stage.argv)

        if opts.resume and manifest.should_skip(stage.name, inputs_digest, params_digest, stage.outputs):
            log(f"stage {stage.name}: unchanged, skipping (manifest)", stage=stage.name)
            health.record_stage(stage.name, exit_code=0, duration_s=0.0,
                                metrics=manifest.last_metrics(stage.name), skipped=True)
            continue

        log(f"stage {stage.name}: running {' '.join(stage.argv)}", stage=stage.name)
        started = time.monotonic()
        exit_code, stderr_text, retries_used = _run_stage(stage, cfg.stages[stage.name].retries, log)
        duration = time.monotonic() - started
        kind, payload = parse_final_stderr_line(stderr_text)

        if exit_code != 0:
            error_obj = payload if kind == "error" else {
                "error": True, "stage": stage.name,
                "message": "stage exited nonzero without a §6 error object",
                "detail": {"stderr_tail": stderr_text.splitlines()[-5:]},
            }
            health.record_stage(stage.name, exit_code=exit_code, duration_s=duration,
                                retries=retries_used, error=error_obj)
            log(f"pipeline failed at {stage.name}: {error_obj.get('message')}", stage=stage.name)
            protocol.fail("platform", f"stage {stage.name} failed after {retries_used + 1} attempt(s)",
                          detail={"failed_stage": stage.name, "stage_error": error_obj},
                          exit_code=exit_code)

        if kind != "metrics":
            log(f"stage {stage.name}: WARNING — succeeded without a §6 metrics line; recording zeros",
                stage=stage.name)
            payload = None
        manifest.record(stage.name, inputs_digest, params_digest, stage.outputs, payload)
        manifest.save()
        health.record_stage(stage.name, exit_code=0, duration_s=duration,
                            metrics=payload, retries=retries_used)
        log(f"stage {stage.name}: done in {duration:.2f}s", stage=stage.name)

    health.write()
    log("pipeline complete: " + health.console_summary())
    protocol.emit_metrics(health.summary_metrics())
    return 0
