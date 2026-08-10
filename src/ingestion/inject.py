"""Messiness injector (E5) — deterministic messy variants at scale.

Regenerates and extends the synthetic contractor reports (Source C) and
degrades copies of Sources A/B (missing fields, unit swaps, date-format
chaos, duplicated rows, conflicting values, late-arriving files).

Usage:
    python -m src.ingestion.inject --in data/seed --out artifacts/injected --seed 42 --scale 1

- ``--seed N``  : RNG seed — same seed + same inputs => byte-identical output.
- ``--scale N`` : multiplies synthetic Source C volume. At ``--scale 2500``
                  the digest megafiles alone yield ~100k maintenance events
                  (the §8 scale run). ``--scale 1`` reproduces the seed-sized
                  report set (~35 events) plus degraded A/B.
- ``--no-degrade`` : Source C only (skip A/B degradation) — handy for
                  isolating synthetic-volume work.
- ``--late``    : emit a late-arriving batch file for Source A
                  (``*_late.csv``) — default on; orchestrator manifest
                  invalidation (§7 A1) treats it as new raw input.

Output goes to ``--out`` (default ``artifacts/injected/``, gitignored); the
Integration Manager swaps its contents into ``data/raw/`` for the scale run
(see data/README.md).
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.ingestion import readers

DEFAULT_OUT = "artifacts/injected"

# --------------------------------------------------------------------------
# Degradation of Source A (CSV) — column indexes of the seed layout
# --------------------------------------------------------------------------

_A_HEADER = [
    "start_date",
    "station_name",
    "charging_time_hh_mm_ss",
    "energy_kwh",
    "address_1",
    "address_2",
    "city",
    "state_province",
    "zip_postal_code",
]

_MESSY_DATE_FORMATS = [
    "{m}/{d}/{yy} {h}:{mm}{ampm}",       # 3/4/24 2pm
    "{d}.{m}.{yyyy}",                    # 01.02.2023
    "{M} {d}, {yyyy}",                   # Jan 3, 2023
    "{m}/{d}/{yyyy} {h}:{mm}:{ss}",      # 1/3/2023 5:58:09
]


def _parse_iso_date(value: str) -> Optional[List[int]]:
    """Parse the seed's ISO-8601 timestamps (e.g. 2023-01-03T17:58:04+00:00)."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", value)
    if not m:
        return None
    return [int(g) for g in m.groups()]


def _messy_date(parts: List[int], rng: random.Random) -> str:
    y, mo, d, h, mi, s = parts
    ampm = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    fmt = rng.choice(_MESSY_DATE_FORMATS)
    return fmt.format(
        m=mo, d=d, yyyy=y, yy=str(y)[2:], M="Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[mo - 1],
        h=h12, mm=mi, ss=s, ampm=ampm,
    )


def degrade_source_a(seed_path: str, out_dir: str, rng: random.Random, late: bool) -> Dict[str, int]:
    """Rewrite Source A as a messy CSV (+ optional late batch). Returns row counts."""
    header = _A_HEADER
    rows: List[List[str]] = []
    late_rows: List[List[str]] = []
    with readers.open_maybe_gz(seed_path) as fh:
        first = fh.readline().rstrip("\r\n")
        # strip BOM if present; trust our known header otherwise
        if first.lstrip("\ufeff") != ";".join(header):
            raise ValueError(f"unexpected Source A header: {first[:80]!r}")
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            cells = line.split(";")
            cells = cells + [""] * (len(header) - len(cells))
            cells = cells[: len(header)]
            rows.append(cells)

    messy_rows: List[List[str]] = []
    for cells in rows:
        out = list(cells)
        # missing fields
        if rng.random() < 0.06:
            idx = rng.randrange(2, len(header))  # never blank station_name/start
            out[idx] = ""
        # date-format chaos
        if rng.random() < 0.35:
            parts = _parse_iso_date(cells[0])
            if parts:
                out[0] = _messy_date(parts, rng)
        # unit swap: kWh -> Wh (x1000)
        if rng.random() < 0.05 and cells[3]:
            try:
                out[3] = str(int(round(float(cells[3]) * 1000)))
            except ValueError:
                pass
        # duplicated rows (exact) — Yash's dedup must see them
        if rng.random() < 0.02:
            messy_rows.append(list(out))
        # conflicting values: same station/time, different energy (duplicate billing)
        if rng.random() < 0.02 and cells[3]:
            out[3] = str(round(float(cells[3]) * 1.12, 3))
            messy_rows.append(list(out))
        messy_rows.append(out)

    # late-arriving batch: a random subset goes to a separate file. Guarantee
    # at least one row so the mechanism always exercises (negligible at scale).
    if late and messy_rows:
        late_pick = [r for r in messy_rows if rng.random() < 0.04]
        if not late_pick:
            late_pick = [messy_rows[0]]
        late_rows = late_pick

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "ev_charging_sessions_cary_messy.csv.gz")
    # gzip embeds an mtime by default — pin it so identical seeds produce
    # byte-identical archives (determinism guarantee, E5).
    body = ";".join(header) + "\r\n"
    body += "".join(";".join(r) + "\r\n" for r in messy_rows)
    with open(out_path, "wb") as fh:
        fh.write(gzip.compress(body.encode("utf-8-sig"), mtime=0))
    if late_rows:
        late_path = os.path.join(out_dir, "ev_charging_sessions_cary_late.csv.gz")
        late_body = ";".join(header) + "\r\n"
        late_body += "".join(";".join(r) + "\r\n" for r in late_rows)
        with open(late_path, "wb") as fh:
            fh.write(gzip.compress(late_body.encode("utf-8-sig"), mtime=0))
    return {"rows": len(messy_rows), "late": len(late_rows)}


