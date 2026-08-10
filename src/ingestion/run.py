"""P1 ingestion stage CLI — frozen contract (PRODUCT_DEVELOPMENT_MANAGER.md §6).

Usage:
    python -m src.ingestion.run --in data/raw/ --out artifacts/canonical.jsonl [--no-llm] [--limit N] [--log-json]

Output:
    stdout  -> JSONL canonical records (one per line, §5.1-5.3 P1 shapes)
    stderr  -> log lines; with ``--log-json`` every log line is a JSON object
               with ``"log": true``. On success the final stderr line is
               ``{"metrics": {"records_in": n, "records_out": n,
               "llm_calls": n, "llm_tokens": n}}``; on failure the final line
               is ``{"error": true, "stage": "ingestion", "message": "...",
               "detail": {}}`` and the exit code is nonzero.

Design notes (P1 semantics, §5.0 / §7):
- Source values are passed through **verbatim** as free strings. Nothing is
  normalized here — that is P2's job.
- ``quality.score`` = parsing/extraction confidence at ingestion time.
- Files are processed in sorted-name order; ``--limit N`` stops after N
  emitted records total.
- Streaming: one record is written per line; nothing is held in memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from src.ingestion import extract, readers
from src.ingestion.models import ChargerRecord, MaintenanceEvent, SessionRecord

STAGE = "ingestion"

SOURCES = {
    "csv": "cary",
    "geojson": "afdc",
    "text": "contractor",
}

# Extra Source B properties worth keeping on charger records (verbatim).
_B_EXTRAS = (
    "station_name",
    "ev_connector_types",
    "date_last_confirmed",
    "access_days_time",
    "ev_pricing",
    "ev_network",
    "status_code",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_id(source_file: str, line_no: int) -> str:
    key = f"{os.path.basename(source_file)}:{line_no}"
    return "cary-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _quality(score: float, issues: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "score": score,
        "extraction_confidence": None,
        "issues": list(issues or []),
        "fixes_applied": [],
    }


def assemble_csv_row(
    row: Dict[str, Any],
    issues: List[str],
    source_file: str,
    ingested_at: str,
    line_no: int,
) -> Dict[str, Any]:
    """Source A row -> §5.2 SessionRecord dict (values verbatim)."""
    def g(col: str) -> Optional[str]:
        v = row.get(col)
        return v if v not in (None, "") else None

    score = 0.7 if issues else 1.0
    rec: Dict[str, Any] = {
        "record_type": "session",
        "session_id": _session_id(source_file, line_no),
        "start_time": g("start_date"),
        "energy_kwh": g("energy_kwh"),
        "duration_min": g("charging_time_hh_mm_ss"),
        # Extras — verbatim source fidelity; P3 reconciles Source A to B on
        # these (station name + address), P2 normalizes values.
        "station_name": g("station_name"),
        "address_1": g("address_1"),
        "address_2": g("address_2"),
        "city": g("city"),
        "state_province": g("state_province"),
        "zip_postal_code": g("zip_postal_code"),
        "provenance": {
            "source": SOURCES["csv"],
            "source_file": os.path.basename(source_file),
            "ingested_at": ingested_at,
            "raw_ref": f"line:{line_no}",
        },
        "quality": _quality(score, issues),
    }
    return {k: v for k, v in rec.items() if v is not None}


def _s(v: Any) -> Optional[str]:
    """Verbatim pass-through as a free string; None stays None (§5.0).

    ijson parses JSON numbers as Decimal; str(Decimal("1287100800000")) is
    "1287100800000" — verbatim. This keeps every source value a string in
    P1 output without ever normalizing it.
    """
    return None if v is None else str(v)


def _parse_ev_charging_units(raw: Any) -> List[Dict[str, Any]]:
    """ev_charging_units arrives as a JSON string (or already a list)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [u for u in raw if isinstance(u, dict)]


