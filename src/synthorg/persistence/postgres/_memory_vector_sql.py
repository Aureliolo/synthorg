# module-kind: declarative
"""SQL statements and filter building for the Postgres memory vector repository.

Mirrors the SQLite sibling statement for statement so the two backends
stay at parity; the differences are dialect only (``%s`` placeholders,
``JSONB`` containment for tags, ``TIMESTAMPTZ`` binding).

The dense column and its HNSW index are added at runtime rather than in
the migration because the embedding width is operator-configurable, and
``vector(n)`` needs a literal width. The column is named for its
dimension so switching embedder re-indexes into a fresh column instead of
silently mixing incompatible vectors.
"""

import json
from typing import Final, LiteralString, NamedTuple, cast

from synthorg.memory.vector_spec import MemoryVectorSearchSpec

# pgvector's index ceilings, per element type. A full-precision ``vector``
# takes an HNSW index up to 2000 dimensions and a half-precision ``halfvec``
# up to 4000, while either type *stores* up to 16000. A width above the HNSW
# ceilings is therefore searchable but not ANN-indexable.
HNSW_VECTOR_MAX_DIMENSIONS: Final[int] = 2000
HNSW_HALFVEC_MAX_DIMENSIONS: Final[int] = 4000
STORAGE_MAX_DIMENSIONS: Final[int] = 16000


class DenseColumnSpec(NamedTuple):
    """How one embedding width is stored and indexed.

    Attributes:
        dimensions: The embedding width.
        name: Dense column name. Width- and type-suffixed so switching
            embedder re-indexes into a fresh column instead of silently
            mixing incompatible vectors, and so a width that changes element
            type never collides with a column an earlier build left behind
            (``ADD COLUMN IF NOT EXISTS`` would keep the old type).
        element_type: pgvector type backing the column.
        indexable: Whether an HNSW index can be built at this width. When
            ``False`` dense search still works, by exact scan.
    """

    dimensions: int
    name: LiteralString
    element_type: LiteralString
    indexable: bool


def dense_column_spec(dimensions: int) -> DenseColumnSpec:
    """Resolve the storage strategy for an embedding width.

    Half precision is chosen only where full precision cannot be indexed:
    ``halfvec`` costs recall against the exact vectors, which is a fair trade
    for keeping ANN search but not one worth making below 2000 dimensions.

    Returns:
        The :class:`DenseColumnSpec` for *dimensions*.
    """
    width = int(dimensions)
    if width <= HNSW_VECTOR_MAX_DIMENSIONS:
        return DenseColumnSpec(
            dimensions=width,
            name=_column_name(f"embedding_{width}"),
            element_type="vector",
            indexable=True,
        )
    if width <= HNSW_HALFVEC_MAX_DIMENSIONS:
        return DenseColumnSpec(
            dimensions=width,
            name=_column_name(f"embedding_h{width}"),
            element_type="halfvec",
            indexable=True,
        )
    return DenseColumnSpec(
        dimensions=width,
        name=_column_name(f"embedding_{width}"),
        element_type="vector",
        indexable=False,
    )


def _column_name(name: str) -> LiteralString:
    """Narrow a repository-composed column name back to ``LiteralString``.

    The name is interpolated from an ``int`` the repository controls, so it
    carries no injection surface; psycopg's query types nonetheless reject
    every statement composed from a plain ``str``.

    Returns:
        The column name.
    """
    # mypy erases LiteralString to str and calls the cast redundant;
    # pyright needs it, because psycopg's query types reject a plain str.
    return cast("LiteralString", name)  # type: ignore[redundant-cast]


ENTRY_COLUMNS: Final[LiteralString] = (
    "memory_id, agent_id, namespace, category, content, source, "
    "confidence, tags, created_at, updated_at, expires_at"
)

