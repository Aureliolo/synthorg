"""Vector-store width ceilings, shared across the layers that enforce them.

pgvector fixes these, and three layers have to agree on them: the Postgres
repository picks a column type from them, the settings registry refuses a
wider ``memory.embedder_dims`` at write time, and embedder resolution
refuses one that no store could hold. Stating them once here keeps a
settings definition from reaching into persistence to validate itself,
which would invert the dependency, without leaving copies free to drift.
"""

from enum import StrEnum
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


class IndexSupport(StrEnum):
    """What a vector store can do with a given embedding width.

    A statement about the store, not a judgement of the model: the same
    embedder is indexable against one backend and not another. Named so a
    surface can report the mechanical consequence of a width without
    ranking, recommending, or choosing an embedder on the operator's behalf.

    Attributes:
        INDEXED: An HNSW index covers this width at full precision.
        INDEXED_HALF_PRECISION: Indexed, but only by storing half-precision
            components. Approximate recall is kept; exactness is traded for
            it, which is worth saying out loud.
        EXACT_SCAN: Stored and searchable, with no index. Results stay
            correct; every query reads the whole corpus, so latency grows
            with it.
        UNSTORABLE: Beyond what the store holds at all.
    """

    INDEXED = "indexed"
    INDEXED_HALF_PRECISION = "indexed_half_precision"
    EXACT_SCAN = "exact_scan"
    UNSTORABLE = "unstorable"


def index_support_for(dimensions: int) -> IndexSupport:
    """Classify an embedding width against pgvector's fixed ceilings.

    Mirrors the storage strategy the Postgres repository selects, so a
    surface can tell an operator what a model's width will mean *before*
    they commit to it rather than after the next restart.

    Returns:
        The support level for *dimensions*.
    """
    if dimensions > STORAGE_MAX_DIMENSIONS:
        return IndexSupport.UNSTORABLE
    if dimensions <= HNSW_VECTOR_MAX_DIMENSIONS:
        return IndexSupport.INDEXED
    if dimensions <= HNSW_HALFVEC_MAX_DIMENSIONS:
        return IndexSupport.INDEXED_HALF_PRECISION
    return IndexSupport.EXACT_SCAN


__all__ = [
    "HNSW_HALFVEC_MAX_DIMENSIONS",
    "HNSW_VECTOR_MAX_DIMENSIONS",
    "STORAGE_MAX_DIMENSIONS",
    "IndexSupport",
    "index_support_for",
]
