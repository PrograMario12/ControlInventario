"""Lógica de negocio del inventario. La interfaz gráfica sólo habla con este módulo."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, sessionmaker

from inventario.models import MovementType, Product, StockMovement


class InventoryError(Exception):
    """Error de negocio con un mensaje apto para mostrarse al usuario."""


@dataclass
class ProductData:
    name: str
    sku: str | None = None
    ml_item_id: str | None = None
    cost: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    min_stock: int = 0
    notes: str | None = None


@dataclass
class InventorySummary:
    product_count: int
    total_units: int
    value_at_cost: Decimal
    value_at_price: Decimal
    low_stock_count: int


# Sentido en que cada tipo de movimiento afecta al stock (los ajustes se calculan aparte).
_DIRECTION = {
    MovementType.ENTRADA: 1,
    MovementType.DEVOLUCION: 1,
    MovementType.VENTA: -1,
}


class InventoryService:
    def __init__(self, session_factory: sessionmaker):
        self._sessions = session_factory

    # ----- Productos -----

    def list_products(self, include_inactive: bool = False) -> list[Product]:
        with self._sessions() as session:
            stmt = select(Product).order_by(Product.name)
            if not include_inactive:
                stmt = stmt.where(Product.active.is_(True))
            return list(session.scalars(stmt))

    def get_product(self, product_id: int) -> Product:
        with self._sessions() as session:
            product = session.get(Product, product_id)
            if product is None:
                raise InventoryError("El producto no existe.")
            return product

    def create_product(self, data: ProductData, initial_stock: int = 0) -> Product:
        if initial_stock < 0:
            raise InventoryError("El stock inicial no puede ser negativo.")
        product = Product(stock=0, active=True)
        _apply_product_data(product, data)
        try:
            with self._sessions.begin() as session:
                session.add(product)
                session.flush()
                if initial_stock:
                    _record_movement(
                        session, product, MovementType.ENTRADA, initial_stock,
                        unit_cost=product.cost, note="Stock inicial",
                    )
        except IntegrityError as exc:
            raise _duplicate_error(exc) from exc
        return product

    def update_product(self, product_id: int, data: ProductData) -> Product:
        try:
            with self._sessions.begin() as session:
                product = _lock_product(session, product_id)
                _apply_product_data(product, data)
        except IntegrityError as exc:
            raise _duplicate_error(exc) from exc
        return product

    def set_active(self, product_id: int, active: bool) -> Product:
        with self._sessions.begin() as session:
            product = _lock_product(session, product_id)
            product.active = active
        return product

    # ----- Movimientos de stock -----

    def register_movement(
        self,
        product_id: int,
        kind: MovementType,
        quantity: int,
        unit_price: Decimal | None = None,
        unit_cost: Decimal | None = None,
        note: str | None = None,
    ) -> StockMovement:
        """Registra una entrada, venta o devolución. `quantity` siempre es positiva."""
        if kind not in _DIRECTION:
            raise ValueError(f"Usa adjust_stock() para movimientos de tipo {kind.value}.")
        if quantity <= 0:
            raise InventoryError("La cantidad debe ser mayor que cero.")
        with self._sessions.begin() as session:
            product = _lock_product(session, product_id)
            if unit_cost is None:
                unit_cost = product.cost
            if unit_price is None and kind is not MovementType.ENTRADA:
                unit_price = product.price
            return _record_movement(
                session, product, kind, _DIRECTION[kind] * quantity,
                unit_price=unit_price, unit_cost=unit_cost, note=note,
            )

    def adjust_stock(self, product_id: int, counted: int, note: str | None = None) -> StockMovement | None:
        """Fija el stock al valor contado físicamente. Devuelve None si no había diferencia."""
        if counted < 0:
            raise InventoryError("El stock contado no puede ser negativo.")
        with self._sessions.begin() as session:
            product = _lock_product(session, product_id)
            delta = counted - product.stock
            if delta == 0:
                return None
            return _record_movement(
                session, product, MovementType.AJUSTE, delta, unit_cost=product.cost, note=note,
            )

    def list_movements(self, product_id: int | None = None, limit: int = 1000) -> list[StockMovement]:
        with self._sessions() as session:
            stmt = (
                select(StockMovement)
                .options(joinedload(StockMovement.product))
                .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
                .limit(limit)
            )
            if product_id is not None:
                stmt = stmt.where(StockMovement.product_id == product_id)
            return list(session.scalars(stmt))

    # ----- Resumen -----

    def summary(self) -> InventorySummary:
        with self._sessions() as session:
            row = session.execute(
                select(
                    func.count(Product.id),
                    func.coalesce(func.sum(Product.stock), 0),
                    func.coalesce(func.sum(Product.stock * Product.cost), 0),
                    func.coalesce(func.sum(Product.stock * Product.price), 0),
                    func.count(Product.id).filter(Product.stock <= Product.min_stock),
                ).where(Product.active.is_(True))
            ).one()
        count, units, value_at_cost, value_at_price, low = row
        return InventorySummary(int(count), int(units), Decimal(value_at_cost), Decimal(value_at_price), int(low))


def _lock_product(session: Session, product_id: int) -> Product:
    # FOR UPDATE evita que dos movimientos simultáneos lean el mismo stock y se pisen.
    product = session.scalars(
        select(Product).where(Product.id == product_id).with_for_update()
    ).one_or_none()
    if product is None:
        raise InventoryError("El producto no existe.")
    return product


def _record_movement(
    session: Session,
    product: Product,
    kind: MovementType,
    delta: int,
    unit_price: Decimal | None = None,
    unit_cost: Decimal | None = None,
    note: str | None = None,
) -> StockMovement:
    new_stock = product.stock + delta
    if new_stock < 0:
        raise InventoryError(
            f"Stock insuficiente de «{product.name}»: hay {product.stock} y se intentó sacar {-delta}."
        )
    product.stock = new_stock
    movement = StockMovement(
        product=product,
        kind=kind,
        quantity=delta,
        stock_after=new_stock,
        unit_price=unit_price,
        unit_cost=unit_cost,
        note=_clean(note),
    )
    session.add(movement)
    session.flush()
    return movement


def _apply_product_data(product: Product, data: ProductData) -> None:
    name = data.name.strip()
    if not name:
        raise InventoryError("El nombre del producto es obligatorio.")
    if data.cost < 0 or data.price < 0:
        raise InventoryError("El costo y el precio no pueden ser negativos.")
    if data.min_stock < 0:
        raise InventoryError("El stock mínimo no puede ser negativo.")
    ml_item_id = _clean(data.ml_item_id)
    product.name = name
    product.sku = _clean(data.sku)
    product.ml_item_id = ml_item_id.upper() if ml_item_id else None
    product.cost = data.cost
    product.price = data.price
    product.min_stock = data.min_stock
    product.notes = _clean(data.notes)


def _clean(text: str | None) -> str | None:
    """Quita espacios y convierte el texto vacío en None (para no chocar con los UNIQUE)."""
    if text is None:
        return None
    text = text.strip()
    return text or None


def _duplicate_error(exc: IntegrityError) -> InventoryError:
    detail = str(exc.orig)
    if "products_sku_key" in detail:
        return InventoryError("Ya existe otro producto con ese SKU.")
    if "products_ml_item_id_key" in detail:
        return InventoryError("Ya existe otro producto con ese ID de publicación de Mercado Libre.")
    return InventoryError(f"No se pudo guardar el producto: {detail}")
