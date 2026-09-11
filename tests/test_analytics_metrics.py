"""Pruebas del cálculo de métricas (sin base de datos)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from inventario.analytics.metrics import (
    CRITICAL,
    DEAD,
    HEALTHY,
    NO_HISTORY,
    OUT_OF_STOCK,
    SLOW,
    AnalyticsParams,
    BoundedRanking,
    InventoryReportBuilder,
    ProductSnapshot,
    classify_movement,
    classify_stock,
    coverage_days,
    days_between,
)

AS_OF = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
PARAMS = AnalyticsParams(sales_window_days=30, dead_stock_days=90, slow_coverage_days=60, max_items=2)


def snapshot(**overrides) -> ProductSnapshot:
    base = ProductSnapshot(
        id=1, sku="SKU-1", name="Funda iPhone", ml_item_id="MLM1", cost=Decimal("50.00"),
        price=Decimal("199.00"), stock=10, min_stock=2, active=True,
        created_at=AS_OF - timedelta(days=365), ledger_stock=10,
        last_movement_at=AS_OF - timedelta(days=5), last_sale_at=AS_OF - timedelta(days=5),
        units_sold_window=10, units_returned_window=0,
    )
    return replace(base, **overrides)


def test_days_between_rounds_down():
    assert days_between(AS_OF - timedelta(days=2, hours=23), AS_OF) == 2


@pytest.mark.parametrize(("stock", "net", "expected"), [
    (10, 5, Decimal("60.0")),
    (1, 7, Decimal("4.3")),  # 30 / 7 = 4.2857…
    (0, 5, Decimal("0.0")),
    (10, 0, None),
])
def test_coverage_days(stock, net, expected):
    assert coverage_days(stock, net, 30) == expected


def test_net_units_never_negative():
    assert snapshot(units_sold_window=1, units_returned_window=3).net_units_window == 0


@pytest.mark.parametrize(("overrides", "expected"), [
    ({"stock": 0}, OUT_OF_STOCK),
    ({"stock": 2, "min_stock": 2}, CRITICAL),
    ({"stock": 3, "min_stock": 2}, None),
    ({"stock": 0, "active": False}, None),
])
def test_classify_stock(overrides, expected):
    assert classify_stock(snapshot(**overrides)) == expected


@pytest.mark.parametrize(("overrides", "expected"), [
    ({}, HEALTHY),  # 10 unidades vendidas en 30 días -> 30 días de cobertura
    ({"last_sale_at": None}, DEAD),  # nunca vendido y con un año de antigüedad
    ({"last_sale_at": None, "created_at": AS_OF - timedelta(days=10)}, NO_HISTORY),
    ({"last_sale_at": AS_OF - timedelta(days=90), "units_sold_window": 0}, DEAD),
    ({"last_sale_at": AS_OF - timedelta(days=45), "units_sold_window": 0}, SLOW),  # vendió, pero no en la ventana
    ({"stock": 100, "units_sold_window": 10}, SLOW),  # 300 días de cobertura
    ({"stock": 0}, None),
    ({"active": False}, None),
])
def test_classify_movement(overrides, expected):
    assert classify_movement(snapshot(**overrides), AS_OF, PARAMS) == expected


@pytest.mark.parametrize(("overrides", "rule"), [
    ({"price": Decimal("0")}, "price_zero"),
    ({"cost": Decimal("0")}, "cost_zero"),
    ({"price": Decimal("40.00")}, "negative_margin"),
    ({"sku": None}, "missing_sku"),
    ({"ml_item_id": None}, "missing_ml_listing"),
    ({"name": " Mic "}, "incomplete_name"),
    ({"ledger_stock": 7}, "ledger_mismatch"),
    ({"ledger_stock": None}, "ledger_mismatch"),  # tiene stock pero ningún movimiento que lo respalde
    ({"active": False}, "inactive_with_stock"),
])
def test_each_anomaly_rule(overrides, rule):
    builder = InventoryReportBuilder(AS_OF, PARAMS)
    builder.add(snapshot(**overrides))
    rules = builder.build()["catalog_anomalies"]["rules"]

    assert {code for code, found in rules.items() if found["count"]} == {rule}


def test_clean_product_has_no_anomalies():
    builder = InventoryReportBuilder(AS_OF, PARAMS)
    builder.add(snapshot())

    assert builder.build()["catalog_anomalies"]["products_with_anomalies"] == 0


def test_bounded_ranking_keeps_top_items_regardless_of_order():
    keys = [5, 3, 9, 1, 7, 2, 8]
    forward, backward = BoundedRanking(3), BoundedRanking(3)
    for k in keys:
        forward.add((k,), {"id": k})
    for k in reversed(keys):
        backward.add((k,), {"id": k})

    assert forward.to_dict() == backward.to_dict() == {
        "count": 7, "truncated": True, "items": [{"id": 1}, {"id": 2}, {"id": 3}],
    }


def test_valuation_is_exact():
    builder = InventoryReportBuilder(AS_OF, PARAMS)
    builder.add(snapshot(id=1, stock=3, cost=Decimal("0.10"), price=Decimal("0.20"), ledger_stock=3))
    builder.add(snapshot(id=2, stock=7, cost=Decimal("0.20"), price=Decimal("0.30"), ledger_stock=7))
    builder.add(snapshot(id=3, stock=5, cost=Decimal("9.99"), active=False, ledger_stock=5))

    valuation = builder.build()["valuation"]

    assert valuation["total_units"] == 10
    assert valuation["cost_value"] == "1.70"  # 3*0.10 + 7*0.20 (con float daría 1.7000000000000002)
    assert valuation["retail_value"] == "2.70"
    assert valuation["potential_gross_margin"] == "1.00"
    assert valuation["potential_gross_margin_pct"] == "37.0"
    assert valuation["inactive_stock"] == {"products": 1, "units": 5, "cost_value": "49.95"}


def test_critical_items_sorted_by_coverage():
    builder = InventoryReportBuilder(AS_OF, replace(PARAMS, max_items=10))
    builder.add(snapshot(id=1, stock=2, min_stock=5, units_sold_window=1, ledger_stock=2))  # 60 días
    builder.add(snapshot(id=2, stock=2, min_stock=5, units_sold_window=0, ledger_stock=2))  # sin ventas
    builder.add(snapshot(id=3, stock=2, min_stock=5, units_sold_window=6, ledger_stock=2))  # 10 días

    items = builder.build()["stock_health"]["critical"]["items"]

    assert [i["id"] for i in items] == [3, 1, 2]
    assert [i["coverage_days"] for i in items] == ["10.0", "60.0", None]


@pytest.mark.parametrize("field", ["chunk_size", "sales_window_days", "statement_timeout_ms"])
def test_params_are_validated(field):
    with pytest.raises(ValueError, match=field):
        AnalyticsParams(**{field: 0})
