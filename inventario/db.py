"""Conexión a PostgreSQL y creación del esquema."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def create_db_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def create_session_factory(engine: Engine) -> sessionmaker:
    # expire_on_commit=False permite que la UI siga leyendo los objetos después de cerrar la sesión.
    return sessionmaker(engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Crea las tablas que falten. No modifica tablas existentes."""
    from inventario import models  # noqa: F401  (registra las tablas en Base.metadata)

    Base.metadata.create_all(engine)
