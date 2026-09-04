"""Isolated, provider-neutral video recognition benchmark harness.

The benchmark deliberately lives outside :mod:`app`.  It owns frozen sample
contracts, protocol adapters, deterministic scoring, and a no-provider dry run
so that benchmark work cannot mutate production ingestion or search state.
"""

from .schema import (
    BENCHMARK_REVISION,
    BenchmarkCatalog,
    BenchmarkGateError,
    CatalogValidationError,
    load_catalog,
    load_probe_snapshot,
)

__all__ = [
    "BENCHMARK_REVISION",
    "BenchmarkCatalog",
    "BenchmarkGateError",
    "CatalogValidationError",
    "load_catalog",
    "load_probe_snapshot",
]
