"""Utilidades compartidas por las ventanas."""

from collections.abc import Callable
from decimal import Decimal
from typing import TypeVar

from PySide6.QtWidgets import QDialog, QDoubleSpinBox, QMessageBox, QWidget
from sqlalchemy.exc import SQLAlchemyError

from inventario.services import InventoryError

T = TypeVar("T")

MAX_MONEY = 99_999_999.99
MAX_UNITS = 1_000_000


def format_money(value: Decimal | None) -> str:
    return "" if value is None else f"${value:,.2f}"


def money_spinbox(value: Decimal = Decimal("0")) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, MAX_MONEY)
    box.setDecimals(2)
    box.setPrefix("$ ")
    box.setGroupSeparatorShown(True)
    box.setValue(float(value))
    return box


def spinbox_money(box: QDoubleSpinBox) -> Decimal:
    return Decimal(f"{box.value():.2f}")


def run_or_warn(parent: QWidget, action: Callable[[], T]) -> tuple[bool, T | None]:
    """Ejecuta una operación del servicio y muestra un mensaje amigable si falla."""
    try:
        return True, action()
    except InventoryError as exc:
        QMessageBox.warning(parent, "No se pudo completar", str(exc))
    except SQLAlchemyError as exc:
        QMessageBox.critical(parent, "Error de base de datos", str(getattr(exc, "orig", exc)))
    return False, None


class ServiceDialog(QDialog):
    """Diálogo que sólo se cierra si la operación de guardado tuvo éxito.

    Las subclases implementan `_save()`; su valor de retorno queda en `result_value`.
    """

    result_value: object = None

    def _save(self) -> object:
        raise NotImplementedError

    def accept(self) -> None:
        ok, self.result_value = run_or_warn(self, self._save)
        if ok:
            super().accept()
