from __future__ import annotations

import json

import pytest

from app import enterprise
from app.config import PROJECT_ROOT
from app.models import OpsReport

EXPECTED_PATH = PROJECT_ROOT / "data" / "expected" / "15_ops_incident.json"


@pytest.fixture(scope="module")
def expected() -> dict:
    return json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))


def test_expected_report_is_valid_ops_report(expected):
    report = OpsReport.model_validate(expected)
    assert report.incident_id == "INC-2026-11-06-01"
    assert report.root_cause_code == "ATP_MISMATCH_AND_BLOCKED_PO"
    assert report.confidence >= 0.7


def test_expected_numbers_match_readonly_tools(expected, settings):
    gap = enterprise.calculate_material_gap("ORD-2026-01201", "MAT-CU-2.5", settings)
    assert gap["need_kg"] == pytest.approx(expected["impact"]["copper_need_kg"], abs=0.01)
    assert gap["available_kg"] == expected["impact"]["available_kg"]
    assert gap["deficit_kg"] == pytest.approx(expected["impact"]["deficit_kg"], abs=0.01)

    risk = enterprise.calculate_delivery_risk("ORD-2026-01201", "2026-11-12", settings=settings)
    assert risk["days_late"] == expected["impact"]["days_late_without_action"]
    assert risk["penalty_rub"] == pytest.approx(expected["impact"]["penalty_rub"], abs=0.01)


def test_expected_actions_are_present(expected):
    ids = {a["action_id"] for a in expected["recommended_actions"]}
    assert {
        "reallocate_reserved_copper",
        "approve_price_variance_and_pay",
        "expedite_alternative_supplier",
        "notify_customer",
    } <= ids
