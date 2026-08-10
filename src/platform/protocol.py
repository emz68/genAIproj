"""Shared §6 process-protocol helpers for the platform module and its stubs.

The contract every stage CLI must honor (PRODUCT_DEVELOPMENT_MANAGER.md §6):
- exit 0 on success, with the FINAL stderr line being {"metrics": {...}}
- exit nonzero on failure, with the FINAL stderr line being
  {"error": true, "stage": "<module>", "message": "...", "detail": {}}
- under --log-json, intermediate log lines are JSON objects with "log": true
"""

from __future__ import annotations

import gzip
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str, *, log_json: bool = False, **fields: Any) -> None:
    if log_json:
        print(json.dumps({"log": True, "message": message, **fields}), file=sys.stderr)
    else:
        print(message, file=sys.stderr)


def base_metrics(records_in: int, records_out: int, llm_calls: int = 0, llm_tokens: int = 0) -> dict:
    return {
        "records_in": records_in,
        "records_out": records_out,
        "llm_calls": llm_calls,
        "llm_tokens": llm_tokens,
    }


def emit_metrics(metrics: dict) -> None:
    """Final stderr line on success."""
    print(json.dumps({"metrics": metrics}), file=sys.stderr, flush=True)


def fail(stage: str, message: str, detail: dict | None = None, exit_code: int = 1) -> None:
    """Final stderr line on failure, then exit nonzero."""
    print(
        json.dumps({"error": True, "stage": stage, "message": message, "detail": detail or {}}),
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(exit_code)


def run_guarded(stage: str, fn):
    """§6 crash boundary: any unexpected exception exits via the error object."""
    import traceback

    try:
        return fn()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — stage boundary: anything → §6 error object
        fail(stage, f"{type(e).__name__}: {e}",
             detail={"traceback": traceback.format_exc().splitlines()[-6:]})


def strip_nones(value):
    """Recursively drop None-valued dict keys (§5.0: writers should omit nulls;
    readers treat absent == null, so this is always safe)."""
    if isinstance(value, dict):
        return {k: strip_nones(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [strip_nones(v) for v in value]
    return value


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def open_maybe_gzip(path: Path | str, mode: str = "rt"):
    """Open a file, transparently handling .gz (text mode, UTF-8, BOM-tolerant)."""
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8", errors="replace")
    return open(path, mode, encoding="utf-8-sig", errors="replace")


def iter_jsonl(path: Path | str) -> Iterator[tuple[int, dict]]:
    """Stream (lineno, obj) pairs. Raises ValueError with the line number on bad JSON."""
    with open_maybe_gzip(path) as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"invalid JSON at line {lineno}: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError(f"line {lineno} is not a JSON object")
            yield lineno, obj


class JsonlWriter:
    """Streaming JSONL writer that creates its parent directory (§2.3)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        ensure_parent_dir(self.path)
        self._fh: io.TextIOBase | None = None
        self.count = 0

    def __enter__(self) -> "JsonlWriter":
        self._fh = open(self.path, "w", encoding="utf-8")
        return self

    def write(self, obj: dict) -> None:
        assert self._fh is not None
        self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.count += 1

    def __exit__(self, *exc) -> None:
        assert self._fh is not None
        self._fh.close()


def add_common_flags(parser) -> None:
    """The three flags every stage must support (§6)."""
    parser.add_argument("--no-llm", action="store_true", help="deterministic fallback, no network")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="emit at most N output records total")
    parser.add_argument("--log-json", action="store_true", help="structured JSON log lines on stderr")


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip() in ("1", "true", "yes")
