"""Makes harness.py importable by the test modules under pytest's
--import-mode=importlib (this directory is not a package)."""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]

for path in (str(_REPO_ROOT), str(_HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)
