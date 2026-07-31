"""Width classification against pgvector's fixed index ceilings.

The ceilings belong to the extension, not to us, and an operator picking an
embedder has no way to know them. Classifying a width is what lets a surface
say what a model will mean for recall *before* the choice is committed,
rather than leaving it to a log line after the next restart.
"""

import pytest

from synthorg.core.vector_limits import (
    HNSW_HALFVEC_MAX_DIMENSIONS,
    HNSW_VECTOR_MAX_DIMENSIONS,
    STORAGE_MAX_DIMENSIONS,
    IndexSupport,
    index_support_for,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("dims", "expected"),
    [
        (1, IndexSupport.INDEXED),
        (768, IndexSupport.INDEXED),
        (1024, IndexSupport.INDEXED),
        (HNSW_VECTOR_MAX_DIMENSIONS, IndexSupport.INDEXED),
        (HNSW_VECTOR_MAX_DIMENSIONS + 1, IndexSupport.INDEXED_HALF_PRECISION),
        (HNSW_HALFVEC_MAX_DIMENSIONS, IndexSupport.INDEXED_HALF_PRECISION),
        (HNSW_HALFVEC_MAX_DIMENSIONS + 1, IndexSupport.EXACT_SCAN),
        (4096, IndexSupport.EXACT_SCAN),
        (STORAGE_MAX_DIMENSIONS, IndexSupport.EXACT_SCAN),
        (STORAGE_MAX_DIMENSIONS + 1, IndexSupport.UNSTORABLE),
    ],
)
def test_width_is_classified_at_every_boundary(
    dims: int, expected: IndexSupport
) -> None:
    assert index_support_for(dims) is expected


def test_the_common_large_embedder_width_is_the_unindexable_one() -> None:
    # 4096 is a popular native width and sits 96 past the last rung, which is
    # exactly the trap this classification exists to show an operator before
    # they commit rather than after a restart.
    assert index_support_for(4096) is IndexSupport.EXACT_SCAN
    assert index_support_for(4000) is IndexSupport.INDEXED_HALF_PRECISION
