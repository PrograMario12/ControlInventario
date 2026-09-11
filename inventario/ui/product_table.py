"""Modelo de tabla de productos y filtro de búsqueda."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from inventario.models import Product
from inventario.ui.common import format_money

SORT_ROLE = Qt.ItemDataRole.UserRole

_OUT_OF_STOCK_BG = QColor("#f8b4b4")
_LOW_STOCK_BG = QColor("#fde68a")
_HIGHLIGHT_FG = QColor("#1f2937")
_INACTIVE_FG = QColor("#9ca3af")

_Index = QModelIndex | QPersistentModelIndex
_ROOT = QModelIndex()


@dataclass(frozen=True)
class _Column:
    header: str
    value: Callable[[Product], object]  # valor crudo, usado también para ordenar
    fmt: Callable[[object], str] = str
    numeric: bool = False


COLUMNS = (
    _Column("SKU", lambda p: p.sku or ""),
    _Column("Producto", lambda p: p.name),
    _Column("Publicación ML", lambda p: p.ml_item_id or ""),
    _Column("Stock", lambda p: p.stock, numeric=True),
    _Column("Stock mínimo", lambda p: p.min_stock, numeric=True),
    _Column("Costo", lambda p: p.cost, format_money, numeric=True),
    _Column("Precio", lambda p: p.price, format_money, numeric=True),
    _Column("Valor en stock", lambda p: p.stock * p.cost, format_money, numeric=True),
)
NAME_COLUMN = 1


class ProductTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._products: list[Product] = []

    def set_products(self, products: list[Product]) -> None:
        self.beginResetModel()
        self._products = list(products)
        self.endResetModel()

    def product_at(self, row: int) -> Product:
        return self._products[row]

    def row_of(self, product_id: int) -> int | None:
        return next((i for i, p in enumerate(self._products) if p.id == product_id), None)

    def rowCount(self, parent: _Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(self._products)

    def columnCount(self, parent: _Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section].header
        return None

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        product = self._products[index.row()]
        column = COLUMNS[index.column()]

        if role == Qt.ItemDataRole.DisplayRole:
            return column.fmt(column.value(product))
        if role == SORT_ROLE:
            value = column.value(product)
            return float(value) if isinstance(value, Decimal) else value
        if role == Qt.ItemDataRole.TextAlignmentRole and column.numeric:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.BackgroundRole and product.active:
            if product.stock == 0:
                return _OUT_OF_STOCK_BG
            if product.is_low_stock:
                return _LOW_STOCK_BG
        if role == Qt.ItemDataRole.ForegroundRole:
            if not product.active:
                return _INACTIVE_FG
            if product.is_low_stock:
                return _HIGHLIGHT_FG
        if role == Qt.ItemDataRole.ToolTipRole:
            if not product.active:
                return "Producto inactivo"
            if product.stock == 0:
                return "Sin stock"
            if product.is_low_stock:
                return "Stock bajo: conviene reabastecer"
        return None


class ProductFilterProxy(QSortFilterProxyModel):
    """Búsqueda de texto en todas las columnas + filtro opcional de "sólo stock bajo"."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._low_stock_only = False
        self.setSortRole(SORT_ROLE)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(-1)

    def set_low_stock_only(self, enabled: bool) -> None:
        self.beginFilterChange()
        self._low_stock_only = enabled
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, source_row: int, source_parent: _Index) -> bool:
        if self._low_stock_only:
            product = self.sourceModel().product_at(source_row)
            if not (product.active and product.is_low_stock):
                return False
        return super().filterAcceptsRow(source_row, source_parent)
