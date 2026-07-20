# module-kind: declarative
"""SQL statements and filter building for the SQLite memory vector repository.

Split from the repository module so the repository stays within its
module-size budget and the SQL is reviewable in one place.

The dense index (``memory_entries_vec_<dims>``) is created at runtime
because ``sqlite-vec``'s ``vec0`` requires a literal dimension and the
embedding width is operator-configurable.
"""

from typing import Final

from synthorg.memory.vector_spec import MemoryVectorSearchSpec
from synthorg.persistence._shared import format_iso_utc

ENTRY_COLUMNS: Final[str] = (
    "memory_id, agent_id, namespace, category, content, source, "
    "confidence, tags, created_at, updated_at, expires_at"
)

UPSERT_ENTRY: Final[str] = f"""
    INSERT INTO memory_entries ({ENTRY_COLUMNS}, token_count)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(memory_id) DO UPDATE SET
        agent_id = excluded.agent_id,
        namespace = excluded.namespace,
        category = excluded.category,
        content = excluded.content,
        source = excluded.source,
        confidence = excluded.confidence,
        tags = excluded.tags,
        created_at = excluded.created_at,
        updated_at = excluded.updated_at,
        expires_at = excluded.expires_at,
        token_count = excluded.token_count
"""  # noqa: S608 -- column list is a compile-time constant

DELETE_TERMS: Final[str] = "DELETE FROM memory_entry_terms WHERE memory_id = ?"

INSERT_TERM: Final[str] = (
    "INSERT INTO memory_entry_terms (memory_id, term, term_frequency) VALUES (?, ?, ?)"
)

SELECT_BY_ID: Final[str] = (
    f"SELECT {ENTRY_COLUMNS} FROM memory_entries "  # noqa: S608
    "WHERE memory_id = ? AND agent_id = ?"
)

DELETE_BY_ID: Final[str] = (
    "DELETE FROM memory_entries WHERE memory_id = ? AND agent_id = ?"
)

COUNT_BY_AGENT: Final[str] = "SELECT COUNT(*) FROM memory_entries WHERE agent_id = ?"

COUNT_BY_AGENT_CATEGORY: Final[str] = (
    "SELECT COUNT(*) FROM memory_entries WHERE agent_id = ? AND category = ?"
)

SELECT_EXPIRED_IDS: Final[str] = (
    "SELECT memory_id FROM memory_entries "
    "WHERE expires_at IS NOT NULL AND expires_at <= ?"
)

SELECT_OLDEST_IDS: Final[str] = (
    "SELECT memory_id FROM memory_entries WHERE agent_id = ? "
    "ORDER BY created_at ASC, memory_id ASC LIMIT ?"
)


def build_filter_clause(spec: MemoryVectorSearchSpec) -> tuple[str, list[object]]:
    """Build the shared ``WHERE`` fragment for a search spec.

    Emits the agent scope plus every optional filter, so dense, lexical
    and metadata-only reads apply an identical predicate and cannot
    drift apart.

    Args:
        spec: The search specification.

    Returns:
        A ``(sql_fragment, params)`` pair. The fragment always starts
        with ``e.agent_id = ?`` so callers can prefix it with ``WHERE``.
    """
    clauses: list[str] = ["e.agent_id = ?"]
    params: list[object] = [spec.agent_id]

    if spec.namespaces:
        placeholders = ", ".join("?" for _ in spec.namespaces)
        clauses.append(f"e.namespace IN ({placeholders})")
        params.extend(sorted(spec.namespaces))
    if spec.categories:
        placeholders = ", ".join("?" for _ in spec.categories)
        clauses.append(f"e.category IN ({placeholders})")
        params.extend(sorted(c.value for c in spec.categories))
    for tag in spec.tags:
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each(e.tags) WHERE json_each.value = ?)"
        )
        params.append(tag)
    if spec.excluded_tags:
        placeholders = ", ".join("?" for _ in spec.excluded_tags)
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM json_each(e.tags) "  # noqa: S608 -- placeholders are count-derived
            f"WHERE json_each.value IN ({placeholders}))"
        )
        params.extend(spec.excluded_tags)
    if spec.since is not None:
        clauses.append("e.created_at >= ?")
        params.append(format_iso_utc(spec.since))
    if spec.until is not None:
        clauses.append("e.created_at < ?")
        params.append(format_iso_utc(spec.until))
    if spec.now is not None:
        clauses.append("(e.expires_at IS NULL OR e.expires_at > ?)")
        params.append(format_iso_utc(spec.now))

    return " AND ".join(clauses), params


def delete_entries_by_id(id_placeholders: str) -> str:
    """Return the batched entry delete for a set of memory ids.

    Returns:
        A ``DELETE`` restricted to the given ids.
    """
    return f"DELETE FROM memory_entries WHERE memory_id IN ({id_placeholders})"  # noqa: S608 -- placeholders are count-derived


def list_filtered(where: str, *, oldest_first: bool = False) -> str:
    """Return the metadata-only listing query for a filter fragment.

    Args:
        where: The pre-built, parameterised filter fragment.
        oldest_first: Order oldest-first (for cap eviction) rather than
            the default newest-first.

    Returns:
        A ``SELECT`` ordered by creation time (direction per
        ``oldest_first``), with ``memory_id`` breaking ties for
        determinism.
    """
    cols = ", ".join(f"e.{c.strip()}" for c in ENTRY_COLUMNS.split(","))
    direction = "ASC" if oldest_first else "DESC"
    return (
        f"SELECT {cols} FROM memory_entries AS e "  # noqa: S608 -- fragment is repository-built from a typed spec
        f"WHERE {where} ORDER BY e.created_at {direction}, e.memory_id ASC LIMIT ?"
    )


