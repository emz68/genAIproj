"""Maintenance-event extraction from unstructured contractor reports (E3).

Two parsing paths, both returning event dicts shaped for schema §5.3:

- ``parse_maintenance_regex`` - the deterministic ``--no-llm`` fallback. Runs
  offline, needs no API key, and is what every test exercises.
- ``parse_maintenance_llm`` - Claude API path (anthropic SDK, requires
  ``ANTHROPIC_API_KEY``). The caller falls back to the regex path if the LLM
  call fails, recording an ``llm_fallback`` issue.

Semantics (boundary rules §7 / §5.0):
- Values are **verbatim source strings** - dates, severities, ratings and
  station names are extracted and placed, never normalized. Mapping messy
  severity words (``fyi``/``safety!!``) to canonical values is P2's job; here
  we only choose the closest canonical label as an *extraction* decision.
- ``quality.score`` = extraction confidence (0.0-1.0), per §5.0.
- One file may yield many events (automated fault digests list one entry per
  station). ``event_id`` is a deterministic hash of (source file, index).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

EVENT_TYPES = {"INSPECTION", "REPAIR", "FAULT", "INSTALL", "COMPLAINT", "OTHER"}
SEVERITIES = {"INFO", "MINOR", "MAJOR", "SAFETY"}

DEFAULT_MODEL = "claude-sonnet-4-5"


# --------------------------------------------------------------------------
# Keyword tables (extraction, not normalization)
# --------------------------------------------------------------------------

_SEVERITY_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"safety|urgent", re.I), "SAFETY"),
    (re.compile(r"major", re.I), "MAJOR"),
    (re.compile(r"minor", re.I), "MINOR"),
    (re.compile(r"fyi|routine|info", re.I), "INFO"),
]

_TYPE_RULES: List[Tuple[re.Pattern, str]] = [
    # Report-level markers first (they name the event itself).
    (re.compile(r"install completion|commissioned|energized", re.I), "INSTALL"),
    (re.compile(r"inspection form|insp\.", re.I), "INSPECTION"),
    (re.compile(r"complaint via 311|311", re.I), "COMPLAINT"),
    (re.compile(r"fault digest", re.I), "FAULT"),
    # Then body keywords for free-form emails.
    (re.compile(r"breaker trip|comms dropout|overtemp|display flicker|unit resets?|ground fault|gfci|fault", re.I), "FAULT"),
    (re.compile(r"latch broken|cable insulation cracked|cable too short|wont reset|won't reset|screen dead|beeping", re.I), "REPAIR"),
]

# filename suffix -> strong type hint (digest/install/inspection/complaint).
_FILENAME_TYPE_HINT = {
    "fault": "FAULT",
    "install": "INSTALL",
    "inspection": "INSPECTION",
    "complaint": "COMPLAINT",
    "email": None,
}

# Labeled lines that carry useful facts, keyed by extracted_fields name.
_LABELED_FIELDS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"routed\s+to:\s*(.+?)\s*$", re.M | re.I), "routed_to"),
    (re.compile(r"technician\s*:\s*(.+?)\s*$", re.M | re.I), "technician"),
    (re.compile(r"crew\s*:\s*(.+?)\s*$", re.M | re.I), "crew"),
    (re.compile(r"punch\s*list\s*:\s*(.+?)\s*$", re.M | re.I), "punch_list"),
    (re.compile(r"followup\s*req'?d\s*:\s*(.+?)\s*$", re.M | re.I), "followup"),
    (re.compile(r"billing\s*starts\s*(.+?)\s*$", re.M | re.I), "billing_starts"),
    (re.compile(r"nameplate\s*:\s*(.+?)\s*$", re.M | re.I), "nameplate"),
    (re.compile(r"addr on file:\s*(.+?)\s*$", re.M | re.I), "address"),
    (re.compile(r"location given:\s*\"?(.+?)\"?\s*$", re.M | re.I), "location"),
]

_DATE_RE = re.compile(
    r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|[A-Z][a-z]{2,9}\s+\d{1,2},?\s+\d{4})\b"
)

# Format-specific date labels, highest priority first.
_DATE_LABEL_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"period\s+ending\s*[:]?\s*(\S+.*)$", re.M | re.I), "digest"),
    (re.compile(r"^sent\s*:\s*(.+)$", re.M | re.I), "email"),
    (re.compile(r"insp\.date\s*:\s*(.+)$", re.M | re.I), "inspection"),
    (re.compile(r"logged\s+(.+?)\s*$", re.M | re.I), "complaint"),
    (re.compile(r"^\s*(\d{1,2}[/.]\d{1,2}[/.]\d{2,4})\s*\|", re.M | re.I), "install"),
]

_STATION_RULES: List[re.Pattern] = [
    re.compile(r"station\s*:\s*(.+?)\s*$", re.M | re.I),
    re.compile(r"site/station\.{3,}\s*(.+?)\s*$", re.M | re.I),
    re.compile(r"new unit commissioned at\s*(.+?)\s*$", re.M | re.I),
]

_ADDRESS_KEYS = ("address", "location")  # _LABELED_FIELDS entries usable as station fallback

_RATING_RE = re.compile(
    r"rated\s*([\d.]+\s*(?:kW|W|MW)(?:\s*DCFC)?|L1|L2|DCFC|level\s*\d)", re.I
)
_RATING_LINE_RE = re.compile(
    r"(?:nameplate|charger\s*rating|rating)\s*:?\s*([\d.]+\s*(?:kW|W|MW)(?:\s*DCFC)?|L1|L2|DCFC|L2 dual port|dual\s+J1772|dual port)", re.I
)

_SEVERITY_LABEL_RE = re.compile(
    r"(?:severity\s*:\s*|severity\s*code\s*\[?|priority\s*:\s*|sev\s*=\s*)([A-Za-z!-]+)", re.I
)

_NOTE_RE = re.compile(r"^\s*NOTE\b.*$", re.M | re.I)

# One digest entry:  STATION :: description [:: count=N] [:: sev=X]
_DIGEST_LINE_RE = re.compile(
    r"^\s*(.+?)\s*::\s*(.+?)(?:\s*::\s*count=(\d+))?(?:\s*::\s*sev=([A-Za-z!]+))?\s*$"
)
_DIGEST_HEADER_RE = re.compile(r"fault\s+digest", re.I)

# Formats whose report-level marker covers the whole file as one event.
_SINGLE_EVENT_MARKERS = [
    re.compile(r"install completion report", re.I),
    re.compile(r"quarterly inspection form", re.I),
    re.compile(r"customer complaint via 311", re.I),
]


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip().strip('"').strip()
    return value or None


def _map_severity(text: Optional[str], hint: Optional[str] = None) -> Optional[str]:
    candidates = [hint, text]
    for src in candidates:
        if not src:
            continue
        for pattern, sev in _SEVERITY_RULES:
            if pattern.search(src):
                return sev
    return None


def _event_type_from_filename(filename: str) -> Optional[str]:
    base = os.path.basename(filename).lower()
    for key, hint in _FILENAME_TYPE_HINT.items():
        if f"_{key}." in base or base.startswith(key):
            return hint
    return None


def _infer_event_type(text: str, filename: str, digest_line: Optional[str] = None) -> str:
    haystack = " ".join(filter(None, [digest_line, text]))
    for pattern, etype in _TYPE_RULES:
        if pattern.search(haystack):
            return etype
    hint = _event_type_from_filename(filename)
    if hint:
        return hint
    return "OTHER"


def _extract_date(text: str) -> Optional[str]:
    """Pick the most relevant date string, verbatim, format-specific first."""
    for pattern, fmt in _DATE_LABEL_RULES:
        m = pattern.search(text)
        if m:
            return _clean(m.group(1))
    m = _DATE_RE.search(text)
    return _clean(m.group(1)) if m else None


def _extract_station(text: str) -> Optional[str]:
    for pattern in _STATION_RULES:
        m = pattern.search(text)
        if m:
            return _clean(m.group(1))
    return None


def _extract_rating(text: str) -> Optional[str]:
    for pattern in (_RATING_LINE_RE, _RATING_RE):
        m = pattern.search(text)
        if m:
            return _clean(m.group(1))
    return None


def _split_digest_lines(text: str) -> List[str]:
    """Split a fault digest into its per-station entries."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _DIGEST_HEADER_RE.search(line) or _NOTE_RE.match(line):
            continue
        if "::" in line:
            lines.append(line)
    return lines