UPSERT_ENTRY: Final[LiteralString] = f"""
    INSERT INTO memory_entries ({ENTRY_COLUMNS}, token_count)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
    ON CONFLICT (memory_id) DO UPDATE SET
        agent_id = EXCLUDED.agent_id,
        namespace = EXCLUDED.namespace,
        category = EXCLUDED.category,
        content = EXCLUDED.content,
        source = EXCLUDED.source,
        confidence = EXCLUDED.confidence,
        tags = EXCLUDED.tags,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at,
        expires_at = EXCLUDED.expires_at,
        token_count = EXCLUDED.token_count
"""  # noqa: S608 -- column list is a compile-time constant

DELETE_TERMS: Final[LiteralString] = (
    "DELETE FROM memory_entry_terms WHERE memory_id = %s"
)

INSERT_TERM: Final[LiteralString] = (
    "INSERT INTO memory_entry_terms (memory_id, term, term_frequency) "
    "VALUES (%s, %s, %s)"
)

SELECT_BY_ID: Final[LiteralString] = (
    f"SELECT {ENTRY_COLUMNS} FROM memory_entries "  # noqa: S608
    "WHERE memory_id = %s AND agent_id = %s"
)

DELETE_BY_ID: Final[LiteralString] = (
    "DELETE FROM memory_entries WHERE memory_id = %s AND agent_id = %s"
)

COUNT_BY_AGENT: Final[LiteralString] = (
    "SELECT COUNT(*) AS n FROM memory_entries WHERE agent_id = %s"
)

COUNT_BY_AGENT_CATEGORY: Final[LiteralString] = (
    "SELECT COUNT(*) AS n FROM memory_entries WHERE agent_id = %s AND category = %s"
)

DELETE_EXPIRED: Final[LiteralString] = (
    "DELETE FROM memory_entries WHERE expires_at IS NOT NULL AND expires_at <= %s"
)

SELECT_OLDEST_IDS: Final[LiteralString] = (
    "SELECT memory_id FROM memory_entries WHERE agent_id = %s "
    "ORDER BY created_at ASC, memory_id ASC LIMIT %s"
)

# Session advisory lock serialising the CONCURRENTLY index build across
# processes. Keyed on the dimension-suffixed column name via hashtext so
# each width has its own lock; hashtext's int4 widens to the bigint the
# single-argument overload takes.
ACQUIRE_INDEX_BUILD_LOCK: Final[LiteralString] = "SELECT pg_advisory_lock(hashtext(%s))"
RELEASE_INDEX_BUILD_LOCK: Final[LiteralString] = (
    "SELECT pg_advisory_unlock(hashtext(%s))"
)

# Per-transaction HNSW scan tuning for a filtered dense search (pgvector
# 0.8+). ``iterative_scan`` keeps the index producing candidates until
# the filtered LIMIT is satisfied instead of stopping at the first
# ``ef_search`` window; the wider ``ef_search`` enlarges that window.
# SET LOCAL resets on commit, so neither leaks to the pool.
SET_DENSE_ITERATIVE_SCAN: Final[LiteralString] = (
    "SET LOCAL hnsw.iterative_scan = relaxed_order"
)
SET_DENSE_EF_SEARCH: Final[LiteralString] = "SET LOCAL hnsw.ef_search = 200"


def build_filter_clause(
    spec: MemoryVectorSearchSpec,
) -> tuple[LiteralString, list[object]]:
    """Build the shared ``WHERE`` fragment for a search spec.

    Emits the agent scope plus every optional filter so dense, lexical
    and metadata-only reads apply an identical predicate.

    Args:
        spec: The search specification.

    Returns:
        A ``(sql_fragment, params)`` pair. Every fragment is a literal
        and every value is bound as a parameter, so the composed string
        stays a ``LiteralString`` and carries no injection surface.
    """
    clauses: list[LiteralString] = ["e.agent_id = %s"]
    params: list[object] = [spec.agent_id]

    if spec.namespaces:
        clauses.append("e.namespace = ANY(%s)")
        params.append(sorted(spec.namespaces))
    if spec.categories:
        clauses.append("e.category = ANY(%s)")
        params.append(sorted(c.value for c in spec.categories))
    if spec.tags:
        clauses.append("e.tags @> %s::JSONB")
        params.append(_json_array(spec.tags))
    if spec.excluded_tags:
        # ``?|`` asks whether the array shares any key with the given
        # list, which is exactly "carries at least one disqualifying
        # tag"; negating it keeps the check in one indexable predicate.
        clauses.append("NOT (e.tags ?| %s)")
        params.append(sorted(spec.excluded_tags))
    if spec.since is not None:
        clauses.append("e.created_at >= %s")
        params.append(spec.since)
    if spec.until is not None:
        clauses.append("e.created_at < %s")
        params.append(spec.until)
    if spec.now is not None:
        clauses.append("(e.expires_at IS NULL OR e.expires_at > %s)")
        params.append(spec.now)

    return " AND ".join(clauses), params


