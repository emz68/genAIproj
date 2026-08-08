"""A6 stub for the reporting stage (§6): golden JSONL (+ optional §5.5 report
files) → artifacts/reports/ directory.

Writes the expected file set, non-empty, with real counts from the golden
stream; the three optional report inputs render into the lineage section, or
"data unavailable" when absent (§6). --limit is accepted and ignored: this
stage emits files, not records. Real report design is Emily's P4.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.platform import protocol

STAGE = "reporting"

EXPECTED_FILES = ("psc_report.csv", "psc_report.html", "dashboard.html",
                  "safety_health.html", "customer_experience.html")


def _load_optional(path: str | None) -> dict | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _html(title: str, body: str) -> str:
    return f"<!doctype html><html><head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"python -m src.platform.stubs.{STAGE}_stub")
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--out", dest="out_dir", required=True)
    parser.add_argument("--validation-report", default=None)
    parser.add_argument("--reconciliation-report", default=None)
    parser.add_argument("--pipeline-health", default=None)
    protocol.add_common_flags(parser)
    args = parser.parse_args(argv)

    in_path = Path(args.in_path)
    if not in_path.is_file():
        protocol.fail(STAGE, f"input file not found: {in_path}", {"path": str(in_path)})

    counts = {"charger": 0, "session": 0, "maintenance": 0}
    energy = 0.0
    try:
        for _lineno, obj in protocol.iter_jsonl(in_path):
            rt = obj.get("record_type")
            if rt in counts:
                counts[rt] += 1
            if rt == "session" and isinstance(obj.get("energy_kwh"), (int, float)):
                energy += obj["energy_kwh"]
    except ValueError as e:
        protocol.fail(STAGE, str(e), {"input": str(in_path)})
    records_in = sum(counts.values())

    vrep = _load_optional(args.validation_report)
    rrep = _load_optional(args.reconciliation_report)
    health = _load_optional(args.pipeline_health)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    kpis = [
        ("chargers", counts["charger"]),
        ("sessions", counts["session"]),
        ("maintenance_events", counts["maintenance"]),
        ("energy_kwh_total", round(energy, 3)),
    ]
    (out_dir / "psc_report.csv").write_text(
        "kpi,value\n" + "\n".join(f"{k},{v}" for k, v in kpis) + "\n", encoding="utf-8")

    def lineage(rep: dict | None, name: str, keys: tuple[str, ...]) -> str:
        if rep is None:
            return f"<p>{name}: data unavailable</p>"
        stats = ", ".join(f"{k}={rep.get(k)}" for k in keys if k in rep)
        return f"<p>{name}: {stats}</p>"

    lineage_html = (
        lineage(vrep, "validation", ("records_in", "records_out", "quarantined"))
        + lineage(rrep, "reconciliation", ("records_in", "golden_records_out", "duplicates_removed"))
        + lineage(health, "pipeline health", ("run_started",))
    )
    kpi_html = "".join(f"<li>{k}: {v}</li>" for k, v in kpis)
    (out_dir / "psc_report.html").write_text(
        _html("PSC Compliance Report (stub)", f"<ul>{kpi_html}</ul><h2>Data lineage</h2>{lineage_html}"),
        encoding="utf-8")
    (out_dir / "dashboard.html").write_text(
        _html("Operational Awareness (stub)", f"<ul>{kpi_html}</ul>"), encoding="utf-8")
    (out_dir / "safety_health.html").write_text(
        _html("Safety & Infrastructure Health (stub)", "<p>No SAFETY anomalies in stub data.</p>"),
        encoding="utf-8")
    (out_dir / "customer_experience.html").write_text(
        _html("Customer Experience (stub)", f"<p>{counts['charger']} charger(s) tracked.</p>"),
        encoding="utf-8")

    protocol.emit_metrics(protocol.base_metrics(records_in=records_in, records_out=len(EXPECTED_FILES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