def _is_single_event_format(text: str) -> bool:
    return any(m.search(text) for m in _SINGLE_EVENT_MARKERS)


def _event_id(filename: str, index: int, station: Optional[str]) -> str:
    key = f"{os.path.basename(filename)}#{index}#{station or ''}"
    return "maint-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _confidence(station: bool, date: bool, sev: bool, etype: bool, structured: bool) -> float:
    have = sum([station, date, sev, etype])
    base = {4: 0.9, 3: 0.8, 2: 0.65, 1: 0.5, 0: 0.4}[have]
    if structured:
        base = min(0.95, base + 0.05)
    return round(base, 2)


def _collect_labeled_fields(text: str) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for pattern, key in _LABELED_FIELDS:
        m = pattern.search(text)
        if m:
            fields[key] = _clean(m.group(1))
    return fields


def parse_maintenance_regex(
    text: str, filename: str, stats: Optional[Dict[str, int]] = None
) -> List[Dict[str, Any]]:
    """Regex/heuristic extraction — the deterministic ``--no-llm`` path."""
    text = text or ""
    events: List[Dict[str, Any]] = []
    structured = bool(_is_single_event_format(text) or _DIGEST_HEADER_RE.search(text))

    digest_lines = _split_digest_lines(text)
    if digest_lines and _DIGEST_HEADER_RE.search(text):
        # Fault digest: one event per entry line.
        digest_date = _extract_date(text)
        notes = _collect_labeled_fields(text)
        note_m = _NOTE_RE.search(text)
        if note_m:
            notes["notes"] = _clean(note_m.group(0).replace("NOTE", "", 1).lstrip(": "))
        for i, line in enumerate(digest_lines):
            m = _DIGEST_LINE_RE.match(line)
            if not m:
                continue
            station, desc = m.group(1), m.group(2)
            count = m.group(3)
            sev_hint = m.group(4)
            sev = _map_severity(sev_hint or "", sev_hint) or _map_severity(text, sev_hint)
            etype = "FAULT"
            fields: Dict[str, Any] = dict(notes)
            if count:
                fields["count"] = count
            events.append(
                {
                    "event_id": _event_id(filename, i, station),
                    "event_date": digest_date,
                    "event_type": etype,
                    "severity": sev,
                    "station_name": _clean(station),
                    "description": _clean(desc) or text.strip(),
                    "extracted_fields": fields,
                    "confidence": _confidence(
                        bool(station), bool(digest_date), bool(sev), True, True
                    ),
                }
            )
        return events

    if structured:
        # Single-event form: inspection / install / complaint.
        station = _extract_station(text) or _extract_location(text)
        fields = _collect_labeled_fields(text)
        rating = _extract_rating(text)
        if rating:
            fields["power_rating"] = rating
        etype = _infer_event_type(text, filename)
        sev = _map_severity(_severity_label(text)) or _map_severity(text)
        events.append(
            {
                "event_id": _event_id(filename, 0, station),
                "event_date": _extract_date(text),
                "event_type": etype,
                "severity": sev,
                "station_name": station,
                "description": text.strip(),
                "extracted_fields": fields,
                "confidence": _confidence(
                    bool(station), True, bool(sev), True, True
                ),
            }
        )
        return events

    # Free-form email / unknown: single event, content-based classification.
    station = _extract_station(text) or _extract_location(text)
    fields = _collect_labeled_fields(text)
    rating = _extract_rating(text)
    if rating:
        fields["power_rating"] = rating
    sev = _map_severity(_severity_label(text)) or _map_severity(text)
    events.append(
        {
            "event_id": _event_id(filename, 0, station),
            "event_date": _extract_date(text),
            "event_type": _infer_event_type(text, filename),
            "severity": sev,
            "station_name": station,
            "description": text.strip(),
            "extracted_fields": fields,
            "confidence": _confidence(
                bool(station), bool(_extract_date(text)), bool(sev), True, False
            ),
        }
    )
    return events


