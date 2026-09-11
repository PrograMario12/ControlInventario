"""Cálculo determinista de las métricas de inventario (sin acceso a la base de datos).

PostgreSQL entrega un `ProductSnapshot` por producto con los movimientos ya agregados; este módulo
lo clasifica y acumula el reporte. Toda la aritmética usa `Decimal` y enteros (nada de float), y la
memoria está acotada: contadores y totales son exactos, pero cada lista de detalle guarda como
máximo `max_items` productos, sin importar el tamaño del catálogo.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

SCHEMA_VERSION = 1

_MONEY = Decimal("0.01")
_ONE_DECIMAL = Decimal("0.1")
_ZERO = Decimal("0")

# Estados de rotación.
DEAD = "dead"  # sin ventas en `dead_stock_days` (capital inmovilizado)
SLOW = "slow"  # vende, pero el stock alcanza para más de `slow_coverage_days`
NO_HISTORY = "no_history"  # producto nuevo: todavía no hay historia suficiente para juzgarlo
HEALTHY = "healthy"

# Estados de stock.
OUT_OF_STOCK = "out_of_stock"
CRITICAL = "critical"


@dataclass(frozen=True)
class AnalyticsParams:
    """Parámetros del batch. Todos tienen límites para no disparar consultas desproporcionadas."""

    chunk_size: int = 500  # productos por consulta
    sales_window_days: int = 30  # ventana para medir el ritmo de venta
    dead_stock_days: int = 90  # días sin ventas para considerar un producto estancado
    slow_coverage_days: int = 60  # cobertura (días de stock) a partir de la cual es de lenta rotación
    max_items: int = 50  # productos listados por sección del reporte
    min_name_length: int = 5  # nombres más cortos se reportan como incompletos
    statement_timeout_ms: int = 30_000  # tope por consulta en el servidor

    def __post_init__(self) -> None:
        limits = {
            "chunk_size": (1, 10_000),
            "sales_window_days": (1, 3_650),
            "dead_stock_days": (1, 3_650),
            "slow_coverage_days": (1, 3_650),
            "max_items": (0, 1_000),
            "min_name_length": (0, 200),
            "statement_timeout_ms": (1_000, 600_000),
        }
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if not low <= value <= high:
                raise ValueError(f"{name} debe estar entre {low} y {high} (recibido: {value}).")


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    """Una fila del lote: el producto más sus movimientos agregados por PostgreSQL."""

    id: int
    sku: str | None
    name: str
    ml_item_id: str | None
    cost: Decimal
    price: Decimal
    stock: int
    min_stock: int
    active: bool
    created_at: datetime
    ledger_stock: int | None  # stock_after del último movimiento; None si nunca tuvo movimientos
    last_movement_at: datetime | None
    last_sale_at: datetime | None
    units_sold_window: int
    units_returned_window: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ProductSnapshot:
        return cls(**{f.name: row[f.name] for f in fields(cls)})

    @property
    def cost_value(self) -> Decimal:
        return self.cost * self.stock

    @property
    def retail_value(self) -> Decimal:
        return self.price * self.stock

    @property
    def net_units_window(self) -> int:
        """Unidades vendidas en la ventana menos devoluciones (nunca negativo)."""
        return max(self.units_sold_window - self.units_returned_window, 0)


# ----- Reglas de cálculo -----


def days_between(start: datetime, end: datetime) -> int:
    """Días completos transcurridos entre dos instantes (redondeo hacia abajo)."""
    return (end - start) // timedelta(days=1)


def coverage_days(stock: int, net_units: int, window_days: int) -> Decimal | None:
    """Días que alcanzaría el stock al ritmo de venta neto de la ventana; None si no hubo ventas."""
    if net_units <= 0:
        return None
    return (Decimal(stock) * window_days / Decimal(net_units)).quantize(_ONE_DECIMAL, ROUND_HALF_UP)


def classify_stock(p: ProductSnapshot) -> str | None:
    """Agotado o crítico (mismo criterio que la app: stock <= stock mínimo). Sólo productos activos."""
    if not p.active:
        return None
    if p.stock == 0:
        return OUT_OF_STOCK
    if p.stock <= p.min_stock:
        return CRITICAL
    return None


def classify_movement(p: ProductSnapshot, as_of: datetime, params: AnalyticsParams) -> str | None:
    """Rotación de productos activos con stock. None si no aplica (inactivo o sin unidades)."""
    if not p.active or p.stock <= 0:
        return None
    if p.last_sale_at is None:
        return DEAD if days_between(p.created_at, as_of) >= params.dead_stock_days else NO_HISTORY
    if days_between(p.last_sale_at, as_of) >= params.dead_stock_days:
        return DEAD
    coverage = coverage_days(p.stock, p.net_units_window, params.sales_window_days)
    if coverage is None or coverage > params.slow_coverage_days:
        return SLOW
    return HEALTHY


@dataclass(frozen=True)
class AnomalyRule:
    code: str
    description: str
    check: Callable[[ProductSnapshot, AnalyticsParams], bool]


ANOMALY_RULES: tuple[AnomalyRule, ...] = (
    AnomalyRule("price_zero", "Producto activo con precio de venta en cero.",
                lambda p, _: p.active and p.price == 0),
    AnomalyRule("cost_zero", "Producto activo con costo en cero: su valorización queda subestimada.",
                lambda p, _: p.active and p.cost == 0),
    AnomalyRule("negative_margin", "Precio de venta menor al costo: cada venta pierde dinero.",
                lambda p, _: p.active and 0 < p.price < p.cost),
    AnomalyRule("missing_sku", "Producto activo sin SKU.",
                lambda p, _: p.active and p.sku is None),
    AnomalyRule("missing_ml_listing", "Producto activo sin ID de publicación de Mercado Libre.",
                lambda p, _: p.active and p.ml_item_id is None),
    AnomalyRule("incomplete_name", "Nombre más corto que el mínimo configurado (min_name_length).",
                lambda p, params: len(p.name.strip()) < params.min_name_length),
    AnomalyRule("ledger_mismatch",
                "El stock no coincide con el último movimiento registrado (¿se editó la tabla a mano?).",
                lambda p, _: p.stock != (p.ledger_stock or 0)),
    AnomalyRule("inactive_with_stock", "Producto inactivo que todavía tiene unidades en inventario.",
                lambda p, _: not p.active and p.stock > 0),
)

NOT_APPLICABLE_RULES: tuple[dict[str, str], ...] = (
    {"code": "category_null",
     "reason": "Información no disponible: la tabla products no tiene columna de categoría."},
    {"code": "description_incomplete",
     "reason": "Información no disponible: la tabla products no tiene columna de descripción "
               "(el nombre se evalúa con la regla incomplete_name)."},
)


# ----- Acumulación con memoria acotada -----


class BoundedRanking:
    """Cuenta todos los elementos pero conserva sólo los `limit` primeros según `key` (menor = primero).

    La clave debe terminar en el id del producto para que el orden sea total y el resultado no
    dependa del orden de llegada ni del tamaño de lote.
    """

    def __init__(self, limit: int):
        self._limit = limit
        self._entries: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.count = 0

    def add(self, key: tuple[Any, ...], item: dict[str, Any]) -> None:
        self.count += 1
        if self._limit == 0:
            return
        self._entries.append((key, item))
        if len(self._entries) >= 2 * self._limit:
            self._compact()

    def _compact(self) -> None:
        self._entries.sort(key=lambda entry: entry[0])
        del self._entries[self._limit:]

    def to_dict(self) -> dict[str, Any]:
        self._compact()
        return {
            "count": self.count,
            "truncated": self.count > len(self._entries),
            "items": [item for _, item in self._entries],
        }


def _money(value: Decimal) -> str:
    return str(value.quantize(_MONEY, ROUND_HALF_UP))


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _percent(part: Decimal, whole: Decimal) -> str | None:
    if whole == 0:
        return None
    return str((part * 100 / whole).quantize(_ONE_DECIMAL, ROUND_HALF_UP))


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _base_item(p: ProductSnapshot) -> dict[str, Any]:
    return {"id": p.id, "sku": p.sku, "name": p.name, "stock": p.stock}


class InventoryReportBuilder:
    """Recibe los productos lote por lote y arma el reporte consolidado."""

    def __init__(self, as_of: datetime, params: AnalyticsParams):
        self.as_of = as_of
        self.params = params
        limit = params.max_items

        self.products_scanned = 0
        self.active_products = 0
        self.active_with_stock = 0
        self.total_units = 0
        self.cost_value = _ZERO
        self.retail_value = _ZERO
        self.inactive_with_stock = 0
        self.inactive_units = 0
        self.inactive_cost_value = _ZERO

        self.out_of_stock = BoundedRanking(limit)
        self.critical = BoundedRanking(limit)

        self.dead = BoundedRanking(limit)
        self.dead_units = 0
        self.dead_cost_value = _ZERO
        self.slow = BoundedRanking(limit)
        self.slow_units = 0
        self.slow_cost_value = _ZERO
        self.no_history = BoundedRanking(limit)
        self.healthy = 0

        self.anomalies = {rule.code: BoundedRanking(limit) for rule in ANOMALY_RULES}
        self.products_with_anomalies = 0

    def add(self, p: ProductSnapshot) -> None:
        self.products_scanned += 1
        self._add_valuation(p)
        self._add_stock_health(p)
        self._add_movement_health(p)
        self._add_anomalies(p)

    def _add_valuation(self, p: ProductSnapshot) -> None:
        if p.active:
            self.active_products += 1
            self.total_units += p.stock
            self.cost_value += p.cost_value
            self.retail_value += p.retail_value
            if p.stock > 0:
                self.active_with_stock += 1
        elif p.stock > 0:
            self.inactive_with_stock += 1
            self.inactive_units += p.stock
            self.inactive_cost_value += p.cost_value

    def _add_stock_health(self, p: ProductSnapshot) -> None:
        status = classify_stock(p)
        if status is None:
            return
        net_units = p.net_units_window
        item = _base_item(p) | {
            "min_stock": p.min_stock,
            "shortfall_to_min": p.min_stock - p.stock,
            "net_units_sold_window": net_units,
            "last_sale_at": _iso(p.last_sale_at),
        }
        if status == OUT_OF_STOCK:
            # Primero lo que más se estaba vendiendo: es la venta que se está perdiendo.
            self.out_of_stock.add((-net_units, p.id), item)
        else:
            coverage = coverage_days(p.stock, net_units, self.params.sales_window_days)
            item["coverage_days"] = _decimal(coverage)
            # Primero lo que se agota antes; los que no venden van al final.
            self.critical.add((coverage is None, coverage or _ZERO, p.id), item)

    def _add_movement_health(self, p: ProductSnapshot) -> None:
        status = classify_movement(p, self.as_of, self.params)
        if status is None:
            return
        if status == HEALTHY:
            self.healthy += 1
            return
        item = _base_item(p) | {
            "cost_value": _money(p.cost_value),
            "last_sale_at": _iso(p.last_sale_at),
        }
        if status == DEAD:
            since = p.last_sale_at or p.created_at
            item["days_without_sales"] = days_between(since, self.as_of)
            self.dead_units += p.stock
            self.dead_cost_value += p.cost_value
            # Primero el que más capital tiene inmovilizado.
            self.dead.add((-p.cost_value, p.id), item)
        elif status == SLOW:
            coverage = coverage_days(p.stock, p.net_units_window, self.params.sales_window_days)
            item["net_units_sold_window"] = p.net_units_window
            item["coverage_days"] = _decimal(coverage)
            self.slow_units += p.stock
            self.slow_cost_value += p.cost_value
            # Primero los que no vendieron en la ventana, luego la mayor cobertura.
            self.slow.add((coverage is not None, -(coverage or _ZERO), p.id), item)
        else:
            self.no_history.add((p.id,), item)

    def _add_anomalies(self, p: ProductSnapshot) -> None:
        found = False
        for rule in ANOMALY_RULES:
            if rule.check(p, self.params):
                found = True
                self.anomalies[rule.code].add((p.id,), _base_item(p) | {
                    "active": p.active,
                    "price": _money(p.price),
                    "cost": _money(p.cost),
                    "ledger_stock": p.ledger_stock,
                })
        if found:
            self.products_with_anomalies += 1

    def build(self) -> dict[str, Any]:
        margin = self.retail_value - self.cost_value
        rules = {rule.code: {"description": rule.description} | self.anomalies[rule.code].to_dict()
                 for rule in ANOMALY_RULES}
        return {
            "schema_version": SCHEMA_VERSION,
            "as_of": _iso(self.as_of),
            "parameters": asdict(self.params),
            "products_scanned": self.products_scanned,
            "valuation": {
                "active_products": self.active_products,
                "active_products_with_stock": self.active_with_stock,
                "total_units": self.total_units,
                "cost_value": _money(self.cost_value),
                "retail_value": _money(self.retail_value),
                "potential_gross_margin": _money(margin),
                "potential_gross_margin_pct": _percent(margin, self.retail_value),
                "dead_stock_cost_value": _money(self.dead_cost_value),
                "dead_stock_share_pct": _percent(self.dead_cost_value, self.cost_value),
                "inactive_stock": {
                    "products": self.inactive_with_stock,
                    "units": self.inactive_units,
                    "cost_value": _money(self.inactive_cost_value),
                },
            },
            "stock_health": {
                "out_of_stock": self.out_of_stock.to_dict(),
                "critical": self.critical.to_dict(),
            },
            "movement_health": {
                "dead": {"units": self.dead_units, "cost_value": _money(self.dead_cost_value)}
                | self.dead.to_dict(),
                "slow": {"units": self.slow_units, "cost_value": _money(self.slow_cost_value)}
                | self.slow.to_dict(),
                "no_history": self.no_history.to_dict(),
                "healthy_count": self.healthy,
            },
            "catalog_anomalies": {
                "products_with_anomalies": self.products_with_anomalies,
                "rules": rules,
                "not_applicable": list(NOT_APPLICABLE_RULES),
            },
        }