def assemble_geojson_feature(
    feat: Dict[str, Any], source_file: str, ingested_at: str
) -> List[Dict[str, Any]]:
    """Source B feature -> one §5.1 ChargerRecord per physical port.

    Per-port expansion uses the ``ev_charging_units`` structure (one dict per
    EVSE, per-connector ``port_count``/``power_kw``); stations without that
    structure yield a single record with the raw ``ev_connector_types`` list.
    """
    props = feat.get("properties") or {}
    geom = feat.get("geometry") or {}
    coords = geom.get("coordinates") if geom.get("type") == "Point" else None
    station_id = str(props["id"]) if props.get("id") is not None else None
    raw_ref = f"feature:{station_id}" if station_id else None

    extras = {k: _s(props[k]) for k in _B_EXTRAS if props.get(k) is not None}

    def base_charger(network: Optional[str], level: Optional[str]) -> Dict[str, Any]:
        return {
            "record_type": "charger",
            "station_id": _s(station_id),
            "network": _s(network),
            "address": {
                "street": _s(props.get("street_address")),
                "city": _s(props.get("city")),
                "state": _s(props.get("state")),
                "zip": _s(props.get("zip")),
            },
            "lat": str(coords[1]) if coords and len(coords) > 1 else None,
            "lon": str(coords[0]) if coords else None,
            "level": _s(level),
            "status": _s(props.get("status_code")),
            "install_date": _s(props.get("open_date")),
            "provenance": {
                "source": SOURCES["geojson"],
                "source_file": os.path.basename(source_file),
                "ingested_at": ingested_at,
                "raw_ref": raw_ref,
            },
            "quality": _quality(1.0),
        }

    units = _parse_ev_charging_units(props.get("ev_charging_units"))
    records: List[Dict[str, Any]] = []
    if units:
        for ui, unit in enumerate(units):
            unit_network = unit.get("network") or props.get("ev_network")
            level = unit.get("charging_level")
            connectors = unit.get("connectors") or {}
            for conn_name, conn in connectors.items():
                port_count = int(conn.get("port_count") or 0) if isinstance(conn, dict) else 0
                if port_count <= 0:
                    continue
                power = conn.get("power_kw")
                for p in range(port_count):
                    rec = base_charger(unit_network, level)
                    rec["connector_type"] = _s(conn_name)
                    rec["power_kw"] = _s(power)
                    rec["charger_id"] = f"{station_id}#{ui}#{conn_name}#{p}" if station_id else None
                    rec.update(extras)
                    records.append({k: v for k, v in rec.items() if v is not None})
    else:
        rec = base_charger(props.get("ev_network"), None)
        rec["connector_type"] = _s(props.get("ev_connector_types"))
        rec.update(extras)
        records.append({k: v for k, v in rec.items() if v is not None})

    # keep `address` present but prune null street/city entries inside it
    for rec in records:
        if rec.get("address"):
            rec["address"] = {k: v for k, v in rec["address"].items() if v is not None}
            if not rec["address"]:
                del rec["address"]
    return records


def assemble_maintenance_events(
    text: str, source_file: str, ingested_at: str, use_llm: bool, stats: Dict[str, int]
) -> List[Dict[str, Any]]:
    events, fallback = extract.parse_maintenance(text, source_file, use_llm, stats=stats)
    records = []
    for ev in events:
        issues = [fallback] if fallback else []
        rec: Dict[str, Any] = {
            "record_type": "maintenance",
            "event_id": ev["event_id"],
            "event_date": ev.get("event_date"),
            "event_type": ev.get("event_type"),
            "severity": ev.get("severity"),
            "description": ev["description"],
            "extracted_fields": ev.get("extracted_fields") or {},
            "provenance": {
                "source": SOURCES["text"],
                "source_file": os.path.basename(source_file),
                "ingested_at": ingested_at,
                "raw_ref": os.path.basename(source_file),
            },
            "quality": _quality(ev.get("confidence", 0.5), issues),
        }
        if ev.get("station_name"):
            rec["station_name"] = ev["station_name"]  # extra, verbatim
        records.append({k: v for k, v in rec.items() if v is not None})
    return records


def iter_input_files(in_dir: str) -> Iterator[str]:
    if os.path.isfile(in_dir):
        yield in_dir
        return
    for name in sorted(os.listdir(in_dir)):
        if name.startswith("."):
            continue
        path = os.path.join(in_dir, name)
        if os.path.isfile(path):
            yield path