def _severity_label(text: str) -> Optional[str]:
    m = _SEVERITY_LABEL_RE.search(text)
    return _clean(m.group(1)) if m else None


def _extract_location(text: str) -> Optional[str]:
    for pattern, key in _LABELED_FIELDS:
        if key not in _ADDRESS_KEYS:
            continue
        m = pattern.search(text)
        if m:
            return _clean(m.group(1))
    return None


# --------------------------------------------------------------------------
# LLM path (Claude)
# --------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """You extract maintenance events from unstructured EV-charger contractor reports (Con Edison Use Case 3).

Return ONLY a JSON array - no markdown fences, no prose, nothing else. Each element has exactly this shape:
{
  "event_type": one of INSPECTION|REPAIR|FAULT|INSTALL|COMPLAINT|OTHER,
  "severity": one of INFO|MINOR|MAJOR|SAFETY,
  "event_date": "verbatim date string from the report, or null",
  "station_name": "verbatim station/location string, or null",
  "description": "short factual summary using the report's own words",
  "extracted_fields": {"free-form": "key/values you pulled out, e.g. power_rating, address, count, notes, routed_to, technician"},
  "confidence": 0.0 to 1.0
}

Rules:
- A single file may contain MULTIPLE events (e.g. automated fault digests have one entry per station line). Emit one element per event.
- Values must be VERBATIM from the report - never convert dates, units, or severity words.
- description must never be empty.
- confidence reflects how confidently the event was extracted."""


def _parse_llm_json(content: str) -> List[Dict[str, Any]]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    data = json.loads(content)
    if isinstance(data, dict):  # tolerate a single-event object
        data = [data]
    if not isinstance(data, list):
        raise ValueError("LLM returned non-list JSON")
    return data


def parse_maintenance_llm(
    text: str,
    filename: str,
    client: Any,
    model: Optional[str] = None,
    max_tokens: int = 1024,
    stats: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """Claude-based extraction. Raises on any failure so the caller can fall
    back to the regex path (recording ``llm_fallback``)."""
    model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=LLM_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    if stats is not None:
        stats["llm_calls"] += 1
        usage = getattr(resp, "usage", None)
        if usage is not None:
            stats["llm_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
            stats["llm_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)

    raw = "".join(block.text for block in resp.content if getattr(block, "text", None))
    data = _parse_llm_json(raw)

    events: List[Dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        station = _clean(item.get("station_name"))
        conf = item.get("confidence")
        try:
            conf = max(0.0, min(1.0, float(conf))) if conf is not None else 0.9
        except (TypeError, ValueError):
            conf = 0.9
        fields = dict(item.get("extracted_fields") or {})
        events.append(
            {
                "event_id": _event_id(filename, i, station),
                "event_date": _clean(item.get("event_date")),
                "event_type": _clean(item.get("event_type")),
                "severity": _clean(item.get("severity")),
                "station_name": station,
                "description": _clean(item.get("description")) or text.strip(),
                "extracted_fields": fields,
                "confidence": round(conf, 2),
            }
        )
    if not events:
        raise ValueError("LLM returned no events")
    return events


def parse_maintenance(
    text: str,
    filename: str,
    use_llm: bool,
    client: Any = None,
    stats: Optional[Dict[str, int]] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Dispatch: LLM path with automatic regex fallback.

    Returns ``(events, llm_fallback_issue)`` where the second element is
    ``"llm_fallback"`` when the LLM path was attempted but failed.
    """
    if use_llm:
        try:
            return parse_maintenance_llm(text, filename, client, stats=stats), None
        except Exception as exc:  # noqa: BLE001 - deliberate fallback boundary
            if stats is not None:
                stats.setdefault("llm_fallback", 0)
                stats["llm_fallback"] += 1
            if client is None:
                raise RuntimeError(
                    "LLM parsing requested but no client provided; use --no-llm "
                    "or set ANTHROPIC_API_KEY"
                ) from exc
            return parse_maintenance_regex(text, filename, stats), "llm_fallback"
    return parse_maintenance_regex(text, filename, stats), None
