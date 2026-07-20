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
from typing import Final, LiteralString, cast

from synthorg.memory.vector_spec import MemoryVectorSearchSpec


def vector_column(dimensions: int) -> LiteralString:
    """Return the dense column name for a given embedding width.

    The name is interpolated from an ``int`` the repository controls, so
    it carries no injection surface; the cast narrows it back to
    ``LiteralString`` for psycopg's query types, which otherwise reject
    every composed statement built from it.

    Returns:
        The column name.
    """
    # mypy erases LiteralString to str and calls the cast redundant;
    # pyright needs it, because psycopg's query types reject a plain str.
    return cast("LiteralString", f"embedding_{int(dimensions)}")  # type: ignore[redundant-cast]


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
        clauses.append("e.tags @> %s::jsonb")
        params.append(_json_array(spec.tags))
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


def list_filtered(where: LiteralString) -> LiteralString:
    """Return the metadata-only listing query.

    Returns:
        A ``SELECT`` ordered newest-first for determinism.
    """
    return (
        f"SELECT {_qualified_columns()} FROM memory_entries AS e "  # noqa: S608 -- fragment is repository-built from a typed spec
        f"WHERE {where} ORDER BY e.created_at DESC, e.memory_id ASC LIMIT %s"
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


def add_vector_column(column: LiteralString, dimensions: int) -> LiteralString:
    """Return DDL adding the dense column for a given width.

    Returns:
        An ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` statement.
    """
    return cast(  # type: ignore[redundant-cast]  # see vector_column
        "LiteralString",
        f"ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS "
        f"{column} vector({int(dimensions)})",
    )


SELECT_VECTOR_COLUMNS: Final[LiteralString] = (
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = 'memory_entries' "
    "AND column_name LIKE 'embedding\\_%' AND column_name <> %s"
)


def count_vectors(column: LiteralString) -> LiteralString:
    """Return the populated-row count for a dense column.

    Returns:
        A ``SELECT COUNT(*)`` over the non-null values of *column*.
    """
    return cast(  # type: ignore[redundant-cast]  # see vector_column
        "LiteralString",
        f"SELECT COUNT(*) FROM memory_entries WHERE {column} IS NOT NULL",  # noqa: S608 -- column name comes from information_schema
    )


def create_vector_index(column: LiteralString) -> LiteralString:
    """Return DDL creating the HNSW index over the dense column.

    HNSW is used over IVFFlat because it needs no training pass and
    performs well on a corpus that grows continuously, which is exactly
    how agent memory accumulates.

    Returns:
        A ``CREATE INDEX IF NOT EXISTS`` statement.
    """
    return (
        f"CREATE INDEX IF NOT EXISTS idx_{column} ON memory_entries "
        f"USING hnsw ({column} vector_l2_ops)"
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


def dense_match(column: LiteralString, where: LiteralString) -> LiteralString:
    """Return the KNN query over the dense column.

    Uses L2 distance to match the ``vec0`` default on SQLite so both
    backends rank identically for the same vectors.

    Returns:
        A ``SELECT`` yielding entries plus their distance.
    """
    return (
        f"SELECT {_qualified_columns()}, "  # noqa: S608 -- fragments are repository-built from a typed spec
        f"e.{column} <-> %s::vector AS distance "
        "FROM memory_entries AS e "
        f"WHERE {where} AND e.{column} IS NOT NULL "
        "ORDER BY distance LIMIT %s"
    )


def set_vector(column: LiteralString) -> LiteralString:
    """Return the dense-column update for one entry.

    Returns:
        An ``UPDATE`` statement.
    """
    return f"UPDATE memory_entries SET {column} = %s::vector WHERE memory_id = %s"  # noqa: S608 -- column name is repository-controlled
