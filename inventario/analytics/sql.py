"""SQL del batch de analítica.

Cada lote es una consulta corta y paginada por llave (`p.id > :after_id ORDER BY p.id LIMIT n`):
recorre la llave primaria sin OFFSET, así que cada lote cuesta lo mismo sin importar en qué parte del
catálogo esté. Por cada producto, tres subconsultas LATERAL resuelven sus movimientos con índices
(ver models.py), de modo que PostgreSQL devuelve UNA fila por producto en lugar de enviar el historial
de movimientos por la red.
"""

from sqlalchemy import Index, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from inventario.models import MovementType, StockMovement

# Literal (no parámetro) a propósito: así el planificador puede usar el índice parcial
# ix_movements_product_sales incluso con planes genéricos de sentencias preparadas.
_SALE = MovementType.VENTA.value
_RETURN = MovementType.DEVOLUCION.value

PRODUCT_CHUNK_QUERY = text(f"""
SELECT
    p.id, p.sku, p.name, p.ml_item_id, p.cost, p.price, p.stock, p.min_stock, p.active, p.created_at,
    last_mov.stock_after             AS ledger_stock,
    last_mov.created_at              AS last_movement_at,
    last_sale.created_at             AS last_sale_at,
    COALESCE(win.units_sold, 0)      AS units_sold_window,
    COALESCE(win.units_returned, 0)  AS units_returned_window
FROM products AS p
LEFT JOIN LATERAL (
    -- Último movimiento (sin fecha de corte: se compara contra el stock actual).
    SELECT sm.stock_after, sm.created_at
    FROM stock_movements AS sm
    WHERE sm.product_id = p.id
    ORDER BY sm.created_at DESC, sm.id DESC
    LIMIT 1
) AS last_mov ON TRUE
LEFT JOIN LATERAL (
    SELECT sm.created_at
    FROM stock_movements AS sm
    WHERE sm.product_id = p.id AND sm.kind = '{_SALE}' AND sm.created_at <= :as_of
    ORDER BY sm.created_at DESC
    LIMIT 1
) AS last_sale ON TRUE
LEFT JOIN LATERAL (
    -- Sólo recorre los movimientos dentro de la ventana (rango del índice compuesto).
    SELECT
        -SUM(sm.quantity) FILTER (WHERE sm.kind = '{_SALE}')   AS units_sold,
        SUM(sm.quantity)  FILTER (WHERE sm.kind = '{_RETURN}') AS units_returned
    FROM stock_movements AS sm
    WHERE sm.product_id = p.id AND sm.created_at > :window_start AND sm.created_at <= :as_of
) AS win ON TRUE
WHERE p.id > :after_id
ORDER BY p.id
LIMIT :chunk_size
""")

RECOMMENDED_INDEXES: tuple[Index, ...] = tuple(
    index for index in StockMovement.__table__.indexes
    if index.name in {"ix_movements_product_created", "ix_movements_product_sales"}
)

MISSING_INDEXES_QUERY = text(
    "SELECT unnest(CAST(:names AS text[])) EXCEPT SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
)


def index_ddl(index: Index) -> str:
    """DDL para crear el índice sin bloquear escrituras (CONCURRENTLY) y de forma idempotente."""
    ddl = str(CreateIndex(index, if_not_exists=True).compile(dialect=postgresql.dialect()))
    return ddl.replace("CREATE INDEX ", "CREATE INDEX CONCURRENTLY ", 1)
