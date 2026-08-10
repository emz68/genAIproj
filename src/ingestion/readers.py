"""Format readers for P1 ingestion (task E2).

- Transparent ``.gz`` handling (sniffed by magic bytes, not by extension).
- Auto-detection of file type in ``data/raw/``.
- Streaming iterators - never whole-file loads (§2.3 streaming mandate).

Reader contract: each iterator yields ``(row: dict, issues: list[str])``
where ``row`` holds verbatim source values keyed by column/property name and
``issues`` carries ingestion-level parse problems (e.g. ``malformed_row``)
that the stage assembler folds into ``quality.issues``.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
from typing import Any, Dict, Iterator, List, Tuple

try:
    import ijson
except ImportError:  # pragma: no cover - exercised only when ijson is absent
    ijson = None

GZIP_MAGIC = b"\x1f\x8b"

CSV_DELIMITER_CANDIDATES = [";", ",", "\t"]


def open_maybe_gz(path: str):
    """Open a file for text reading, decompressing gzip transparently.

    Works regardless of extension - detection is by magic bytes, so a file
    named ``foo.csv`` that happens to be gzipped is handled correctly too.
    """
    with open(path, "rb") as fh:
        magic = fh.read(2)
    if magic == GZIP_MAGIC:
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return open(path, "rt", encoding="utf-8-sig", newline="")


def open_maybe_gz_bin(path: str):
    """Binary variant of :func:`open_maybe_gz` (for streaming JSON parsers)."""
    with open(path, "rb") as fh:
        magic = fh.read(2)
    if magic == GZIP_MAGIC:
        return gzip.open(path, "rb")
    return open(path, "rb")


def _is_gzip(path: str) -> bool:
    with open(path, "rb") as fh:
        return fh.read(2) == GZIP_MAGIC


def strip_gz(name: str) -> str:
    return name[:-3] if name.endswith(".gz") else name


# --------------------------------------------------------------------------
# Format auto-detection (E2)
# --------------------------------------------------------------------------

def detect_format(path: str) -> str:
    """Return ``'csv'`` | ``'geojson'`` | ``'text'`` for a file.

    Primary signal is the (de-gzipped) extension; unknown extensions fall
    back to content sniffing. Raises ``ValueError`` for files we cannot place.
    """
    base = strip_gz(os.path.basename(path))
    ext = os.path.splitext(base)[1].lower()
    if ext == ".csv":
        return "csv"
    if ext == ".geojson":
        return "geojson"
    if ext == ".txt":
        return "text"
    if ext in (".json", ""):
        return sniff_format(path)
    raise ValueError(
        f"cannot detect format for {os.path.basename(path)!r}: unsupported extension {ext!r}"
    )


def sniff_format(path: str) -> str:
    """Content-based detection for ambiguous files (JSON dump vs CSV vs text)."""
    with open_maybe_gz(path) as fh:
        head = fh.read(8192)
    stripped = head.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and obj.get("type") == "FeatureCollection":
                return "geojson"
        except json.JSONDecodeError:
            pass
        if '"features"' in stripped or '"FeatureCollection"' in stripped:
            return "geojson"
        return "text"
    if ";" in head or "," in head or "\t" in head:
        return "csv"
    return "text"


# --------------------------------------------------------------------------
# CSV reader (Source A and any structured tabular input)
# --------------------------------------------------------------------------

def _pick_delimiter(sample_line: str) -> str:
    counts = {d: sample_line.count(d) for d in CSV_DELIMITER_CANDIDATES}
    best_delim, best_count = max(counts.items(), key=lambda kv: kv[1])
    # Ties fall through to ';' when zero occurrences everywhere; for a
    # single-column file any delimiter is equivalent, so ';' is harmless.
    return best_delim if best_count > 0 else ";"


def _looks_like_header(cells: List[str]) -> bool:
    """A header row is all snake_case-ish identifiers (no timestamps/numbers)."""
    if not cells:
        return False
    token = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    return all(token.match(c.strip()) for c in cells if c.strip()) and len(cells) >= 2


def iter_csv_records(path: str) -> Iterator[Tuple[dict, List[str]]]:
    """Yield ``(row, issues)`` for every data row of a CSV file.

    Robustness handled here:
    - UTF-8 BOM (utf-8-sig) and CRLF (newline='').
    - Delimiter auto-detected among ``;`` ``,`` ``\\t``.
    - Merged/missing headers: if the first line is not a header, synthetic
      ``col_N`` names are used.
    - Duplicate header rows are skipped.
    - Rows with fewer cells than the header are padded with None (issue
      ``malformed_row``); rows with more cells keep the surplus under the
      ``_extra_columns`` key (issue ``unmapped_columns``).
    - Unparseable quoting falls back to a lenient manual splitter.
    """
    with open_maybe_gz(path) as fh:
        first_line = fh.readline()
        if not first_line:
            return
        delimiter = _pick_delimiter(first_line)
        header = _parse_line(first_line, delimiter)
        if not _looks_like_header(header):
            header = [f"col_{i + 1}" for i in range(len(header))]

    # Re-open and stream: gzip file objects support seek(0) but re-opening is
    # cheaper and avoids surprise re-decompression.
    with open_maybe_gz(path) as fh:
        reader = csv.reader(fh, delimiter=delimiter, quotechar='"')
        for lineno, raw_row in enumerate(reader, start=1):
            issues: List[str] = []
            if not raw_row or all(c.strip() == "" for c in raw_row):
                continue  # blank line
            if raw_row == header:
                continue  # duplicated header row
            row, issues = _normalize_row(raw_row, header, issues)
            yield row, issues


def _parse_line(line: str, delimiter: str) -> List[str]:
    try:
        return next(csv.reader([line], delimiter=delimiter, quotechar='"'))
    except csv.Error:
        # Lenient fallback: manual split, strip surrounding quotes.
        cells = line.rstrip("\r\n").split(delimiter)
        return [c.strip().strip('"') for c in cells]


def _normalize_row(raw_row: List[str], header: List[str], issues: List[str]) -> Tuple[Dict[str, Any], List[str]]:
    n = len(header)
    row: Dict[str, Any] = {}
    if len(raw_row) < n:
        issues.append("malformed_row")
        row = dict(zip(header, raw_row + [None] * (n - len(raw_row))))
    elif len(raw_row) > n:
        issues.append("unmapped_columns")
        row = dict(zip(header, raw_row[:n]))
        row["_extra_columns"] = raw_row[n:]
    else:
        row = dict(zip(header, raw_row))
    # Normalize empty strings to None (absent == null).
    row = {k: (v if v not in (None, "") else None) for k, v in row.items()}
    return row, issues


# --------------------------------------------------------------------------
# GeoJSON reader (Source B and any API dump)
# --------------------------------------------------------------------------

def iter_geojson_features(path: str) -> Iterator[dict]:
    """Yield one feature dict per feature, streaming when ijson is available.

    GeoJSON is a single JSON document; ``ijson`` streams the ``features``
    array item-by-item (satisfying the §2.3 streaming mandate). If ijson is
    not installed we fall back to a whole-document parse - acceptable for the
    committed seed, but ``ijson`` is declared in the module requirements so
    the streaming path is the one that runs in a properly provisioned env.
    """
    if ijson is not None:
        with open_maybe_gz_bin(path) as fh:
            yield from ijson.items(fh, "features.item")
    else:  # pragma: no cover - fallback path
        with open_maybe_gz(path) as fh:
            data = json.load(fh)
        for feat in data.get("features", []):
            yield feat
