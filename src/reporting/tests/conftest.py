"""Shared pytest fixtures/helpers for the reporting module (P4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.reporting.kpis import Kpis
from src.reporting.models import (
    PipelineHealth,
    ReconciliationReport,
    ValidationReport,
    parse_golden_line,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_golden_records(path: Path = FIXTURES / "golden.jsonl"):
    with open(path, "r", encoding="utf-8") as fh:
        return [parse_golden_line(line) for line in fh if line.strip()]


@pytest.fixture(scope="module")
def records():
    return load_golden_records()


@pytest.fixture(scope="module")
def kpis(records):
    return Kpis(records)


@pytest.fixture(scope="module")
def validation_report():
    return ValidationReport.model_validate(
        json.loads((FIXTURES / "validation_report.json").read_text(encoding="utf-8"))
    )


@pytest.fixture(scope="module")
def reconciliation_report():
    return ReconciliationReport.model_validate(
        json.loads((FIXTURES / "reconciliation_report.json").read_text(encoding="utf-8"))
    )


@pytest.fixture(scope="module")
def pipeline_health():
    return PipelineHealth.model_validate(
        json.loads((FIXTURES / "pipeline_health.json").read_text(encoding="utf-8"))
    )


@pytest.fixture(scope="module")
def prior_kpis():
    return json.loads((FIXTURES / "prior_kpis.json").read_text(encoding="utf-8"))
