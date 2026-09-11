"""Diálogos para registrar entradas, ventas, devoluciones y ajustes de stock."""

import html
from collections.abc import Callable
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from inventario.models import MovementType, Product, StockMovement
from inventario.ui.common import MAX_UNITS, ServiceDialog, format_money, money_spinbox, spinbox_money

_TITLES = {
    MovementType.ENTRADA: "Registrar entrada de mercancía",
    MovementType.VENTA: "Registrar venta",
    MovementType.DEVOLUCION: "Registrar devolución",
}

_NOTE_HINTS = {
    MovementType.ENTRADA: "Ej. proveedor, número de factura",
    MovementType.VENTA: "Ej. número de venta de Mercado Libre",
    MovementType.DEVOLUCION: "Ej. número de venta y motivo",
}


def _header(product: Product) -> QLabel:
    label = QLabel(f"<b>{html.escape(product.name)}</b><br>Stock actual: {product.stock}")
    label.setTextFormat(Qt.TextFormat.RichText)
    return label


def _buttons(dialog: ServiceDialog) -> QDialogButtonBox:
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons


class MovementDialog(ServiceDialog):
    """Entrada, venta o devolución. `on_save(cantidad, monto_unitario, nota)`."""

    def __init__(
        self,
        parent: QWidget,
        product: Product,
        kind: MovementType,
        on_save: Callable[[int, Decimal, str], StockMovement],
    ):
        super().__init__(parent)
        self._product = product
        self._kind = kind
        self._on_save = on_save
        self._sign = -1 if kind is MovementType.VENTA else 1
        self.setWindowTitle(_TITLES[kind])
        self.setMinimumWidth(420)

        self._quantity = QSpinBox()
        self._quantity.setRange(1, product.stock if kind is MovementType.VENTA else MAX_UNITS)
        self._quantity.setValue(1)

        is_purchase = kind is MovementType.ENTRADA
        self._amount = money_spinbox(product.cost if is_purchase else product.price)
        self._total = QLabel()
        self._stock_after = QLabel()
        self._note = QLineEdit()
        self._note.setPlaceholderText(_NOTE_HINTS[kind])

        form = QFormLayout()
        form.addRow("Cantidad", self._quantity)
        form.addRow("Costo unitario" if is_purchase else "Precio unitario", self._amount)
        form.addRow("Total", self._total)
        form.addRow("Stock después", self._stock_after)
        form.addRow("Nota", self._note)

        layout = QVBoxLayout(self)
        layout.addWidget(_header(product))
        layout.addLayout(form)
        layout.addWidget(_buttons(self))

        self._quantity.valueChanged.connect(self._update_preview)
        self._amount.valueChanged.connect(self._update_preview)
        self._update_preview()
        self._quantity.selectAll()
        self._quantity.setFocus()

    def _update_preview(self) -> None:
        quantity = self._quantity.value()
        self._total.setText(format_money(spinbox_money(self._amount) * quantity))
        self._stock_after.setText(str(self._product.stock + self._sign * quantity))

    def _save(self) -> StockMovement:
        return self._on_save(self._quantity.value(), spinbox_money(self._amount), self._note.text())


class AdjustDialog(ServiceDialog):
    """Ajuste a conteo físico. `on_save(stock_contado, nota)`."""

    def __init__(
        self,
        parent: QWidget,
        product: Product,
        on_save: Callable[[int, str], StockMovement | None],
    ):
        super().__init__(parent)
        self._product = product
        self._on_save = on_save
        self.setWindowTitle("Ajustar stock")
        self.setMinimumWidth(420)

        self._counted = QSpinBox()
        self._counted.setRange(0, MAX_UNITS)
        self._counted.setValue(product.stock)
        self._difference = QLabel()
        self._note = QLineEdit()
        self._note.setPlaceholderText("Motivo: conteo físico, merma, producto dañado…")

        form = QFormLayout()
        form.addRow("Stock contado", self._counted)
        form.addRow("Diferencia", self._difference)
        form.addRow("Motivo", self._note)

        layout = QVBoxLayout(self)
        layout.addWidget(_header(product))
        layout.addLayout(form)
        layout.addWidget(_buttons(self))

        self._counted.valueChanged.connect(self._update_difference)
        self._update_difference()
        self._counted.selectAll()
        self._counted.setFocus()

    def _update_difference(self) -> None:
        self._difference.setText(f"{self._counted.value() - self._product.stock:+d}")

    def _save(self) -> StockMovement | None:
        return self._on_save(self._counted.value(), self._note.text())