# --------------------------------------------------------------------------
# Degradation of Source B (GeoJSON)
# --------------------------------------------------------------------------

def _jitter(value: float, rng: random.Random, span: float = 0.01) -> float:
    return value + rng.uniform(-span, span)


def _json_safe(v: Any) -> Any:
    """Convert ijson Decimals back to int/float so json.dump can serialize."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json_safe(x) for x in v]
    return v


def degrade_source_b(seed_path: str, out_dir: str, rng: random.Random) -> int:
    """Rewrite Source B as a messy GeoJSON. Returns feature count emitted."""
    features: List[Dict[str, Any]] = []
    for feat in readers.iter_geojson_features(seed_path):
        props = dict(feat.get("properties") or {})
        geom = dict(feat.get("geometry") or {})
        # missing props
        for _ in range(rng.randrange(0, 5)):
            if props:
                props.pop(rng.choice(sorted(props.keys())))
        # coordinate jitter
        if geom.get("type") == "Point" and isinstance(geom.get("coordinates"), list):
            coords = list(geom["coordinates"])
            if rng.random() < 0.04 and len(coords) >= 2:
                coords[0] = _jitter(float(coords[0]), rng)
                coords[1] = _jitter(float(coords[1]), rng)
            geom["coordinates"] = coords
        # conflicting status
        if rng.random() < 0.03 and props.get("status_code") in ("E", "T"):
            props["status_code"] = "T" if props["status_code"] == "E" else "E"
        # date chaos: epoch-ms -> ISO / m/d/y
        if rng.random() < 0.15 and props.get("open_date") is not None:
            try:
                ms = int(props["open_date"])
                props["open_date"] = rng.choice(
                    ["2023-01-01", "1/1/23", str(ms), "01.01.2023"]
                )
            except (TypeError, ValueError):
                pass
        # dropped ev_charging_units -> fallback single-record path downstream
        if rng.random() < 0.06:
            props.pop("ev_charging_units", None)
        # duplicate features
        if rng.random() < 0.02:
            features.append({"type": "Feature", "properties": dict(props), "geometry": dict(geom)})
        features.append({"type": "Feature", "properties": props, "geometry": geom})

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "afdc_stations_nc_elec_messy.geojson.gz")
    collection = {"type": "FeatureCollection", "features": _json_safe(features)}
    with open(out_path, "wb") as fh:
        fh.write(gzip.compress(json.dumps(collection).encode("utf-8"), mtime=0))
    return len(features)


# --------------------------------------------------------------------------
# Synthetic Source C generation (regenerate + extend at scale)
# --------------------------------------------------------------------------

_EMAIL_TMPL = """From: {tech} <field-ops@contractor-mail.example>
Sent: {date}
Subj: RE: RE: site visit {station}

