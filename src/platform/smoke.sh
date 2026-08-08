#!/usr/bin/env bash
# A4 — the Integration Manager's one-liner (§8 steps 4-5).
# Runs platform unit tests + the integration harness, then an end-to-end
# pipeline: real §6 modules when they are merged, stubs otherwise.
set -euo pipefail
cd "$(dirname "$0")/../.."  # repo root

PY="${PYTHON:-python3}"

"$PY" -m pytest src/platform tests/integration -q

STUB_FLAG=""
if ! "$PY" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('src.ingestion.run') else 1)"; then
  echo "real stage modules not merged yet — running stubs (--use-stubs)"
  STUB_FLAG="--use-stubs"
fi

mkdir -p data/raw
"$PY" -m src.platform.run --pipeline full --config src/platform/config.yaml \
  --no-llm --limit 500 ${STUB_FLAG}

echo "SMOKE OK"
