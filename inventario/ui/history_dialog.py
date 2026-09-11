"""Historial de movimientos de stock."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from inventario.models import StockMovement
from inventario.ui.common import format_money

_HEADERS = ("Fecha", "Producto", "Tipo", "Cantidad", "Stock después", "Precio unit.", "Costo unit.", "Nota")
_NUMERIC = {3, 4, 5, 6}
_IN_FG = QColor("#15803d")
_OUT_FG = QColor("#b91c1c")


class HistoryDialog(QDialog):
    def __init__(self, parent: QWidget, title: str, movements: list[StockMovement]):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 520)

        table = QTableWidget(len(movements), len(_HEADERS))
        table.setHorizontalHeaderLabels(_HEADERS)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)

        for row, movement in enumerate(movements):
            values = (
                movement.created_at.astimezone().strftime("%d/%m/%Y %H:%M"),
                movement.product.name,
                movement.kind.label,
                f"{movement.quantity:+d}",
                str(movement.stock_after),
                format_money(movement.unit_price),
                format_money(movement.unit_cost),
                movement.note or "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in _NUMERIC:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if column == 3:
                    item.setForeground(_IN_FG if movement.quantity > 0 else _OUT_FG)
                table.setItem(row, column, item)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(_HEADERS) - 1, QHeaderView.ResizeMode.Stretch)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        if not movements:
            layout.addWidget(QLabel("Todavía no hay movimientos registrados."))
        layout.addWidget(table)
        layout.addWidget(buttons)
