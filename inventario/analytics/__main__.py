"""Línea de comandos del batch de analítica.

    python -m inventario.analytics                      # reporte JSON en reports/
    python -m inventario.analytics --persist            # además lo guarda en inventory_analytics_runs
    python -m inventario.analytics --apply-indexes      # crea los índices recomendados (una vez)

Códigos de salida: 0 éxito, 1 configuración inválida, 2 error de base de datos.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from inventario import config
from inventario.analytics.batch import (
    AnalyticsResult,
    apply_indexes,
    batch_engine,
    missing_indexes,
    persist_report,
    run_inventory_analytics,
)
from inventario.analytics.metrics import AnalyticsParams

log = logging.getLogger("inventario.analytics")

REPORTS_DIR = config.PROJECT_ROOT / "reports"


def _parse_as_of(value: str) -> datetime:
    """Fecha ISO 8601; si no trae zona horaria se interpreta como hora local."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.astimezone()


def _build_parser() -> argparse.ArgumentParser:
    defaults = AnalyticsParams()
    parser = argparse.ArgumentParser(prog="python -m inventario.analytics",
                                     description="Analítica batch del inventario.")
    parser.add_argument("--chunk-size", type=int, default=defaults.chunk_size,
                        help="productos por consulta (default: %(default)s)")
    parser.add_argument("--window-days", type=int, default=defaults.sales_window_days,
                        help="ventana para medir el ritmo de venta (default: %(default)s)")
    parser.add_argument("--dead-days", type=int, default=defaults.dead_stock_days,
                        help="días sin ventas para considerar un producto estancado (default: %(default)s)")
    parser.add_argument("--slow-days", type=int, default=defaults.slow_coverage_days,
                        help="días de cobertura a partir de los cuales la rotación es lenta (default: %(default)s)")
    parser.add_argument("--max-items", type=int, default=defaults.max_items,
                        help="productos listados por sección del reporte (default: %(default)s)")
    parser.add_argument("--as-of", type=_parse_as_of,
                        help="fecha de corte ISO 8601, p. ej. 2026-09-01T00:00 (default: ahora)")
    parser.add_argument("--output", help="ruta del JSON; '-' para imprimirlo (default: reports/analitica_<fecha>.json)")
    parser.add_argument("--persist", action="store_true",
                        help="guardar el reporte en la tabla inventory_analytics_runs")
    parser.add_argument("--apply-indexes", action="store_true", help="crear los índices recomendados antes de correr")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostrar el avance de cada lote")
    return parser


def _write_report(report: dict[str, Any], output: str | None, as_of: datetime) -> str:
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if output == "-":
        sys.stdout.write(payload + "\n")
        return "stdout"
    path = Path(output) if output else REPORTS_DIR / f"analitica_{as_of.astimezone():%Y%m%d_%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Escritura atómica: nunca queda un reporte a medias si el proceso se interrumpe.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def _log_summary(result: AnalyticsResult) -> None:
    r = result.report
    valuation, stock, movement = r["valuation"], r["stock_health"], r["movement_health"]
    log.info(
        "Productos: %d | Valor a costo: %s | Agotados: %d | Críticos: %d | Estancados: %d (%s) | "
        "Lenta rotación: %d | Productos con anomalías: %d | %d lotes en %d ms",
        r["products_scanned"], valuation["cost_value"], stock["out_of_stock"]["count"],
        stock["critical"]["count"], movement["dead"]["count"], movement["dead"]["cost_value"],
        movement["slow"]["count"], r["catalog_anomalies"]["products_with_anomalies"],
        r["run"]["chunks"], r["run"]["duration_ms"],
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    log.setLevel(logging.INFO)  # el resumen final siempre se muestra; el avance por lote, sólo con -v
    logging.getLogger("inventario.analytics.batch").setLevel(logging.INFO if args.verbose else logging.WARNING)

    try:
        params = AnalyticsParams(
            chunk_size=args.chunk_size,
            sales_window_days=args.window_days,
            dead_stock_days=args.dead_days,
            slow_coverage_days=args.slow_days,
            max_items=args.max_items,
        )
        url = config.database_url()
    except (ValueError, config.ConfigError) as exc:
        log.error("%s", exc)
        return 1

    try:
        with batch_engine(url) as engine:
            if args.apply_indexes:
                apply_indexes(engine)
            missing = missing_indexes(engine)
            if missing:
                log.warning("Faltan índices recomendados (%s); ejecuta con --apply-indexes.", ", ".join(missing))
            result = run_inventory_analytics(engine, params, as_of=args.as_of)
            run_id = persist_report(engine, result) if args.persist else None
    except SQLAlchemyError as exc:
        log.error("Error de base de datos: %s", getattr(exc, "orig", exc))
        return 2

    destination = _write_report(result.report, args.output, result.as_of)
    _log_summary(result)
    log.info("Reporte: %s%s", destination, f" | guardado como ejecución #{run_id}" if run_id else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