def select_by_ids(where: str, id_placeholders: str) -> str:
    """Return the hydration query for a set of dense-search memory ids.

    Returns:
        A ``SELECT`` restricted to the given ids and the filter.
    """
    cols = ", ".join(f"e.{c.strip()}" for c in ENTRY_COLUMNS.split(","))
    return (
        f"SELECT {cols} FROM memory_entries AS e "  # noqa: S608 -- fragments are repository-built from a typed spec
        f"WHERE e.memory_id IN ({id_placeholders}) AND {where}"
    )


def lexical_postings(where: str, term_placeholders: str) -> str:
    """Return the posting-list query backing BM25 scoring.

    Emits one row per (entry, matching term) with the entry's token
    count, which is everything the Python-side BM25 scorer needs beyond
    corpus statistics.

    Returns:
        A ``SELECT`` joining the inverted index to filtered entries.
    """
    cols = ", ".join(f"e.{c.strip()}" for c in ENTRY_COLUMNS.split(","))
    return (
        "SELECT t.term AS term, t.term_frequency AS term_frequency, "  # noqa: S608 -- fragments are repository-built from a typed spec
        f"e.token_count AS token_count, {cols} "
        "FROM memory_entry_terms AS t "
        "JOIN memory_entries AS e ON e.memory_id = t.memory_id "
        f"WHERE t.term IN ({term_placeholders}) AND {where}"
    )


def corpus_stats(where: str) -> str:
    """Return the corpus-size and average-length query for BM25.

    Returns:
        A ``SELECT`` yielding ``(doc_count, avg_length)`` over the
        filtered set.
    """
    return (
        "SELECT COUNT(*) AS doc_count, "  # noqa: S608 -- fragment is repository-built from a typed spec
        "COALESCE(AVG(e.token_count), 0.0) AS avg_length "
        f"FROM memory_entries AS e WHERE {where}"
    )


def document_frequency(where: str, term_placeholders: str) -> str:
    """Return the per-term document-frequency query for BM25.

    Returns:
        A ``SELECT`` yielding ``(term, doc_frequency)``.
    """
    return (
        "SELECT t.term AS term, COUNT(*) AS doc_frequency "  # noqa: S608 -- fragments are repository-built from a typed spec
        "FROM memory_entry_terms AS t "
        "JOIN memory_entries AS e ON e.memory_id = t.memory_id "
        f"WHERE t.term IN ({term_placeholders}) AND {where} "
        "GROUP BY t.term"
    )


def create_vector_table(table: str, dimensions: int) -> str:
    """Return the ``vec0`` DDL for a dimension-specific dense index.

    ``memory_id`` is the primary key rather than a rowid mirror because
    SQLite reuses rowids: a vector left behind by a delete would
    otherwise be inherited by whichever entry next claims that rowid,
    and a fresh memory would rank as an exact match for a deleted one.

    ``agent_id`` is a partition key so ownership is enforced *inside*
    the KNN. Filtering after the k nearest are chosen spends every slot
    on whichever agent happens to own the closest vectors, which returns
    nothing for everyone else once a store holds more than one agent.

    Returns:
        A ``CREATE VIRTUAL TABLE IF NOT EXISTS`` statement.
    """
    return (
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING vec0("
        "memory_id TEXT PRIMARY KEY, "
        "agent_id TEXT PARTITION KEY, "
        f"embedding float[{int(dimensions)}])"
    )


SELECT_VECTOR_TABLES: Final[str] = (
    "SELECT name FROM sqlite_master WHERE type = 'table' "
    "AND name LIKE 'memory_entries_vec_%' AND name <> ? "
    # vec0 backs each index with shadow tables sharing its name prefix;
    # only the virtual table itself carries the vectors.
    "AND sql LIKE 'CREATE VIRTUAL TABLE%'"
)


def count_vectors(table: str) -> str:
    """Return the row count for a dense index table.

    Returns:
        A ``SELECT COUNT(*)`` over *table*.
    """
    return f"SELECT COUNT(*) FROM {table}"  # noqa: S608 -- table name comes from sqlite_master


def upsert_vector(table: str) -> str:
    """Return the dense-index insert for *table*.

    ``vec0`` has no upsert, so callers delete the prior row first.

    Returns:
        An ``INSERT`` statement.
    """
    return f"INSERT INTO {table} (memory_id, agent_id, embedding) VALUES (?, ?, ?)"  # noqa: S608 -- table name is repository-controlled


def delete_vector(table: str) -> str:
    """Return the dense-index delete for *table*.

    Returns:
        A ``DELETE`` statement.
    """
    return f"DELETE FROM {table} WHERE memory_id = ?"  # noqa: S608 -- table name is repository-controlled


def dense_match(table: str) -> str:
    """Return the agent-scoped KNN query for *table*.

    ``vec0`` takes the neighbour count as a ``k =`` predicate rather
    than a ``LIMIT``, and constrains the search to one partition when
    the partition key is given, so the k slots hold only this agent's
    vectors. The remaining spec filters (category, namespace, tags,
    expiry) still apply during hydration, which the caller's over-fetch
    multiplier compensates for.

    Returns:
        A ``SELECT`` yielding ``(memory_id, distance)``.
    """
    return (
        f"SELECT memory_id, distance FROM {table} "  # noqa: S608 -- table name is repository-controlled
        "WHERE embedding MATCH ? AND k = ? AND agent_id = ? ORDER BY distance"
    )
