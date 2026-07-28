"""Vector-store width ceilings, shared across the layers that enforce them.

pgvector fixes these, and three layers have to agree on them: the Postgres
repository picks a column type from them, the settings registry refuses a
wider ``memory.embedder_dims`` at write time, and embedder resolution
refuses one that no store could hold. Stating them once here keeps a
settings definition from reaching into persistence to validate itself,
which would invert the dependency, without leaving copies free to drift.
"""

from typing import Final

HNSW_VECTOR_MAX_DIMENSIONS: Final[int] = 2000
"""Widest full-precision ``vector`` an HNSW index accepts."""

HNSW_HALFVEC_MAX_DIMENSIONS: Final[int] = 4000
"""Widest half-precision ``halfvec`` an HNSW index accepts."""

STORAGE_MAX_DIMENSIONS: Final[int] = 16000
"""Widest vector pgvector stores at all, indexed or not.

Applied to every backend rather than only to Postgres: one operator-facing
width setting serves both, and the narrower of the two ceilings is the one
that has to hold for the setting to be portable.
"""

__all__ = [
    "HNSW_HALFVEC_MAX_DIMENSIONS",
    "HNSW_VECTOR_MAX_DIMENSIONS",
    "STORAGE_MAX_DIMENSIONS",
]
