"""Clear Hermes PYTHONPATH from os.environ (for subprocesses) and sys.path (for imports)."""
import os, sys

_BAD = ('hermes-agent', 'hermes/hermes-agent')
os.environ.pop('PYTHONPATH', None)
sys.path[:] = [p for p in sys.path if not any(b in p for b in _BAD)]