Hey team - swung by today. {finding} rated {rating} but pulling less.
Station: {station}
addr on file: {addr}
severity: {sev}   will file proper ticket later
- sent from my phone"""

_INSPECTION_TMPL = """QUARTERLY INSPECTION FORM (rev 3b)
insp.date : {date}
site/station.... {station}
technician:{tech}
charger rating   {rating}
findings -> {finding}; cosmetic scratches
severity code [{sev}]
followup req'd: {followup}"""

_INSTALL_TMPL = """INSTALL COMPLETION REPORT
{date} | crew: {tech}
new unit commissioned at {station}
nameplate: {rating}, {connectors}
punch list: {punch}
energized + verified. billing starts {billing}"""

_COMPLAINT_TMPL = """customer complaint via 311, logged {date}
location given: "{location}"
says {complaint}
routed to: {tech}  priority: {sev}"""

_FINDINGS = [
    "breaker trip x3 this wk",
    "comms dropout to network",
    "overtemp warning @ 147F",
    "display flicker, unit resets",
    "connector latch broken",
    "cable insulation cracked",
    "GFCI wont reset",
    "E-341 ground fault",
    "screen dead",
    "charger beeping loudly",
]

_COMPLAINTS = [
    "billed twice??",
    "charge stopped after 10 min",
    "spot ICEd, also charger beeping loudly",
    "cable too short",
    "screen dead",
]

_RATINGS = ["50 kW DCFC", "6600 W", "L2 dual port", "7.2", "120 kW DCFC"]
_CONNECTORS = ["dual J1772", "dual J1772 + CCS", "single J1772", "NACS"]
_PUNCH = ["GFCI wont reset", "E-341 ground fault", "overtemp warning @ 147F", "cosmetic scratches"]
_TECHS = ["M. Chen", "D.K.", "R. Singh - ChargeRight LLC", "priya (subcontr.)", "T. Okafor", "J. Alvarez"]

_SEV_WORDS = ["fyi", "minor", "major", "urgent-safety", "safety!!"]

_DATE_FMT_MAKERS = [
    lambda rng: f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.randint(2021, 2024)}",
    lambda rng: f"{rng.randint(1, 12)}/{rng.randint(1, 28)}/{rng.randint(21, 24)}",
    lambda rng: f"{rng.randint(2021, 2024)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
    lambda rng: rng.choice(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
    + f" {rng.randint(1, 28)}, {rng.randint(2021, 2024)}",
]


def _station_pool(seed_dir: str, rng: random.Random, want: int = 40) -> List[str]:
    """Real station names from Sources A/B so synthetic C references reality."""
    pool: List[str] = []
    a_path = os.path.join(seed_dir, "ev_charging_sessions_cary.csv.gz")
    if os.path.exists(a_path):
        for row, _issues in readers.iter_csv_records(a_path):
            name = row.get("station_name")
            if name:
                pool.append(name)
            if len(pool) >= want // 2:
                break
    b_path = os.path.join(seed_dir, "afdc_stations_nc_elec.geojson.gz")
    if os.path.exists(b_path):
        for feat in readers.iter_geojson_features(b_path):
            name = (feat.get("properties") or {}).get("station_name")
            if name:
                pool.append(name)
            if len(pool) >= want:
                break
    rng.shuffle(pool)
    return pool or ["TOWN OF CARY / DT DECK P2 (2)"]


def _pick(pool: List[str], rng: random.Random) -> str:
    return rng.choice(pool)


def _make_report(kind: str, station: str, addr: str, rng: random.Random) -> str:
    date = rng.choice(_DATE_FMT_MAKERS)(rng)
    sev = rng.choice(_SEV_WORDS)
    tech = rng.choice(_TECHS)
    rating = rng.choice(_RATINGS)
    finding = rng.choice(_FINDINGS)
    if kind == "email":
        return _EMAIL_TMPL.format(
            tech=tech, date=date, station=station, finding=finding, rating=rating,
            addr=addr, sev=sev,
        )
    if kind == "inspection":
        return _INSPECTION_TMPL.format(
            station=station, date=date, tech=tech, rating=rating,
            finding=finding, sev=sev, followup=rng.choice(["Y", "N", "y - parts ordered"]),
        )
    if kind == "install":
        return _INSTALL_TMPL.format(
            date=date, tech=tech, station=station, rating=rating,
            connectors=rng.choice(_CONNECTORS), punch=rng.choice(_PUNCH),
            billing=rng.choice(_DATE_FMT_MAKERS)(rng),
        )
    return _COMPLAINT_TMPL.format(
        date=date, location=rng.choice([station, addr]), complaint=rng.choice(_COMPLAINTS),
        tech=tech, sev=sev,
    )


