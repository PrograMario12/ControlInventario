"""Arranque de la aplicación."""

import sys

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from inventario import config
from inventario.db import create_db_engine, create_session_factory, init_db
from inventario.services import InventoryService
from inventario.ui.main_window import MainWindow


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Control de Inventario")
    app.setStyle("Fusion")

    # Textos estándar de Qt (Cancelar, Cerrar, Sí/No…) en español.
    translator = QTranslator(app)
    if translator.load(QLocale("es"), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)

    try:
        engine = create_db_engine(config.database_url())
        init_db(engine)
    except config.ConfigError as exc:
        QMessageBox.critical(None, "Falta configuración", str(exc))
        return 1
    except SQLAlchemyError as exc:
        QMessageBox.critical(
            None,
            "No se pudo conectar a la base de datos",
            "No fue posible conectarse a PostgreSQL.\n\n"
            "• Si usas una base en la nube (Neon): revisa tu conexión a internet y DATABASE_URL en .env.\n"
            "• Si usas la base local: abre Docker Desktop y ejecuta  docker compose up -d\n\n"
            f"Detalle: {getattr(exc, 'orig', exc)}",
        )
        return 1

    window = MainWindow(InventoryService(create_session_factory(engine)))
    window.show()
    return app.exec()
