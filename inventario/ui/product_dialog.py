"""Alta y edición de productos."""

from collections.abc import Callable

from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from inventario.models import Product
from inventario.services import ProductData
from inventario.ui.common import MAX_UNITS, ServiceDialog, format_money, money_spinbox, spinbox_money


class ProductDialog(ServiceDialog):
    def __init__(
        self,
        parent: QWidget,
        on_save: Callable[[ProductData, int], Product],
        product: Product | None = None,
    ):
        super().__init__(parent)
        self._on_save = on_save
        is_new = product is None
        self.setWindowTitle("Nuevo producto" if is_new else "Editar producto")
        self.setMinimumWidth(460)

        self._name = QLineEdit(product.name if product else "")
        self._sku = QLineEdit(product.sku or "" if product else "")
        self._sku.setPlaceholderText("Código interno (opcional)")
        self._ml_item_id = QLineEdit(product.ml_item_id or "" if product else "")
        self._ml_item_id.setPlaceholderText("Ej. MLM1234567890 (opcional)")
        self._cost = money_spinbox(product.cost if product else 0)
        self._price = money_spinbox(product.price if product else 0)
        self._margin = QLabel()
        self._margin.setWordWrap(True)
        self._min_stock = QSpinBox()
        self._min_stock.setRange(0, MAX_UNITS)
        self._min_stock.setValue(product.min_stock if product else 0)
        self._min_stock.setToolTip("Cuando el stock llegue a este número el producto se marcará como stock bajo.")
        self._initial_stock = QSpinBox()
        self._initial_stock.setRange(0, MAX_UNITS)
        self._notes = QPlainTextEdit(product.notes or "" if product else "")
        self._notes.setFixedHeight(70)

        form = QFormLayout()
        form.addRow("Nombre *", self._name)
        form.addRow("SKU", self._sku)
        form.addRow("Publicación ML", self._ml_item_id)
        form.addRow("Costo unitario", self._cost)
        form.addRow("Precio de venta", self._price)
        form.addRow("", self._margin)
        form.addRow("Stock mínimo", self._min_stock)
        if is_new:
            form.addRow("Stock inicial", self._initial_stock)
        else:
            form.addRow("Stock actual", QLabel(f"{product.stock}  (usa Entrada / Venta / Ajuste para cambiarlo)"))
        form.addRow("Notas", self._notes)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._cost.valueChanged.connect(self._update_margin)
        self._price.valueChanged.connect(self._update_margin)
        self._update_margin()
        self._name.setFocus()

    def _update_margin(self) -> None:
        cost, price = spinbox_money(self._cost), spinbox_money(self._price)
        margin = price - cost
        percent = f" ({margin / price:.0%} del precio)" if price else ""
        self._margin.setText(f"Ganancia por unidad: {format_money(margin)}{percent}, antes de comisión y envío de ML")

    def _save(self) -> Product:
        data = ProductData(
            name=self._name.text(),
            sku=self._sku.text(),
            ml_item_id=self._ml_item_id.text(),
            cost=spinbox_money(self._cost),
            price=spinbox_money(self._price),
            min_stock=self._min_stock.value(),
            notes=self._notes.toPlainText(),
        )
        return self._on_save(data, self._initial_stock.value())
