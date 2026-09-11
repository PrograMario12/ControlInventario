"""Tablas de la base de datos.

- products: catálogo con el stock actual de cada producto.
- stock_movements: historial de cada entrada, venta, devolución o ajuste. El stock de un
  producto siempre es la suma de sus movimientos; guardar el precio y el costo de cada
  movimiento es lo que después permitirá hacer analítica (más vendidos, márgenes, rotación).
"""

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from inventario.db import Base

Money = Numeric(12, 2)


class MovementType(enum.Enum):
    ENTRADA = "entrada"  # compra o reabastecimiento
    VENTA = "venta"
    DEVOLUCION = "devolucion"  # el cliente regresó el producto
    AJUSTE = "ajuste"  # corrección por conteo físico, merma, daño, etc.

    @property
    def label(self) -> str:
        return _MOVEMENT_LABELS[self]


_MOVEMENT_LABELS = {
    MovementType.ENTRADA: "Entrada",
    MovementType.VENTA: "Venta",
    MovementType.DEVOLUCION: "Devolución",
    MovementType.AJUSTE: "Ajuste",
}


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("stock >= 0", name="ck_products_stock_no_negativo"),
        CheckConstraint("min_stock >= 0", name="ck_products_min_stock_no_negativo"),
        CheckConstraint("cost >= 0 AND price >= 0", name="ck_products_precios_no_negativos"),
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str | None] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    ml_item_id: Mapped[str | None] = mapped_column(String(32), unique=True)  # ej. MLM1234567890
    cost: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    stock: Mapped[int] = mapped_column(default=0)
    min_stock: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    movements: Mapped[list["StockMovement"]] = relationship(back_populates="product")

    @property
    def is_low_stock(self) -> bool:
        return self.stock <= self.min_stock


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (CheckConstraint("quantity <> 0", name="ck_movements_cantidad_no_cero"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[MovementType] = mapped_column(
        Enum(MovementType, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e])
    )
    quantity: Mapped[int]  # con signo: positivo entra al inventario, negativo sale
    stock_after: Mapped[int]  # stock resultante, útil para auditar
    unit_price: Mapped[Decimal | None] = mapped_column(Money)  # precio de venta (ventas y devoluciones)
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)  # costo unitario al momento del movimiento
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    product: Mapped[Product] = relationship(back_populates="movements")