def _make_digest(station: str, rng: random.Random, entries: int = 2) -> str:
    lines = ["*** AUTOMATED FAULT DIGEST (fwd by %s) ***" % rng.choice(_TECHS)]
    period = rng.choice(_DATE_FMT_MAKERS)(rng)
    lines.append(f"period ending {period}")
    for _ in range(entries):
        finding = rng.choice(_FINDINGS)
        count = rng.randint(1, 12)
        sev = rng.choice(_SEV_WORDS)
        station_here = station if entries == 1 else f"{station} #{rng.randint(1, 9)}"
        lines.append(
            f"{station_here} :: {finding} :: count={count} :: sev={sev}"
        )
    if rng.random() < 0.5:
        lines.append("NOTE units in W not kW on this feed")
    return "\n".join(lines) + "\n"


def generate_source_c(
    seed_dir: str, out_dir: str, rng: random.Random, scale: int, base_files: int = 24
) -> Dict[str, int]:
    """Regenerate the seed-sized report set, then extend with digest megafiles
    at scale. Returns {files, events}."""
    pool = _station_pool(seed_dir, rng)
    os.makedirs(out_dir, exist_ok=True)
    kinds = ["email", "inspection", "install", "complaint"]
    files = 0
    events = 0
    for i in range(base_files):
        kind = kinds[i % len(kinds)]
        station = _pick(pool, rng)
        addr = f"{rng.randint(100, 999)} {rng.choice(['Walnut St', 'E Park St', 'James Jackson Ave', 'Ambassador Loop'])} {rng.randint(27500, 27600)}"
        body = _make_report(kind, station, addr, rng)
        path = os.path.join(out_dir, f"report_{i:03d}_{kind}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        files += 1
        events += 1

    # Digest megafiles: the volume driver. Each line = one maintenance event.
    n_megafiles = scale // 25
    lines_per_file = 1000
    for mf in range(n_megafiles):
        path = os.path.join(out_dir, f"report_mega_{mf:04d}_fault.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("*** AUTOMATED FAULT DIGEST (bulk) ***\n")
            fh.write(f"period ending {rng.choice(_DATE_FMT_MAKERS)(rng)}\n")
            for _ in range(lines_per_file):
                station = _pick(pool, rng)
                finding = rng.choice(_FINDINGS)
                count = rng.randint(1, 12)
                sev = rng.choice(_SEV_WORDS)
                fh.write(f"{station} :: {finding} :: count={count} :: sev={sev}\n")
            fh.write("NOTE units in W not kW on this feed\n")
        files += 1
        events += lines_per_file
    return {"files": files, "events": events}


def inject(
    in_dir: str,
    out_dir: str,
    seed: int,
    scale: int,
    degrade: bool = True,
    late: bool = True,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)
    summary: Dict[str, Any] = {"seed": seed, "scale": scale, "out": out_dir}

    if degrade:
        a_path = os.path.join(in_dir, "ev_charging_sessions_cary.csv.gz")
        b_path = os.path.join(in_dir, "afdc_stations_nc_elec.geojson.gz")
        if os.path.exists(a_path):
            summary["source_a"] = degrade_source_a(a_path, out_dir, rng, late)
        if os.path.exists(b_path):
            summary["source_b"] = {"features": degrade_source_b(b_path, out_dir, rng)}

    summary["source_c"] = generate_source_c(in_dir, out_dir, rng, scale)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="src.ingestion.inject", description=__doc__)
    parser.add_argument("--in", dest="in_dir", default="data/seed")
    parser.add_argument("--out", dest="out_dir", default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scale", type=int, default=1, help="multiply synthetic Source C volume")
    parser.add_argument("--no-degrade", action="store_true", help="skip A/B degradation")
    parser.add_argument("--no-late", action="store_true", help="skip the late-arriving A batch")
    args = parser.parse_args(argv)

    summary = inject(
        args.in_dir, args.out_dir, args.seed, args.scale,
        degrade=not args.no_degrade, late=not args.no_late,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