def _json_array(values: tuple[str, ...]) -> str:
    """Render tags as a JSON array literal for ``@>`` containment.

    Returns:
        A JSON array string.
    """
    return json.dumps(list(values))


def _qualified_columns() -> LiteralString:
    """Return ``ENTRY_COLUMNS`` qualified with the ``e`` alias.

    Returns:
        A comma-separated column list.
    """
    return ", ".join(f"e.{c.strip()}" for c in ENTRY_COLUMNS.split(","))


def list_filtered(where: LiteralString, *, oldest_first: bool = False) -> LiteralString:
    """Return the metadata-only listing query.

    Args:
        where: The pre-built, parameterised filter fragment.
        oldest_first: Order oldest-first (for cap eviction) rather than
            the default newest-first.

    Returns:
        A ``SELECT`` ordered by creation time (direction per
        ``oldest_first``), with ``memory_id`` breaking ties.
    """
    direction: LiteralString = "ASC" if oldest_first else "DESC"
    return (
        f"SELECT {_qualified_columns()} FROM memory_entries AS e "  # noqa: S608 -- fragment is repository-built from a typed spec
        f"WHERE {where} ORDER BY e.created_at {direction}, e.memory_id ASC LIMIT %s"
    )


def lexical_postings(where: LiteralString) -> LiteralString:
    """Return the posting-list query backing BM25 scoring.

    Returns:
        A ``SELECT`` joining the inverted index to filtered entries.
    """
    return (
        "SELECT t.term AS term, t.term_frequency AS term_frequency, "  # noqa: S608 -- fragment is repository-built from a typed spec
        f"e.token_count AS token_count, {_qualified_columns()} "
        "FROM memory_entry_terms AS t "
        "JOIN memory_entries AS e ON e.memory_id = t.memory_id "
        f"WHERE t.term = ANY(%s) AND {where}"
    )


def corpus_stats(where: LiteralString) -> LiteralString:
    """Return the corpus-size and average-length query for BM25.

    Returns:
        A ``SELECT`` yielding ``(doc_count, avg_length)``.
    """
    return (
        "SELECT COUNT(*) AS doc_count, "  # noqa: S608 -- fragment is repository-built from a typed spec
        "COALESCE(AVG(e.token_count), 0.0) AS avg_length "
        f"FROM memory_entries AS e WHERE {where}"
    )


def document_frequency(where: LiteralString) -> LiteralString:
    """Return the per-term document-frequency query for BM25.

    Returns:
        A ``SELECT`` yielding ``(term, doc_frequency)``.
    """
    return (
        "SELECT t.term AS term, COUNT(*) AS doc_frequency "  # noqa: S608 -- fragment is repository-built from a typed spec
        "FROM memory_entry_terms AS t "
        "JOIN memory_entries AS e ON e.memory_id = t.memory_id "
        f"WHERE t.term = ANY(%s) AND {where} GROUP BY t.term"
    )


def add_vector_column(spec: DenseColumnSpec) -> LiteralString:
    """Return DDL adding the dense column for a given width.

    Returns:
        An ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` statement.
    """
    return cast(  # type: ignore[redundant-cast]  # see _column_name
        "LiteralString",
        f"ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS "
        f"{spec.name} {spec.element_type}({spec.dimensions})",
    )


