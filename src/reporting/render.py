"""P4 — Reporting & Experience: shared rendering helpers.

Deterministic, dependency-free HTML/CSV rendering: everything here is stdlib.
The fleet map is a data-driven SVG scatter plot — bounds are derived from the
records themselves (with a fallback bounding box matching the seed territory),
so no territory constant lives in code (per §7 P1 geography note).

All output is deterministic given its inputs: the only time-dependent value is
``generated_at``, which the CLI injects (and tests pin).
"""

from __future__ import annotations

import html as _html
import math
from typing import Iterable, List, Optional, Sequence, Tuple

# Fallback bounds (seed territory, North Carolina) used only when records
# carry no usable coordinates — mirrors Sanja's config default (§7 S1).
FALLBACK_BOUNDS = {"min_lat": 33.7, "max_lat": 36.6, "min_lon": -84.4, "max_lon": -75.4}

HEALTH_COLORS = {
    "HEALTHY": "#2e7d32",
    "DEGRADED": "#f9a825",
    "SUSPECT_OUTAGE": "#ef6c00",
    "SAFETY_REVIEW": "#c62828",
    "UNKNOWN": "#9e9e9e",
    None: "#9e9e9e",
}

SEVERITY_COLORS = {
    "SAFETY": "#c62828",
    "CRITICAL": "#d84315",
    "WARN": "#f9a825",
    "INFO": "#1565c0",
    "UNKNOWN": "#9e9e9e",
}


