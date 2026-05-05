"""Sibling module of ``scripts/check_schema_drift.py``: constants + dataclasses.

Imported by ``check_schema_drift.py`` (the entry-point script) and by
the parser / comparator / baseline siblings. Kept under the 800-line
ceiling and free of side effects so tests and other gates can import
freely.

The split mirrors ``scripts/_setting_to_startup_trace_*.py``: the
top-level CLI script orchestrates, while implementation lives in
underscore-prefixed sibling files.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from sqlglot.expressions import DataType


class SqlglotFallbackFilter(logging.Filter):
    """Drop only the expected ``Falling back to ... 'Command'`` notices.

    sqlglot emits a WARNING every time it cannot recognise a DDL
    statement and falls back to a generic ``Command`` node. For our
    schema files those fallbacks are expected (CREATE TRIGGER on
    SQLite, plpgsql CREATE FUNCTION on Postgres, CREATE CONSTRAINT
    TRIGGER) and the parser filters them out before comparison.

    A targeted filter (vs ``setLevel(ERROR)``) keeps real sqlglot
    warnings audible: a malformed CREATE TABLE that triggers a
    different warning would still surface.
    """

    _FALLBACK_PHRASE: Final[str] = "Falling back to parsing as a 'Command'"

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False to drop the record, True to keep it."""
        return self._FALLBACK_PHRASE not in record.getMessage()


def install_sqlglot_filter() -> None:
    """Attach :class:`SqlglotFallbackFilter` to sqlglot's root logger.

    Idempotent: a second call adds a second filter instance, but the
    behaviour is identical (both drop the same records).
    """
    logging.getLogger("sqlglot").addFilter(SqlglotFallbackFilter())


# ── Repo-relative defaults ──────────────────────────────────────

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_SCHEMA: Final[Path] = (
    _REPO_ROOT / "src" / "synthorg" / "persistence" / "sqlite" / "schema.sql"
)
DEFAULT_POSTGRES_SCHEMA: Final[Path] = (
    _REPO_ROOT / "src" / "synthorg" / "persistence" / "postgres" / "schema.sql"
)
DEFAULT_SQLITE_REVISIONS: Final[Path] = (
    _REPO_ROOT / "src" / "synthorg" / "persistence" / "sqlite" / "revisions"
)
DEFAULT_POSTGRES_REVISIONS: Final[Path] = (
    _REPO_ROOT / "src" / "synthorg" / "persistence" / "postgres" / "revisions"
)
DEFAULT_BASELINE: Final[Path] = _REPO_ROOT / "scripts" / "schema_drift_baseline.txt"

# Migration filenames carry a 14-digit timestamp Atlas picks at
# generation time, independently per backend. The gate strips the
# timestamp and compares by suffix because the timestamps will never
# agree across the two backends (Atlas runs them in different
# Docker containers / sqlite-CLI invocations on different machines).
MIGRATION_FILENAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<ts>\d{14})_(?P<suffix>.+)\.sql$"
)
BASELINE_MIGRATION_NAME: Final[str] = "00000000000000_baseline.sql"

# ── Type equivalence ────────────────────────────────────────────

# Default-equivalent type families. Two columns whose canonical
# DataType.Type values appear in the same family are treated as the
# same logical type and produce no drift finding.
#
# JSONB, TIMESTAMPTZ and UUID are deliberately NOT in any default
# family; pairing them with SQLite TEXT requires an explicit baseline
# entry.
TYPE_FAMILIES: Final[tuple[frozenset[Any], ...]] = (
    frozenset(
        {
            DataType.Type.TEXT,
            DataType.Type.VARCHAR,
            DataType.Type.CHAR,
            DataType.Type.NCHAR,
            DataType.Type.NVARCHAR,
        }
    ),
    frozenset(
        {
            DataType.Type.INT,
            DataType.Type.BIGINT,
            DataType.Type.SMALLINT,
            DataType.Type.TINYINT,
            DataType.Type.UINT,
            DataType.Type.UBIGINT,
            DataType.Type.USMALLINT,
            DataType.Type.UTINYINT,
            DataType.Type.SERIAL,
            DataType.Type.BIGSERIAL,
            DataType.Type.SMALLSERIAL,
        }
    ),
    frozenset(
        {
            DataType.Type.FLOAT,
            DataType.Type.DOUBLE,
            DataType.Type.DECIMAL,
            DataType.Type.BIGDECIMAL,
        }
    ),
    frozenset({DataType.Type.VARBINARY, DataType.Type.BINARY}),
    frozenset({DataType.Type.BOOLEAN}),
)