SELECT_VECTOR_COLUMNS: Final[LiteralString] = (
    "SELECT column_name FROM information_schema.columns "
    # Scope to the connection's own schema so a same-named table in
    # another schema on the search_path cannot surface its columns here.
    "WHERE table_schema = current_schema() AND table_name = 'memory_entries' "
    # ``%%`` because psycopg parses the query for client-side placeholders
    # before binding: a bare ``%`` next to a quote reads as a malformed one.
    "AND column_name LIKE 'embedding\\_%%' AND column_name <> %s"
)


def count_vectors(column: LiteralString) -> LiteralString:
    """Return the populated-row count for a dense column.

    Returns:
        A ``SELECT COUNT(*)`` over the non-null values of *column*.
    """
    return cast(  # type: ignore[redundant-cast]  # see _column_name
        "LiteralString",
        f"SELECT COUNT(*) FROM memory_entries WHERE {column} IS NOT NULL",  # noqa: S608 -- column name comes from information_schema
    )


def create_vector_index(spec: DenseColumnSpec) -> LiteralString:
    """Return DDL creating the HNSW index over the dense column.

    HNSW is used over IVFFlat because it needs no training pass and
    performs well on a corpus that grows continuously, which is exactly
    how agent memory accumulates.

    ``CONCURRENTLY`` so building the index on an already-populated table
    (a re-index after an embedder swap or a restore) does not hold a
    share lock that blocks every agent's memory writes org-wide for the
    build's duration. This is why :meth:`ensure_ready` runs it in
    autocommit: ``CONCURRENTLY`` cannot run inside a transaction block.
    A crash mid-build can leave an ``INVALID`` index; the advisory lock
    around the call serialises builders so at most one runs at a time.

    Returns:
        A ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` statement.

    Raises:
        ValueError: If *spec* is not indexable. The caller decides what an
            unindexable width means for recall; composing DDL Postgres will
            reject is never the answer.
    """
    if not spec.indexable:
        msg = (
            f"{spec.dimensions} dimensions exceeds every pgvector HNSW "
            f"ceiling (vector {HNSW_VECTOR_MAX_DIMENSIONS}, halfvec "
            f"{HNSW_HALFVEC_MAX_DIMENSIONS})"
        )
        raise ValueError(msg)
    return (
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{spec.name} "
        f"ON memory_entries USING hnsw ({spec.name} {spec.element_type}_l2_ops)"
    )


def encode_vector(embedding: tuple[float, ...]) -> str:
    """Render an embedding in pgvector's text input format.

    psycopg adapts a Python list to a Postgres ``float8[]``, and there is
    no ``float8[] -> vector`` cast, so binding the list to a ``%s::vector``
    placeholder fails at query time. pgvector's own text form parses, and
    needs no client-side extension registration on every pooled
    connection.

    Returns:
        The ``[a,b,c]`` literal pgvector parses.
    """
    return f"[{','.join(repr(float(value)) for value in embedding)}]"


def dense_match(spec: DenseColumnSpec, where: LiteralString) -> LiteralString:
    """Return the KNN query over the dense column.

    Uses L2 distance to match the ``vec0`` default on SQLite so both
    backends rank identically for the same vectors.

    Returns:
        A ``SELECT`` yielding entries plus their distance.
    """
    return (
        f"SELECT {_qualified_columns()}, "  # noqa: S608 -- fragments are repository-built from a typed spec
        f"e.{spec.name} <-> %s::{spec.element_type} AS distance "
        "FROM memory_entries AS e "
        f"WHERE {where} AND e.{spec.name} IS NOT NULL "
        # memory_id breaks distance ties so equidistant vectors order
        # identically here, on the SQLite arm, and between runs.
        "ORDER BY distance, e.memory_id LIMIT %s"
    )


def set_vector(spec: DenseColumnSpec) -> LiteralString:
    """Return the dense-column update for one entry.

    Returns:
        An ``UPDATE`` statement.
    """
    return (
        f"UPDATE memory_entries SET {spec.name} = %s::{spec.element_type} "  # noqa: S608 -- column name and element type are repository-controlled
        "WHERE memory_id = %s"
    )
