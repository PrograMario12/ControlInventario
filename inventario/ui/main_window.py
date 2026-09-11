"""Ventana principal: listado de productos y acciones de inventario."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from inventario.models import MovementType, Product
from inventario.services import InventoryService, InventorySummary
from inventario.ui.common import format_money, run_or_warn
from inventario.ui.history_dialog import HistoryDialog
from inventario.ui.movement_dialogs import AdjustDialog, MovementDialog
from inventario.ui.product_dialog import ProductDialog
from inventario.ui.product_table import NAME_COLUMN, ProductFilterProxy, ProductTableModel


class MainWindow(QMainWindow):
    def __init__(self, service: InventoryService):
        super().__init__()
        self._service = service
        self.setWindowTitle("Control de Inventario — Mercado Libre")
        self.resize(1150, 650)

        self._model = ProductTableModel(self)
        self._proxy = ProductFilterProxy(self)
        self._proxy.setSourceModel(self._model)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(NAME_COLUMN, Qt.SortOrder.AscendingOrder)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
        self._table.doubleClicked.connect(self._edit_product)
        self._table.selectionModel().selectionChanged.connect(self._update_actions)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Buscar por nombre, SKU o publicación de ML…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._proxy.setFilterFixedString)
        self._low_stock_only = QCheckBox("Sólo stock bajo")
        self._low_stock_only.toggled.connect(self._proxy.set_low_stock_only)
        self._show_inactive = QCheckBox("Mostrar inactivos")
        self._show_inactive.toggled.connect(lambda: self.refresh())

        filters = QHBoxLayout()
        filters.addWidget(self._search, stretch=1)
        filters.addWidget(self._low_stock_only)
        filters.addWidget(self._show_inactive)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(filters)
        layout.addWidget(self._table)
        self.setCentralWidget(central)

        self._summary_label = QLabel()
        self.statusBar().addPermanentWidget(self._summary_label, 1)

        self._build_actions()
        self.refresh()

    # ----- Construcción -----

    def _build_actions(self) -> None:
        def action(text: str, slot, shortcut: str | None = None, tip: str | None = None) -> QAction:
            act = QAction(text, self)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            if tip:
                act.setStatusTip(tip)
                act.setToolTip(tip)
            return act

        self._act_new = action("Nuevo producto", self._new_product, "Ctrl+N")
        self._act_edit = action("Editar", self._edit_product, tip="Editar datos del producto (o doble clic)")
        self._act_entry = action("Entrada", lambda: self._register(MovementType.ENTRADA), "Ctrl+E",
                                 "Registrar compra o reabastecimiento")
        self._act_sale = action("Venta", lambda: self._register(MovementType.VENTA), "Ctrl+R",
                                "Registrar una venta")
        self._act_return = action("Devolución", lambda: self._register(MovementType.DEVOLUCION),
                                  tip="Registrar un producto devuelto por el cliente")
        self._act_adjust = action("Ajustar stock", self._adjust, tip="Corregir el stock según un conteo físico")
        self._act_history = action("Historial", self._product_history, "Ctrl+H",
                                   "Movimientos del producto seleccionado")
        self._act_all_history = action("Todos los movimientos", self._all_history)
        self._act_toggle_active = action("Desactivar", self._toggle_active,
                                         tip="Ocultar un producto que ya no vendes (conserva su historial)")
        self._act_refresh = action("Actualizar", lambda: self.refresh(), "F5")
        self._act_search = action("Buscar", lambda: self._search.setFocus(), "Ctrl+F")
        self._act_quit = action("Salir", self.close, "Ctrl+Q")

        self._product_actions = (
            self._act_edit, self._act_entry, self._act_sale, self._act_return,
            self._act_adjust, self._act_history, self._act_toggle_active,
        )

        toolbar = QToolBar("Acciones")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for act in (self._act_new, self._act_edit, None, self._act_entry, self._act_sale,
                    self._act_return, self._act_adjust, None, self._act_history, None, self._act_refresh):
            toolbar.addSeparator() if act is None else toolbar.addAction(act)
        self.addToolBar(toolbar)

        menu = self.menuBar()
        file_menu = menu.addMenu("&Archivo")
        file_menu.addAction(self._act_refresh)
        file_menu.addAction(self._act_quit)
        product_menu = menu.addMenu("&Productos")
        for act in (self._act_new, self._act_edit, self._act_toggle_active, None, self._act_search):
            product_menu.addSeparator() if act is None else product_menu.addAction(act)
        stock_menu = menu.addMenu("&Inventario")
        for act in (self._act_entry, self._act_sale, self._act_return, self._act_adjust, None,
                    self._act_history, self._act_all_history):
            stock_menu.addSeparator() if act is None else stock_menu.addAction(act)
        self.addAction(self._act_search)

    # ----- Datos -----

    def refresh(self, select_id: int | None = None) -> None:
        if select_id is None:
            current = self._selected_product()
            select_id = current.id if current else None
        ok, result = run_or_warn(self, lambda: (
            self._service.list_products(include_inactive=self._show_inactive.isChecked()),
            self._service.summary(),
        ))
        if not ok:
            return
        products, summary = result
        self._model.set_products(products)
        self._select(select_id)
        self._show_summary(summary)
        self._update_actions()

    def _show_summary(self, s: InventorySummary) -> None:
        low = f"<span style='color:#b91c1c'><b>{s.low_stock_count}</b></span>" if s.low_stock_count else "0"
        self._summary_label.setText(
            f"Productos: <b>{s.product_count}</b> &nbsp;|&nbsp; Unidades: <b>{s.total_units:,}</b>"
            f" &nbsp;|&nbsp; Valor a costo: <b>{format_money(s.value_at_cost)}</b>"
            f" &nbsp;|&nbsp; Valor a precio de venta: <b>{format_money(s.value_at_price)}</b>"
            f" &nbsp;|&nbsp; Con stock bajo: {low}"
        )

    def _selected_product(self) -> Product | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._model.product_at(self._proxy.mapToSource(rows[0]).row())

    def _select(self, product_id: int | None) -> None:
        row = self._model.row_of(product_id) if product_id is not None else None
        if row is None:
            self._table.clearSelection()
            return
        index = self._proxy.mapFromSource(self._model.index(row, 0))
        if index.isValid():
            self._table.selectRow(index.row())
            self._table.scrollTo(index)

    def _update_actions(self) -> None:
        product = self._selected_product()
        for act in self._product_actions:
            act.setEnabled(product is not None)
        if product is not None:
            self._act_sale.setEnabled(product.active and product.stock > 0)
            self._act_toggle_active.setText("Desactivar" if product.active else "Reactivar")

    # ----- Acciones -----

    def _new_product(self) -> None:
        dialog = ProductDialog(self, on_save=self._service.create_product)
        if dialog.exec():
            self.refresh(select_id=dialog.result_value.id)

    def _edit_product(self) -> None:
        product = self._selected_product()
        if product is None:
            return
        dialog = ProductDialog(
            self, product=product,
            on_save=lambda data, _initial: self._service.update_product(product.id, data),
        )
        if dialog.exec():
            self.refresh(select_id=product.id)

    def _register(self, kind: MovementType) -> None:
        product = self._selected_product()
        if product is None:
            return
        if kind is MovementType.VENTA and product.stock == 0:
            QMessageBox.information(self, "Sin stock", f"No hay unidades de «{product.name}» para vender.")
            return

        def save(quantity, amount, note):
            if kind is MovementType.ENTRADA:
                return self._service.register_movement(product.id, kind, quantity, unit_cost=amount, note=note)
            return self._service.register_movement(product.id, kind, quantity, unit_price=amount, note=note)

        if MovementDialog(self, product, kind, on_save=save).exec():
            self.refresh(select_id=product.id)

    def _adjust(self) -> None:
        product = self._selected_product()
        if product is None:
            return
        dialog = AdjustDialog(
            self, product,
            on_save=lambda counted, note: self._service.adjust_stock(product.id, counted, note),
        )
        if dialog.exec():
            self.refresh(select_id=product.id)

    def _toggle_active(self) -> None:
        product = self._selected_product()
        if product is None:
            return
        if product.active:
            answer = QMessageBox.question(
                self, "Desactivar producto",
                f"¿Desactivar «{product.name}»?\n\nDejará de aparecer en el listado, pero se conserva "
                "su historial. Puedes reactivarlo con «Mostrar inactivos».",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        ok, _ = run_or_warn(self, lambda: self._service.set_active(product.id, not product.active))
        if ok:
            self.refresh(select_id=product.id)

    def _product_history(self) -> None:
        product = self._selected_product()
        if product is None:
            return
        ok, movements = run_or_warn(self, lambda: self._service.list_movements(product.id))
        if ok:
            HistoryDialog(self, f"Historial — {product.name}", movements).exec()

    def _all_history(self) -> None:
        ok, movements = run_or_warn(self, lambda: self._service.list_movements())
        if ok:
            HistoryDialog(self, "Todos los movimientos (últimos 1000)", movements).exec()
