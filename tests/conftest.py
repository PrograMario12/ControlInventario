import os

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import OperationalError

from inventario import models  # noqa: F401  (registra las tablas)
from inventario.config import normalize_database_url  # importar config carga el archivo .env
from inventario.db import Base, create_db_engine, create_session_factory, init_db
from inventario.services import InventoryService


@pytest.fixture(scope="session")
def engine():
    raw_url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not raw_url:
        pytest.skip("Define TEST_DATABASE_URL (ver .env.example) para correr las pruebas de base de datos.")
    url = make_url(normalize_database_url(raw_url))
    # Las pruebas BORRAN todas las tablas: sólo se permite una base cuyo nombre indique que es de pruebas.
    if "test" not in (url.database or ""):
        pytest.exit(
            f"TEST_DATABASE_URL apunta a la base «{url.database}». Por seguridad su nombre debe contener "
            "'test', porque las pruebas borran todas las tablas.",
            returncode=2,
        )

    engine = create_db_engine(url)
    try:
        with engine.connect():
            pass
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL de pruebas no disponible ({url.render_as_string()}): {exc.orig}")
    Base.metadata.drop_all(engine)
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def service(engine) -> InventoryService:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE stock_movements, products RESTART IDENTITY CASCADE"))
    return InventoryService(create_session_factory(engine))
