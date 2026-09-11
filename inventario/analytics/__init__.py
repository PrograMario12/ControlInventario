"""Analítica batch del inventario: rotación, stock crítico, valorización y anomalías de catálogo."""

from inventario.analytics.batch import (
    AnalyticsResult,
    apply_indexes,
    batch_engine,
    missing_indexes,
    persist_report,
    run_inventory_analytics,
)
from inventario.analytics.metrics import AnalyticsParams

__all__ = [
    "AnalyticsParams",
    "AnalyticsResult",
    "apply_indexes",
    "batch_engine",
    "missing_indexes",
    "persist_report",
    "run_inventory_analytics",
]
