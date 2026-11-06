from __future__ import annotations

import pytest

from app import enterprise


def test_atp_is_not_gross_stock(settings):
    stock = enterprise.get_wms_stock("MAT-CU-2.5", settings)
    assert stock["balance_kg"] == 2400.0
    assert stock["reserved_kg"] == 1650.0
    assert stock["quality_hold_kg"] == 250.0
    assert stock["safety_stock_kg"] == 300.0
    assert stock["available_kg"] == 200.0


def test_material_gap_for_incident_order(settings):
    gap = enterprise.calculate_material_gap("ORD-2026-01201", "MAT-CU-2.5", settings)
    assert gap["need_kg"] == pytest.approx(1701.212, abs=0.01)
    assert gap["available_kg"] == 200.0
    assert gap["deficit_kg"] == pytest.approx(1501.212, abs=0.01)
    assert gap["coverage_pct"] == pytest.approx(11.8, abs=0.1)


@pytest.mark.parametrize(
    ("arrival", "days_late", "penalty"),
    [
        ("2026-11-06", 0, 0.0),
        ("2026-11-08", 0, 0.0),
        ("2026-11-10", 0, 0.0),
        ("2026-11-12", 2, 27990.54),
    ],
)
def test_delivery_risk(settings, arrival, days_late, penalty):
    risk = enterprise.calculate_delivery_risk("ORD-2026-01201", arrival, settings=settings)
    assert risk["days_late"] == days_late
    assert risk["penalty_rub"] == pytest.approx(penalty, abs=0.01)


def test_donor_penalty_is_cheaper_than_incident_penalty(settings):
    main = enterprise.calculate_penalty("ORD-2026-01201", 2, settings)
    donor = enterprise.calculate_penalty("ORD-2026-01202", 1, settings)
    assert main["penalty_rub"] == pytest.approx(27990.54, abs=0.01)
    assert donor["penalty_rub"] == pytest.approx(2095.82, abs=0.01)
    assert donor["penalty_rub"] < main["penalty_rub"]


def test_search_emails_finds_supplier_delay(settings):
    results = enterprise.search_emails("поставщик оплата задержка", settings=settings)
    assert results
    assert results[0]["file"] == "01_supplier_delay.eml"
    assert "60" in results[0]["body"] or "60" in results[0]["subject"]


def test_search_documents_finds_reserve_rule(settings):
    results = enterprise.search_documents("перераспределение резерва операционный директор", settings=settings)
    assert results
    assert any("regulation_excerpt" in r["source"] for r in results)


def test_customer_lookup_accepts_name(settings):
    customer = enterprise.get_customer("АО «Энергосеть» (01)", settings)
    assert customer["customer"]["customer_id"] == "CUST-002-S01"
    assert customer["orders"][0]["order_id"] == "ORD-2026-01201"


def test_get_incident_reports_available_ids(settings):
    result = enterprise.get_incident("INC-2026-11-0601", settings)
    assert "error" in result
    assert "INC-2026-11-06-01" in result["available"]
