"""A3 — pipeline health monitoring.

Collects each stage's final-stderr-line metrics (or error) per the §6
process protocol into artifacts/pipeline_health.json, whose required shape
is frozen in §5.5. Extra keys (skipped, retries, preflight, per-stage error)
are §5.5-legal additions ("producers may add, never remove").

The file is (re)written after every stage so the reporting stage can receive
a §5.5-valid snapshot of all stages that ran before it via --pipeline-health.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.platform.protocol import utc_now_iso


def parse_final_stderr_line(stderr_text: str) -> tuple[str, dict | None]:
    """Classify the last non-empty stderr line per the §6 protocol.

    Returns ("metrics", {...}) | ("error", {...}) | ("missing", None).
    """
    lines = [ln for ln in stderr_text.splitlines() if ln.strip()]
    if not lines:
        return "missing", None
    try:
        obj = json.loads(lines[-1])
    except json.JSONDecodeError:
        return "missing", None
    if not isinstance(obj, dict):
        return "missing", None
    if isinstance(obj.get("metrics"), dict):
        return "metrics", obj["metrics"]
    if obj.get("error") is True:
        return "error", obj
    return "missing", None


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class HealthWriter:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.run_started = utc_now_iso()
        self._stages: dict[str, dict] = {}
        self._extras: dict = {}

    def set_extra(self, key: str, value) -> None:
        self._extras[key] = value

    def record_stage(self, stage: str, *, exit_code: int, duration_s: float,
                     metrics: dict | None = None, skipped: bool = False,
                     retries: int = 0, error: dict | None = None) -> None:
        metrics = metrics or {}
        entry = {
            "records_in": _int(metrics.get("records_in")),
            "records_out": _int(metrics.get("records_out")),
            "duration_s": round(float(duration_s), 3),
            "llm_calls": _int(metrics.get("llm_calls")),
            "llm_tokens": _int(metrics.get("llm_tokens")),
            "exit_code": exit_code,
            "skipped": skipped,
            "retries": retries,
        }
        if error is not None:
            entry["error"] = error
        self._stages[stage] = entry
        self.write()

    def stage(self, name: str) -> dict | None:
        return self._stages.get(name)

    def summary_metrics(self) -> dict:
        """The platform's own §6 metrics line: ends of the pipe + LLM totals."""
        first = self._stages.get("ingestion", {})
        recon = self._stages.get("reconciliation", {})
        return {
            "records_in": first.get("records_in", 0),
            "records_out": recon.get("records_out", 0),
            "llm_calls": sum(s.get("llm_calls", 0) for s in self._stages.values()),
            "llm_tokens": sum(s.get("llm_tokens", 0) for s in self._stages.values()),
        }

    def write(self) -> None:
        doc = {
            "stages": self._stages,
            "run_started": self.run_started,
            "run_finished": utc_now_iso(),
            **self._extras,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    def console_summary(self) -> str:
        parts = []
        for name, s in self._stages.items():
            state = "SKIP" if s.get("skipped") else ("OK" if s["exit_code"] == 0 else f"FAIL({s['exit_code']})")
            parts.append(
                f"{name}: {state} in={s['records_in']} out={s['records_out']} "
                f"{s['duration_s']}s llm={s['llm_calls']}/{s['llm_tokens']}"
            )
        return " | ".join(parts)
