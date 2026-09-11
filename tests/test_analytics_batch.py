"""Pruebas del batch de analítica contra PostgreSQL."""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from inventario.analytics import (
    AnalyticsParams,
    apply_indexes,
    missing_indexes,
    persist_report,
    run_inventory_analytics,
)
from inventario.analytics.batch import read_only_snapshot
from inventario.analytics.sql import RECOMMENDED_INDEXES, index_ddl
from inventario.models import AnalyticsRun, MovementType
from inventario.services import ProductData


@pytest.fixture
def seeded(service, engine):
    """Catálogo pequeño con un caso de cada métrica. Devuelve {nombre: id}."""

    def product(name, stock, **kwargs):
        data = ProductData(name=name, sku=kwargs.pop("sku", name.upper()[:8]),
                           ml_item_id=kwargs.pop("ml", f"MLM{len(name)}{stock}"),
                           cost=Decimal(kwargs.pop("cost", "50.00")), price=Decimal(kwargs.pop("price", "199.00")),
                           min_stock=kwargs.pop("min_stock", 2))
        return service.create_product(data, initial_stock=stock).id

    ids = {
        "healthy": product("Funda silicón", 20),
        "dead": product("Audífonos viejos", 8, cost="180.00"),
        "critical": product("Cable USB-C", 3, min_stock=5),
        "out": product("Mica cristal", 2),
        "zero_price": product("Soporte auto", 4, price="0"),
    }
    for _ in range(3):
        service.register_movement(ids["healthy"], MovementType.VENTA, 2)
    service.register_movement(ids["critical"], MovementType.VENTA, 1)
    service.register_movement(ids["out"], MovementType.VENTA, 2)
    service.register_movement(ids["dead"], MovementType.VENTA, 1)

    # Envejece el producto estancado: creado y vendido por última vez hace 120 días.
    with engine.begin() as conn:
        conn.execute(text("UPDATE products SET created_at = now() - interval '200 days' WHERE id = :id"),
                     {"id": ids["dead"]})
        conn.execute(text("UPDATE stock_movements SET created_at = now() - interval '120 days' "
                          "WHERE product_id = :id"), {"id": ids["dead"]})
    return ids


def _without_run_metadata(report):
    return {k: v for k, v in report.items() if k != "run"}


def test_report_detects_each_metric(engine, seeded):
    report = run_inventory_analytics(engine, AnalyticsParams()).report

    assert report["products_scanned"] == 5
    assert [i["id"] for i in report["stock_health"]["out_of_stock"]["items"]] == [seeded["out"]]
    assert [i["id"] for i in report["stock_health"]["critical"]["items"]] == [seeded["critical"]]
    dead = report["movement_health"]["dead"]
    assert [i["id"] for i in dead["items"]] == [seeded["dead"]]
    assert dead["cost_value"] == "1260.00"  # 7 unidades x 180.00
    assert dead["items"][0]["days_without_sales"] == 120
    price_zero = report["catalog_anomalies"]["rules"]["price_zero"]
    assert [i["id"] for i in price_zero["items"]] == [seeded["zero_price"]]


def test_valuation_matches_database_aggregate(engine, seeded):
    report = run_inventory_analytics(engine, AnalyticsParams(chunk_size=2)).report

    with engine.connect() as conn:
        expected = conn.execute(text(
            "SELECT sum(stock * cost), sum(stock * price), sum(stock) FROM products WHERE active"
        )).one()
    assert report["valuation"]["cost_value"] == str(expected[0])
    assert report["valuation"]["retail_value"] == str(expected[1])
    assert report["valuation"]["total_units"] == expected[2]


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 1000])
def test_result_does_not_depend_on_chunk_size(engine, seeded, chunk_size):
    reference = run_inventory_analytics(engine, AnalyticsParams(chunk_size=1000)).report

    result = run_inventory_analytics(engine, AnalyticsParams(chunk_size=chunk_size),
                                     as_of=datetime.fromisoformat(reference["as_of"]))

    expected = _without_run_metadata(reference)
    actual = _without_run_metadata(result.report)
    expected["parameters"]["chunk_size"] = actual["parameters"]["chunk_size"]
    assert actual == expected
    assert result.report["run"]["chunks"] == -(-5 // chunk_size)  # techo de 5 / chunk_size


def test_detects_stock_edited_outside_the_app(engine, service, seeded):
    with engine.begin() as conn:
        conn.execute(text("UPDATE products SET stock = 99 WHERE id = :id"), {"id": seeded["healthy"]})

    report = run_inventory_analytics(engine, AnalyticsParams()).report

    [item] = report["catalog_anomalies"]["rules"]["ledger_mismatch"]["items"]
    assert (item["id"], item["stock"], item["ledger_stock"]) == (seeded["healthy"], 99, 14)


def test_snapshot_is_read_only(engine, service):
    with engine.connect() as conn, pytest.raises(DBAPIError, match="read-only"):
        with read_only_snapshot(conn, statement_timeout_ms=5_000):
            conn.execute(text("UPDATE products SET stock = 0"))


def test_empty_catalog(engine, service):
    report = run_inventory_analytics(engine, AnalyticsParams()).report

    assert report["products_scanned"] == 0
    assert report["valuation"]["cost_value"] == "0.00"
    assert report["run"]["chunks"] == 0


def test_persist_report(engine, seeded):
    result = run_inventory_analytics(engine, AnalyticsParams())

    run_id = persist_report(engine, result)

    with engine.connect() as conn:
        stored = conn.execute(select(AnalyticsRun).where(AnalyticsRun.id == run_id)).one()
    assert stored.report == result.report
    assert stored.as_of == result.as_of


def test_indexes_exist_and_apply_is_idempotent(engine):
    assert missing_indexes(engine) == []
    apply_indexes(engine)  # IF NOT EXISTS: no falla aunque ya existan
    assert missing_indexes(engine) == []
    assert all(index_ddl(i).startswith("CREATE INDEX CONCURRENTLY IF NOT EXISTS") for i in RECOMMENDED_INDEXES)


def test_as_of_requires_timezone(engine):
    with pytest.raises(ValueError, match="zona horaria"):
        run_inventory_analytics(engine, AnalyticsParams(), as_of=datetime(2026, 1, 1))
