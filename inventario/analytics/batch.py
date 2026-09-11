"""Orquestación del batch: una conexión, un snapshot de sólo lectura y lectura por lotes acotados.

Garantías de consumo en el servidor:
- Pool acotado a UNA conexión (`pool_size=1, max_overflow=0`), destruido al terminar (`batch_engine`).
- Una sola transacción REPEATABLE READ READ ONLY: todos los lotes ven el mismo snapshot, el servidor
  no toma locks de escritura y cualquier escritura accidental falla.
- Tiempos máximos en el servidor (`SET LOCAL`): por consulta y por inactividad dentro de la transacción,
  así una ejecución colgada no retiene recursos ni frena el VACUUM.
- En memoria de Python sólo vive un lote a la vez; el reporte es de tamaño acotado.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, insert, text
from sqlalchemy.engine import RowMapping

from inventario.analytics.metrics import AnalyticsParams, InventoryReportBuilder, ProductSnapshot
from inventario.analytics.sql import (
    MISSING_INDEXES_QUERY,
    PRODUCT_CHUNK_QUERY,
    RECOMMENDED_INDEXES,
    index_ddl,
)
from inventario.models import AnalyticsRun

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalyticsResult:
    report: dict[str, Any]
    as_of: datetime
    started_at: datetime
    finished_at: datetime


@contextmanager
def batch_engine(url: str) -> Iterator[Engine]:
    """Engine con pool de UNA conexión; al salir se cierran todas sus conexiones."""
    engine = create_engine(
        url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=10,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "application_name": "inventario-analytics"},
    )
    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def read_only_snapshot(conn: Connection, statement_timeout_ms: int) -> Iterator[datetime]:
    """Abre una transacción REPEATABLE READ READ ONLY y devuelve el instante del snapshot (now())."""
    with conn.begin():
        # Debe ser la primera sentencia de la transacción.
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        conn.execute(
            text("SELECT set_config('statement_timeout', :ms, true), "
                 "set_config('idle_in_transaction_session_timeout', :idle, true)"),
            {"ms": f"{statement_timeout_ms}ms", "idle": f"{statement_timeout_ms * 2}ms"},
        )
        yield conn.execute(text("SELECT now()")).scalar_one()


def iter_product_chunks(
    conn: Connection, as_of: datetime, window_start: datetime, chunk_size: int
) -> Iterator[list[RowMapping]]:
    """Recorre el catálogo por llave primaria, `chunk_size` productos por consulta."""
    after_id = 0
    while True:
        rows = conn.execute(PRODUCT_CHUNK_QUERY, {
            "as_of": as_of,
            "window_start": window_start,
            "after_id": after_id,
            "chunk_size": chunk_size,
        }).mappings().all()
        if not rows:
            return
        yield rows
        if len(rows) < chunk_size:
            return
        after_id = rows[-1]["id"]


def run_inventory_analytics(
    engine: Engine, params: AnalyticsParams, as_of: datetime | None = None
) -> AnalyticsResult:
    """Ejecuta el batch completo. `as_of` fija la fecha de corte (por defecto, el instante del snapshot)."""
    if as_of is not None and as_of.tzinfo is None:
        raise ValueError("as_of debe incluir zona horaria.")
    started_at = datetime.now(UTC)
    clock = time.perf_counter()
    chunks = 0

    with engine.connect() as conn, read_only_snapshot(conn, params.statement_timeout_ms) as snapshot_at:
        cutoff = as_of or snapshot_at
        window_start = cutoff - timedelta(days=params.sales_window_days)
        builder = InventoryReportBuilder(cutoff, params)
        for rows in iter_product_chunks(conn, cutoff, window_start, params.chunk_size):
            chunks += 1
            for row in rows:
                builder.add(ProductSnapshot.from_row(row))
            log.info("Lote %d: %d productos (acumulado %d).", chunks, len(rows), builder.products_scanned)
    # Aquí la transacción ya terminó y la conexión volvió al pool.

    finished_at = datetime.now(UTC)
    report = builder.build()
    report["run"] = {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": round((time.perf_counter() - clock) * 1000),
        "chunks": chunks,
        "snapshot_at": snapshot_at.isoformat(),
    }
    return AnalyticsResult(report, cutoff, started_at, finished_at)


def persist_report(engine: Engine, result: AnalyticsResult) -> int:
    """Guarda el reporte en inventory_analytics_runs (una fila por ejecución) y devuelve su id."""
    with engine.begin() as conn:
        AnalyticsRun.__table__.create(conn, checkfirst=True)
        return conn.execute(
            insert(AnalyticsRun)
            .values(as_of=result.as_of, started_at=result.started_at, finished_at=result.finished_at,
                    report=result.report)
            .returning(AnalyticsRun.id)
        ).scalar_one()


def missing_indexes(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        names = [index.name for index in RECOMMENDED_INDEXES]
        return sorted(conn.execute(MISSING_INDEXES_QUERY, {"names": names}).scalars())


def apply_indexes(engine: Engine) -> None:
    """Crea los índices recomendados con CONCURRENTLY (no bloquea las ventas mientras se construyen)."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for index in RECOMMENDED_INDEXES:
            log.info("Creando índice %s (si no existe)…", index.name)
            conn.execute(text(index_ddl(index)))