def run(
    in_dir: str,
    out_path: str,
    no_llm: bool = True,
    limit: Optional[int] = None,
    log_json: bool = False,
    client: Any = None,
) -> Dict[str, int]:
    """Execute the stage; returns metrics. Raises on fatal errors."""
    stats: Dict[str, int] = {"llm_calls": 0, "llm_tokens": 0}
    ingested_at = now_iso()

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)  # every stage mkdir -p's its output

    def log(msg: str) -> None:
        if log_json:
            print(json.dumps({"log": True, "ts": ingested_at, "message": msg}), file=sys.stderr)
        else:
            print(f"[ingestion] {msg}", file=sys.stderr)

    records_in = 0
    records_out = 0

    files = list(iter_input_files(in_dir))
    if not files:
        raise FileNotFoundError(f"no input files found under {in_dir!r}")

    with open(out_path, "w", encoding="utf-8") as out_fh:
        for path in files:
            if limit is not None and records_out >= limit:
                break
            fmt = readers.detect_format(path)
            log(f"processing {os.path.basename(path)} as {fmt}")
            if fmt == "csv":
                for row, issues in readers.iter_csv_records(path):
                    records_in += 1
                    if limit is not None and records_out >= limit:
                        break
                    rec = assemble_csv_row(row, issues, path, ingested_at, records_in)
                    out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    records_out += 1
            elif fmt == "geojson":
                for feat in readers.iter_geojson_features(path):
                    records_in += 1
                    if limit is not None and records_out >= limit:
                        break
                    for rec in assemble_geojson_feature(feat, path, ingested_at):
                        if limit is not None and records_out >= limit:
                            break
                        out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        records_out += 1
            else:  # text
                with readers.open_maybe_gz(path) as fh:
                    text = fh.read()
                records_in += 1
                if limit is not None and records_out >= limit:
                    break
                for rec in assemble_maintenance_events(text, path, ingested_at, not no_llm, stats):
                    if limit is not None and records_out >= limit:
                        break
                    out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    records_out += 1

    metrics = {
        "records_in": records_in,
        "records_out": records_out,
        "llm_calls": stats["llm_calls"],
        "llm_tokens": stats["llm_tokens"],
    }
    if not log_json:
        print(f"[ingestion] done: {records_in} in, {records_out} out", file=sys.stderr)
    # §6: the metrics object is the final stderr line unconditionally —
    # only intermediate log lines are gated by --log-json.
    print(json.dumps({"metrics": metrics}), file=sys.stderr)
    return metrics


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="src.ingestion.run", description=__doc__)
    parser.add_argument("--in", dest="in_dir", default="data/raw", help="input directory (default: data/raw)")
    parser.add_argument("--out", dest="out_path", default="artifacts/canonical.jsonl", help="output JSONL path")
    parser.add_argument("--no-llm", action="store_true", help="use the deterministic regex fallback (no API calls)")
    parser.add_argument("--limit", type=int, default=None, help="emit at most N output records total")
    parser.add_argument("--log-json", action="store_true", help="emit JSON log lines on stderr (§6 protocol)")
    args = parser.parse_args(argv)

    try:
        client = None
        if not args.no_llm:
            try:
                import anthropic  # lazy import: not needed for --no-llm runs
            except ImportError as exc:
                raise RuntimeError(
                    "anthropic SDK not installed; install "
                    "src/ingestion/requirements.txt or pass --no-llm"
                ) from exc
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set; pass --no-llm for the "
                    "deterministic path"
                )
            client = anthropic.Anthropic(api_key=key)

        run(
            args.in_dir,
            args.out_path,
            no_llm=args.no_llm,
            limit=args.limit,
            log_json=args.log_json,
            client=client,
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - §6 error protocol boundary
        detail: Dict[str, Any] = {"type": type(exc).__name__}
        if not args.log_json:
            print(f"[ingestion] ERROR: {exc}", file=sys.stderr)
        # §6: the error object is the final stderr line unconditionally.
        print(
            json.dumps(
                {"error": True, "stage": STAGE, "message": str(exc), "detail": detail}
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