# Integer-like types that may collapse to BOOLEAN when paired with a
# CHECK(col IN (0, 1)) constraint (column-level or table-level).
INTEGER_TYPES_FOR_BOOLEAN_CHECK: Final[frozenset[Any]] = frozenset(
    {DataType.Type.INT, DataType.Type.BIGINT, DataType.Type.SMALLINT}
)

# ── Baseline file format ────────────────────────────────────────

# Each non-comment, non-blank line is ``<kind>:<key fields>:<reason>``
# where the trailing reason field is everything after the
# (kind-specific) number of leading separators. Reasons may contain
# colons (e.g. ``preserves note: payload``).
BASELINE_FIELD_COUNTS: Final[dict[str, int]] = {
    # column:table:col:s_type:p_type:reason
    "column": 6,
    # nullable:table:col:sqlite_yn:postgres_yn:reason
    "nullable": 6,
    # pk:table:sqlite_cols:postgres_cols:reason
    #     where _cols are comma-separated column names; ``_`` means no PK.
    "pk": 5,
    # unique:table:cols:side:reason
    "unique": 5,
    # index:name:side:reason
    "index": 4,
    # index_columns:name:sqlite_cols:postgres_cols:reason
    "index_columns": 5,
    # index_attr:name:attribute:sqlite_value:postgres_value:reason
    #     where attribute is one of: unique, where, using.
    "index_attr": 6,
    # table:name:side:reason
    "table": 4,
    # migration:suffix:side:reason
    "migration": 4,
}

# Side markers for one-sided drift entries.
SIDE_SQLITE_ONLY: Final[str] = "sqlite_only"
SIDE_POSTGRES_ONLY: Final[str] = "postgres_only"

# ── Internal data structures ────────────────────────────────────


@dataclass(frozen=True)
class NormalizedColumn:
    """Canonical column representation suitable for cross-dialect comparison."""

    name: str
    canonical_type: Any  # sqlglot's DataType.Type enum (see import note)
    raw_type: str  # original SQL token for finding display
    nullable: bool  # True iff the column does not carry NOT NULL


@dataclass(frozen=True)
class NormalizedTable:
    """Canonical table representation."""

    name: str
    columns: dict[str, NormalizedColumn] = field(default_factory=dict)
    primary_key: tuple[str, ...] = ()
    uniques: frozenset[tuple[str, ...]] = field(default_factory=frozenset)


@dataclass(frozen=True)
class NormalizedIndex:
    """Canonical index representation. Keyed by name across dialects."""

    name: str
    table: str
    columns: tuple[str, ...]
    unique: bool
    where: str | None
    using: str | None  # ``GIN`` / ``BTREE`` / ``HASH``; ``None`` = default


def yn(*, nullable: bool) -> str:
    """Return ``Y`` for nullable, ``N`` for NOT NULL."""
    return "Y" if nullable else "N"


def bool_yn(*, value: bool) -> str:
    """Return ``Y`` for True, ``N`` for False.

    Generic boolean formatter for finding-key fields that encode
    arbitrary booleans (e.g. an index's ``unique`` flag). Distinct
    from :func:`yn`, which is keyword-only on ``nullable`` and
    documents the nullability semantics in its name. Keyword-only on
    ``value`` to keep call sites self-documenting and to comply with
    ruff FBT001 (no boolean-typed positional arguments).
    """
    return "Y" if value else "N"
