"""A1 — resumability manifest.

A stage is skipped on re-run only when its inputs digest, its parameters
digest (entrypoint argv + propagated flags), and all of its recorded outputs
are unchanged. The inputs digest of the ingestion stage covers the full
contents of data/raw/, so adding, removing, or modifying any raw file —
late-arriving data — invalidates ingestion and, through the output-digest
chain, every downstream stage (§7 A1).

The manifest also stores each stage's last metrics so skipped stages still
report meaningful numbers into pipeline_health.json.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_CHUNK = 1 << 20


def _hash_file(h: "hashlib._Hash", path: Path) -> None:
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)


def digest_path(path: Path | str) -> str:
    """Content digest of a file, or of a directory tree (sorted, streamed).

    Missing paths digest to a fixed sentinel so 'absent' is a stable state.
    """
    path = Path(path)
    h = hashlib.sha256()
    if not path.exists():
        h.update(b"<missing>")
        return h.hexdigest()
    if path.is_file():
        h.update(path.name.encode())
        _hash_file(h, path)
        return h.hexdigest()
    for sub in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(str(sub.relative_to(path)).encode())
        _hash_file(h, sub)
    return h.hexdigest()


def digest_paths(paths: list[Path | str]) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(digest_path(p).encode())
    return h.hexdigest()


def digest_params(argv: list[str]) -> str:
    return hashlib.sha256(json.dumps(argv).encode()).hexdigest()


class Manifest:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._data: dict = {"stages": {}}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {"stages": {}}  # corrupt manifest → full re-run
        self._data.setdefault("stages", {})

    def should_skip(self, stage: str, inputs_digest: str, params_digest: str,
                    outputs: list[Path]) -> bool:
        entry = self._data["stages"].get(stage)
        if not entry:
            return False
        if entry.get("inputs_digest") != inputs_digest:
            return False
        if entry.get("params_digest") != params_digest:
            return False
        recorded = entry.get("outputs", {})
        for out in outputs:
            out = Path(out)
            if not out.exists():
                return False
            if recorded.get(str(out)) != digest_path(out):
                return False
        return True

    def last_metrics(self, stage: str) -> dict | None:
        entry = self._data["stages"].get(stage)
        return entry.get("metrics") if entry else None

    def record(self, stage: str, inputs_digest: str, params_digest: str,
               outputs: list[Path], metrics: dict | None) -> None:
        self._data["stages"][stage] = {
            "inputs_digest": inputs_digest,
            "params_digest": params_digest,
            "outputs": {str(out): digest_path(out) for out in outputs},
            "metrics": metrics,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