def esc(value) -> str:
    """HTML-escape any value for safe inline rendering."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def fmt_number(value, ndigits: int = 1) -> str:
    """Format a number or 'N/A' when null (absent == null per §5.0)."""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{ndigits}f}"
    return str(value)


def fmt_pct(value, ndigits: int = 1) -> str:
    """Format a fraction (0–1) as a percentage string; 'N/A' when null.

    ``metrics.est_uptime_pct`` is stored as a fraction per §5.4
    (1 − outage-days/days-in-period); display multiplies by 100.
    """
    if value is None:
        return "N/A"
    return f"{value * 100:.{ndigits}f}%"


def page(title: str, subtitle: str, body: str, generated_at: str) -> str:
    """Full standalone HTML page with inline CSS — no external assets."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
         margin: 0; background: #f4f6f8; color: #1c2733; }}
  header {{ background: #0d2b45; color: #fff; padding: 18px 28px; }}
  header h1 {{ margin: 0 0 4px; font-size: 22px; }}
  header p {{ margin: 0; color: #bcd0e0; font-size: 13px; }}
  main {{ padding: 22px 28px; max-width: 1200px; margin: 0 auto; }}
  section {{ background: #fff; border: 1px solid #dde3e9; border-radius: 8px;
             padding: 16px 18px; margin-bottom: 20px; }}
  h2 {{ font-size: 17px; margin: 0 0 12px; color: #0d2b45;
        border-bottom: 2px solid #e8eef3; padding-bottom: 8px; }}
  h3 {{ font-size: 14px; margin: 14px 0 8px; color: #2c4a63; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid #edf1f5; }}
  th {{ background: #f0f4f8; color: #33506b; font-weight: 600; }}
  tr:hover td {{ background: #fafcfe; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
               gap: 12px; }}
  .kpi {{ border: 1px solid #dde3e9; border-radius: 8px; padding: 12px 14px;
          background: #fbfcfe; }}
  .kpi .label {{ font-size: 12px; color: #5a6b7c; text-transform: uppercase;
                 letter-spacing: .04em; }}
  .kpi .value {{ font-size: 24px; font-weight: 700; color: #0d2b45; margin-top: 4px; }}
  .kpi .note {{ font-size: 11px; color: #7a8b9c; margin-top: 4px; }}
  .banner {{ border: 2px solid #c62828; background: #fdecec; color: #7f1d1d;
             border-radius: 8px; padding: 10px 14px; font-weight: 600; margin-bottom: 16px; }}
  .badge {{ display: inline-block; padding: 2px 9px; border-radius: 999px;
            font-size: 11px; font-weight: 700; color: #fff; }}
  .card-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
                gap: 12px; }}
  .card {{ border: 1px solid #dde3e9; border-left: 5px solid #9e9e9e;
           border-radius: 8px; padding: 10px 12px; background: #fff; }}
  .card .cid {{ font-weight: 700; font-size: 13px; }}
  .card .meta {{ font-size: 12px; color: #5a6b7c; margin-top: 4px; }}
  .anomaly {{ border-left: 5px solid #9e9e9e; padding: 8px 12px; margin-bottom: 8px;
              background: #fbfcfe; border-radius: 6px; }}
  .muted {{ color: #7a8b9c; font-size: 12px; }}
  footer {{ text-align: center; color: #8a9baa; font-size: 12px; padding: 14px; }}
  .pill {{ font-size: 11px; color: #33506b; background: #eef3f8; border-radius: 999px;
          padding: 2px 8px; margin-right: 4px; }}
</style>
</head>
<body>
<header>
  <h1>{esc(title)}</h1>
  <p>{esc(subtitle)} &middot; generated {esc(generated_at)}</p>
</header>
<main>
{body}
</main>
<footer>EV Charger Data Platform &middot; P4 Reporting (renders only; all metrics computed by earlier stages)</footer>
</body>
</html>
"""


def table(headers: Sequence[str], rows: Sequence[Sequence], empty_note: str = "No data") -> str:
    """HTML table; empty rows render a graceful 'No data' row."""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    if not rows:
        body = f'<tr><td colspan="{len(headers)}" class="muted">{esc(empty_note)}</td></tr>'
    else:
        body = "".join(
            "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
        )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def kpi_grid(items: Sequence[Tuple[str, str, str]]) -> str:
    """items: (label, value, note)."""
    cards = "".join(
        f'<div class="kpi"><div class="label">{esc(label)}</div>'
        f'<div class="value">{esc(value)}</div><div class="note">{esc(note)}</div></div>'
        for label, value, note in items
    )
    return f'<div class="kpi-grid">{cards}</div>'


def badge(text: str, color: str) -> str:
    return f'<span class="badge" style="background:{esc(color)}">{esc(text)}</span>'


# ---------------------------------------------------------------------------
# Fleet map (data-driven SVG scatter)
# ---------------------------------------------------------------------------


class Point:
    """Minimal map point — keeps the map streaming-friendly (no full records)."""

    __slots__ = ("lat", "lon", "state", "label")

    def __init__(self, lat: float, lon: float, state: Optional[str], label: str):
        self.lat = lat
        self.lon = lon
        self.state = state
        self.label = label

    @classmethod
    def from_charger(cls, c) -> "Point":
        return cls(
            lat=c.lat,
            lon=c.lon,
            state=(c.health.state if c.health else None),
            label=c.charger_id or c.golden_id or "?",
        )


def _bounds(points: List[Point]) -> dict:
    lats = [p.lat for p in points if p.lat is not None]
    lons = [p.lon for p in points if p.lon is not None]
    if len(lats) >= 2 and len(lons) >= 2:
        return {
            "min_lat": min(lats),
            "max_lat": max(lats),
            "min_lon": min(lons),
            "max_lon": max(lons),
        }
    return dict(FALLBACK_BOUNDS)


def svg_map(points: List[Point], width: int = 860, height: int = 560) -> str:
    """Scatter map of chargers colored by health state.

    Bounds are derived from the data when possible (fallback to the seed
    territory box otherwise). Renders inline — no external map tiles.
    """
    b = _bounds(points)
    pad_lat = max(0.05, (b["max_lat"] - b["min_lat"]) * 0.08)
    pad_lon = max(0.05, (b["max_lon"] - b["min_lon"]) * 0.08)
    min_lat, max_lat = b["min_lat"] - pad_lat, b["max_lat"] + pad_lat
    min_lon, max_lon = b["min_lon"] - pad_lon, b["max_lon"] + pad_lon

    def project(lat, lon):
        x = (lon - min_lon) / (max_lon - min_lon) * (width - 40) + 20
        y = (max_lat - lat) / (max_lat - min_lat) * (height - 60) + 20
        return x, y

    circles = []
    for p in points:
        if p.lat is None or p.lon is None:
            continue
        x, y = project(p.lat, p.lon)
        color = HEALTH_COLORS.get(p.state, HEALTH_COLORS[None])
        circles.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{color}" '
            f'stroke="#fff" stroke-width="1.5">'
            f'<title>{esc(p.label)} — {esc(p.state or "unknown")}</title></circle>'
        )
    legend = "".join(
        f'<span class="pill" style="color:#fff;background:{HEALTH_COLORS[s]}">{s}</span>'
        for s in ("HEALTHY", "DEGRADED", "SUSPECT_OUTAGE", "SAFETY_REVIEW")
    )
    if not circles:
        body = '<p class="muted">No charger coordinates available — map unavailable.</p>'
    else:
        body = (
            f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Fleet map by health state" style="width:100%;height:auto;'
            f'background:#eef3f8;border-radius:8px;">{ "".join(circles) }</svg>'
        )
    return f"<h3>Fleet map</h3>{body}<p class=\"muted\">Legend: {legend}</p>"
