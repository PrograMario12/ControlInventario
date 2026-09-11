from decimal import Decimal

import pytest

from inventario.models import MovementType
from inventario.services import InventoryError, InventoryService, ProductData


def _product(service: InventoryService, name="Funda iPhone", stock=10, **kwargs):
    data = ProductData(name=name, cost=Decimal("50.00"), price=Decimal("199.00"), **kwargs)
    return service.create_product(data, initial_stock=stock)


def test_create_product_records_initial_stock(service):
    product = _product(service, sku="FUN-01", stock=10)

    assert product.stock == 10
    [movement] = service.list_movements(product.id)
    assert movement.kind is MovementType.ENTRADA
    assert movement.quantity == 10
    assert movement.stock_after == 10
    assert movement.unit_cost == Decimal("50.00")


def test_create_product_without_stock_has_no_movements(service):
    product = _product(service, stock=0)

    assert product.stock == 0
    assert service.list_movements(product.id) == []


def test_sale_reduces_stock_and_keeps_sale_price(service):
    product = _product(service, stock=10)

    movement = service.register_movement(product.id, MovementType.VENTA, 3, unit_price=Decimal("179.90"))

    assert movement.quantity == -3
    assert movement.stock_after == 7
    assert movement.unit_price == Decimal("179.90")
    assert movement.unit_cost == Decimal("50.00")
    assert service.get_product(product.id).stock == 7


def test_sale_defaults_to_product_price(service):
    product = _product(service, stock=5)

    movement = service.register_movement(product.id, MovementType.VENTA, 1)

    assert movement.unit_price == Decimal("199.00")


def test_sale_cannot_exceed_stock(service):
    product = _product(service, stock=2)

    with pytest.raises(InventoryError, match="Stock insuficiente"):
        service.register_movement(product.id, MovementType.VENTA, 3)

    assert service.get_product(product.id).stock == 2
    assert len(service.list_movements(product.id)) == 1


def test_entry_and_return_increase_stock(service):
    product = _product(service, stock=1)

    service.register_movement(product.id, MovementType.ENTRADA, 5, unit_cost=Decimal("45.00"))
    service.register_movement(product.id, MovementType.DEVOLUCION, 1)

    assert service.get_product(product.id).stock == 7


@pytest.mark.parametrize("quantity", [0, -1])
def test_quantity_must_be_positive(service, quantity):
    product = _product(service)

    with pytest.raises(InventoryError):
        service.register_movement(product.id, MovementType.ENTRADA, quantity)


def test_adjust_sets_counted_stock(service):
    product = _product(service, stock=10)

    movement = service.adjust_stock(product.id, 8, note="Conteo físico")

    assert movement.kind is MovementType.AJUSTE
    assert movement.quantity == -2
    assert service.get_product(product.id).stock == 8


def test_adjust_without_difference_records_nothing(service):
    product = _product(service, stock=4)

    assert service.adjust_stock(product.id, 4) is None
    assert len(service.list_movements(product.id)) == 1


def test_duplicate_sku_is_rejected(service):
    _product(service, name="A", sku="SKU-1")

    with pytest.raises(InventoryError, match="SKU"):
        _product(service, name="B", sku="SKU-1")


def test_duplicate_ml_item_id_is_rejected_case_insensitively(service):
    _product(service, name="A", ml_item_id="MLM123")

    with pytest.raises(InventoryError, match="Mercado Libre"):
        _product(service, name="B", ml_item_id=" mlm123 ")


def test_blank_optional_fields_are_stored_as_null(service):
    a = _product(service, name="A", sku="  ", ml_item_id="")
    b = _product(service, name="B", sku="", ml_item_id=" ")

    assert a.sku is None and b.sku is None
    assert a.ml_item_id is None and b.ml_item_id is None


def test_name_is_required(service):
    with pytest.raises(InventoryError, match="nombre"):
        service.create_product(ProductData(name="   "))


def test_update_product(service):
    product = _product(service, stock=3)

    updated = service.update_product(
        product.id, ProductData(name="Funda iPhone 16", price=Decimal("219.00"), min_stock=5)
    )

    assert updated.name == "Funda iPhone 16"
    assert updated.price == Decimal("219.00")
    assert updated.stock == 3
    assert updated.is_low_stock


def test_inactive_products_are_hidden_by_default(service):
    product = _product(service)
    service.set_active(product.id, False)

    assert service.list_products() == []
    assert [p.id for p in service.list_products(include_inactive=True)] == [product.id]


def test_summary(service):
    _product(service, name="A", stock=10, min_stock=2)  # 10 x 50 costo, 10 x 199 precio
    _product(service, name="B", stock=1, min_stock=3)  # stock bajo
    inactive = _product(service, name="C", stock=100)
    service.set_active(inactive.id, False)

    summary = service.summary()

    assert summary.product_count == 2
    assert summary.total_units == 11
    assert summary.value_at_cost == Decimal("550.00")
    assert summary.value_at_price == Decimal("2189.00")
    assert summary.low_stock_count == 1
