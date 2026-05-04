#!/usr/bin/env python3
"""Pre-push / CI gate: SQLite <-> Postgres schema parity.

Compares ``src/synthorg/persistence/sqlite/schema.sql`` and
``src/synthorg/persistence/postgres/schema.sql`` for structural drift,
and the two ``revisions/`` directories for migration-file parity.

The two schemas describe the same logical data model with each
engine's native types (TEXT/INTEGER/REAL on SQLite; JSONB/TIMESTAMPTZ/
BIGINT/BOOLEAN on Postgres). A frozen baseline at
``scripts/schema_drift_baseline.txt`` lists every currently-tolerated
drift entry (TEXT-vs-JSONB, TEXT-vs-TIMESTAMPTZ, GIN-only-on-Postgres
indexes, etc.) with a one-line justification. New drift not in the
baseline fails the gate; baseline-only entries (no longer detected)
print a stale-warning but still pass so an operator can clean them
up in a follow-up PR.

The baseline is shrink-only by default. Re-generate with
``--update-baseline`` (explicit user approval to commit).

Type-equivalence table (SQLite <-> Postgres):

- TEXT <-> TEXT, VARCHAR, CHAR (default).
- INTEGER <-> BIGINT, INT, SMALLINT, BIGSERIAL, SERIAL (default).
- INTEGER (with CHECK col IN (0, 1)) <-> BOOLEAN (default).
- REAL <-> DOUBLE PRECISION, FLOAT, NUMERIC (default).
- BLOB <-> BYTEA (default).
- TEXT <-> JSONB (allowed via baseline only).
- TEXT <-> TIMESTAMPTZ (allowed via baseline only).
- TEXT <-> UUID (allowed via baseline only).

Postgres-only artefacts that the gate intentionally ignores:

- ``CREATE FUNCTION ... RETURNS TRIGGER`` and ``CREATE CONSTRAINT
  TRIGGER`` (sqlite uses inline ``CREATE TRIGGER``; semantic parity
  is exercised by the conformance suite, not by the schema diff).
- ``CREATE EXTENSION`` and ``LISTEN/NOTIFY`` (deployment concerns).

Usage::

    python scripts/check_schema_drift.py
    python scripts/check_schema_drift.py --update-baseline
    python scripts/check_schema_drift.py --explain tasks
"""

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import sqlglot
from sqlglot import exp
from sqlglot.expressions import DataType

# sqlglot logs a WARNING every time it falls back from a recognised
# DDL statement to a generic ``Command`` node (e.g. ``CREATE TRIGGER``
# in SQLite, plpgsql ``CREATE FUNCTION`` bodies). The fallbacks are
# expected and intentionally ignored by ``parse_schema``; the noisy
# warnings drown out the gate's own output. Silenced at module load.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# sqlglot's ``DataType.Type`` is a nested enum class generated via
# ``AutoName``; both mypy and Pyright struggle to recognise the form
# ``DataType.Type`` in a generic-position annotation (mypy: "Variable
# ... not valid as a type"; Pyright: "Variable not allowed in type
# expression"). We accept ``Any`` for the inner type; runtime safety
# is via ``in`` lookups against ``_TYPE_FAMILIES`` whose membership is
# fixed at import time, not via static typing.

# ── Repo-relative defaults ──────────────────────────────────────

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_DEFAULT_SQLITE_SCHEMA: Final[Path] = (
    _REPO_ROOT / "src" / "synthorg" / "persistence" / "sqlite" / "schema.sql"
)
_DEFAULT_POSTGRES_SCHEMA: Final[Path] = (
    _REPO_ROOT / "src" / "synthorg" / "persistence" / "postgres" / "schema.sql"
)
_DEFAULT_SQLITE_REVISIONS: Final[Path] = (
    _REPO_ROOT / "src" / "synthorg" / "persistence" / "sqlite" / "revisions"
)
_DEFAULT_POSTGRES_REVISIONS: Final[Path] = (
    _REPO_ROOT / "src" / "synthorg" / "persistence" / "postgres" / "revisions"
)
_DEFAULT_BASELINE: Final[Path] = _REPO_ROOT / "scripts" / "schema_drift_baseline.txt"

# Migration filename pattern: 14-digit timestamp + _ + suffix + .sql.
_MIGRATION_FILENAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<ts>\d{14})_(?P<suffix>.+)\.sql$"
)
# The post-squash baseline filename reuses an all-zero timestamp; we
# never treat it as a regular migration.
_BASELINE_MIGRATION_NAME: Final[str] = "00000000000000_baseline.sql"

# ── Type equivalence ────────────────────────────────────────────
#
# Default-equivalent type families. Two columns whose canonical
# DataType.Type values appear in the same family are treated as the
# same logical type and produce no drift finding.
#
# ``JSONB``, ``TIMESTAMPTZ`` and ``UUID`` are deliberately NOT in any
# default family; pairing them with SQLite ``TEXT`` requires an
# explicit baseline entry. The schema headers document each TEXT-vs-
# JSONB and TEXT-vs-TIMESTAMPTZ pairing as intentional, so the
# baseline is auto-populated from current state at gate-creation
# time and reviewed by hand before commit.

_TYPE_FAMILIES: Final[tuple[frozenset[Any], ...]] = (
    # TEXT family.
    frozenset(
        {
            DataType.Type.TEXT,
            DataType.Type.VARCHAR,
            DataType.Type.CHAR,
            DataType.Type.NCHAR,
            DataType.Type.NVARCHAR,
        }
    ),
    # Integer family. BIGINT, INT, SMALLINT, TINYINT, UTINYINT,
    # USMALLINT, UINT, UBIGINT and the SERIAL family all collapse here
    # because SQLite has no native size variants.
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
    # Float family. SQLite REAL is 8-byte; sqlglot maps it to FLOAT,
    # which is in the same family as Postgres DOUBLE PRECISION.
    frozenset(
        {
            DataType.Type.FLOAT,
            DataType.Type.DOUBLE,
            DataType.Type.DECIMAL,
            DataType.Type.BIGDECIMAL,
        }
    ),
    # Binary family. SQLite BLOB and Postgres BYTEA both map to
    # ``DataType.Type.VARBINARY`` in sqlglot.
    frozenset({DataType.Type.VARBINARY, DataType.Type.BINARY}),
    # Boolean family (own member; the boolean-via-CHECK detector
    # below collapses INTEGER + CHECK(col IN (0,1)) into this).
    frozenset({DataType.Type.BOOLEAN}),
)

# ── Baseline file format ────────────────────────────────────────
#
# Each non-comment, non-blank line is ``<kind>:<key-fields>:<reason>``
# where the trailing ``<reason>`` field is everything after the
# (kind-specific) number of leading separators. Reasons may contain
# colons (e.g. ``audit cluster #8: payload column``).
#
# Field counts (including trailing reason):
#   column   :: kind:table:column:sqlite_type:postgres_type:reason   = 6
#   index    :: kind:index_name:side:reason                          = 4
#   table    :: kind:table_name:side:reason                          = 4
#   migration:: kind:filename_suffix:side:reason                     = 4

_BASELINE_FIELD_COUNTS: Final[dict[str, int]] = {
    "column": 6,
    "index": 4,
    "table": 4,
    "migration": 4,
}

# Side markers for one-sided drift entries.
_SIDE_SQLITE_ONLY: Final[str] = "sqlite_only"
_SIDE_POSTGRES_ONLY: Final[str] = "postgres_only"

# ── Internal data structures ────────────────────────────────────


@dataclass(frozen=True)
class NormalizedColumn:
    """Canonical column representation suitable for cross-dialect comparison."""

    name: str
    canonical_type: Any  # sqlglot's DataType.Type enum (see import note)
    raw_type: str  # original SQL token for finding display


@dataclass(frozen=True)
class NormalizedTable:
    """Canonical table representation."""

    name: str
    columns: dict[str, NormalizedColumn] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedIndex:
    """Canonical index representation. Compared by name across dialects."""

    name: str
    table: str
    columns: tuple[str, ...]
    unique: bool
    where: str | None
    using: str | None  # ``GIN`` / ``BTREE`` / ``HASH``; ``None`` = default


# ── Parse layer ─────────────────────────────────────────────────


def parse_schema(
    sql_text: str,
    dialect: str,
) -> tuple[dict[str, NormalizedTable], dict[str, NormalizedIndex]]:
    """Parse a ``schema.sql`` file into normalised tables and indexes.

    Statements that do not map to a CREATE TABLE / CREATE INDEX pair
    are filtered out: triggers, plpgsql functions, EndStatement
    sentinels, parser fall-back ``Command`` nodes, and ``None``
    placeholders all produce no entries.

    Args:
        sql_text: The full text of the schema file.
        dialect: Either ``"sqlite"`` or ``"postgres"``; passed straight
            through to sqlglot's parser.

    Returns:
        A pair ``(tables_by_name, indexes_by_name)``. Both dicts use
        identifier strings as keys.
    """
    tables: dict[str, NormalizedTable] = {}
    indexes: dict[str, NormalizedIndex] = {}
    parsed = sqlglot.parse(sql_text, dialect=dialect)
    for stmt in parsed:
        if stmt is None or not isinstance(stmt, exp.Create):
            continue
        kind = (stmt.kind or "").upper()
        if kind == "TABLE":
            normalised = _normalise_table(stmt, dialect)
            if normalised is not None:
                tables[normalised.name] = normalised
        elif kind == "INDEX":
            normalised_idx = _normalise_index(stmt, dialect)
            if normalised_idx is not None:
                indexes[normalised_idx.name] = normalised_idx
        # FUNCTION / TRIGGER / SEQUENCE / EXTENSION etc. are ignored.
    return tables, indexes


def _normalise_table(stmt: exp.Create, dialect: str) -> NormalizedTable | None:
    """Convert a ``CREATE TABLE`` AST into a :class:`NormalizedTable`."""
    schema = stmt.this
    if not isinstance(schema, exp.Schema):
        return None
    table_ident = schema.this
    table_name = table_ident.name if hasattr(table_ident, "name") else str(table_ident)
    columns: dict[str, NormalizedColumn] = {}
    for child in schema.expressions:
        if isinstance(child, exp.ColumnDef):
            normalised_col = _normalise_column(child, dialect)
            if normalised_col is not None:
                columns[normalised_col.name] = normalised_col
        # Table-level constraints (PRIMARY KEY, FOREIGN KEY, CHECK,
        # UNIQUE) are not compared in this gate's first pass; the
        # conformance suite catches semantic mismatches and the
        # schema headers document each intentional asymmetry.
    return NormalizedTable(name=table_name, columns=columns)


def _normalise_column(coldef: exp.ColumnDef, dialect: str) -> NormalizedColumn | None:
    """Convert a single ``ColumnDef`` AST into :class:`NormalizedColumn`.

    The "canonical type" is the sqlglot ``DataType.Type`` enum value,
    except that an INTEGER column carrying a sibling
    ``CHECK (col IN (0, 1))`` is collapsed to ``BOOLEAN`` so the
    SQLite boolean idiom matches Postgres ``BOOLEAN`` columns.
    """
    kind_node = coldef.args.get("kind")
    if not isinstance(kind_node, exp.DataType):
        return None
    canonical_type = kind_node.this
    raw_type = kind_node.sql(dialect=dialect).upper()
    constraints = coldef.args.get("constraints") or []
    if canonical_type in {
        DataType.Type.INT,
        DataType.Type.BIGINT,
        DataType.Type.SMALLINT,
    } and _has_boolean_check(coldef.name, constraints):
        canonical_type = DataType.Type.BOOLEAN
    return NormalizedColumn(
        name=coldef.name,
        canonical_type=canonical_type,
        raw_type=raw_type,
    )


def _has_boolean_check(
    column_name: str,
    constraints: list[exp.ColumnConstraint],
) -> bool:
    """Return ``True`` if any constraint encodes the ``IN (0, 1)`` pattern."""
    for c in constraints:
        inner = c.kind
        if not isinstance(inner, exp.CheckColumnConstraint):
            continue
        check_expr = inner.this
        if not isinstance(check_expr, exp.In):
            continue
        target = check_expr.this
        if not isinstance(target, exp.Column):
            continue
        if target.name != column_name:
            continue
        values = {
            literal.this
            for literal in check_expr.expressions
            if isinstance(literal, exp.Literal)
        }
        if values == {"0", "1"}:
            return True
    return False


def _normalise_index(stmt: exp.Create, dialect: str) -> NormalizedIndex | None:
    """Convert a ``CREATE INDEX`` AST into a :class:`NormalizedIndex`."""
    inner = stmt.this
    if not isinstance(inner, exp.Index):
        return None
    name_ident = inner.args.get("this")
    if name_ident is None:
        return None
    name = name_ident.name if hasattr(name_ident, "name") else str(name_ident)
    table_node = inner.args.get("table")
    table_name = ""
    if table_node is not None and hasattr(table_node, "name"):
        table_name = table_node.name
    unique = bool(stmt.args.get("unique"))
    params = inner.args.get("params")
    columns: tuple[str, ...] = ()
    where_text: str | None = None
    using_text: str | None = None
    if params is not None:
        ordered_cols = params.args.get("columns") or []
        column_names: list[str] = []
        for ordered in ordered_cols:
            target = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            if isinstance(target, exp.Column):
                column_names.append(target.name)
        columns = tuple(column_names)
        where_node = params.args.get("where")
        if where_node is not None:
            where_text = where_node.this.sql(dialect=dialect)
        using_node = params.args.get("using")
        if using_node is not None:
            using_text = using_node.sql(dialect=dialect).upper()
    return NormalizedIndex(
        name=name,
        table=table_name,
        columns=columns,
        unique=unique,
        where=where_text,
        using=using_text,
    )


# ── Compare layer ───────────────────────────────────────────────


def diff_schemas(
    sqlite_tables: dict[str, NormalizedTable],
    sqlite_indexes: dict[str, NormalizedIndex],
    postgres_tables: dict[str, NormalizedTable],
    postgres_indexes: dict[str, NormalizedIndex],
) -> list[str]:
    """Compare two normalised schemas and return canonical drift keys.

    Three diff passes:

    1. **Tables**: symmetric difference of table-name sets.
    2. **Columns**: per-shared-table, symmetric difference of column
       names; for shared columns, mismatched ``canonical_type`` after
       family normalisation.
    3. **Indexes**: symmetric difference of index-name sets.

    Each finding is a colon-separated string suitable for direct
    inclusion in the baseline file (with a trailing reason field
    appended by the operator).
    """
    sqlite_table_names = set(sqlite_tables)
    postgres_table_names = set(postgres_tables)
    sqlite_index_names = set(sqlite_indexes)
    postgres_index_names = set(postgres_indexes)
    findings: list[str] = []
    # Side label answers "exists ONLY on this side"; tables in
    # ``sqlite - postgres`` exist only on sqlite, hence ``sqlite_only``.
    findings.extend(
        f"table:{name}:{_SIDE_SQLITE_ONLY}"
        for name in sorted(sqlite_table_names - postgres_table_names)
    )
    findings.extend(
        f"table:{name}:{_SIDE_POSTGRES_ONLY}"
        for name in sorted(postgres_table_names - sqlite_table_names)
    )
    for table_name in sorted(sqlite_table_names & postgres_table_names):
        findings.extend(
            _diff_columns(
                sqlite_tables[table_name],
                postgres_tables[table_name],
            )
        )
    findings.extend(
        f"index:{name}:{_SIDE_SQLITE_ONLY}"
        for name in sorted(sqlite_index_names - postgres_index_names)
    )
    findings.extend(
        f"index:{name}:{_SIDE_POSTGRES_ONLY}"
        for name in sorted(postgres_index_names - sqlite_index_names)
    )
    return findings


def _diff_columns(
    sqlite_table: NormalizedTable,
    postgres_table: NormalizedTable,
) -> list[str]:
    """Return per-column drift findings for one shared table."""
    sqlite_cols = sqlite_table.columns
    postgres_cols = postgres_table.columns
    sqlite_col_names = set(sqlite_cols)
    postgres_col_names = set(postgres_cols)
    table = sqlite_table.name
    findings: list[str] = []
    findings.extend(
        f"column:{table}:{missing}:{sqlite_cols[missing].raw_type}:_"
        for missing in sorted(sqlite_col_names - postgres_col_names)
    )
    findings.extend(
        f"column:{table}:{missing}:_:{postgres_cols[missing].raw_type}"
        for missing in sorted(postgres_col_names - sqlite_col_names)
    )
    findings.extend(
        f"column:{table}:{col_name}:{sqlite_cols[col_name].raw_type}:"
        f"{postgres_cols[col_name].raw_type}"
        for col_name in sorted(sqlite_col_names & postgres_col_names)
        if not _types_equivalent(
            sqlite_cols[col_name].canonical_type,
            postgres_cols[col_name].canonical_type,
        )
    )
    return findings


def _types_equivalent(a: Any, b: Any) -> bool:
    """Return True iff *a* and *b* live in the same default-equivalence family.

    *a* / *b* are ``DataType.Type`` enum values; see the module-level
    annotation note for why ``Any`` is used at the type level.
    """
    if a == b:
        return True
    return any(a in family and b in family for family in _TYPE_FAMILIES)


# ── Migration parity ────────────────────────────────────────────


def diff_migrations(
    sqlite_dir: Path,
    postgres_dir: Path,
) -> list[str]:
    """Compare the two ``revisions/`` directories for filename-suffix parity.

    Filenames match the pattern ``<14-digit-timestamp>_<suffix>.sql``;
    we strip the timestamp (which is independently picked by Atlas on
    each side) and take the symmetric difference of suffix sets.

    The post-squash ``00000000000000_baseline.sql`` is always ignored
    so the gate does not force operators to keep an artificial entry
    pinned in the baseline.
    """
    sqlite_suffixes = _collect_suffixes(sqlite_dir)
    postgres_suffixes = _collect_suffixes(postgres_dir)
    findings: list[str] = []
    findings.extend(
        f"migration:{name}:{_SIDE_SQLITE_ONLY}"
        for name in sorted(sqlite_suffixes - postgres_suffixes)
    )
    findings.extend(
        f"migration:{name}:{_SIDE_POSTGRES_ONLY}"
        for name in sorted(postgres_suffixes - sqlite_suffixes)
    )
    return findings


def _collect_suffixes(revisions_dir: Path) -> set[str]:
    """Return the set of migration suffixes under *revisions_dir*."""
    if not revisions_dir.is_dir():
        return set()
    suffixes: set[str] = set()
    for entry in revisions_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".sql":
            continue
        if entry.name == _BASELINE_MIGRATION_NAME:
            continue
        match = _MIGRATION_FILENAME_RE.match(entry.name)
        if not match:
            continue
        suffixes.add(match.group("suffix"))
    return suffixes


# ── Baseline I/O ────────────────────────────────────────────────


def load_baseline(path: Path) -> set[str]:
    """Load the baseline file and return its set of canonical keys.

    Comments (``#``-prefixed) and blank lines are skipped silently.
    Every non-skipped line is split on ``:`` per ``_BASELINE_FIELD_COUNTS``;
    the first field is the kind, the trailing field is the reason
    (which may itself contain colons), and the joined middle fields
    form the canonical key returned to the caller.

    Raises:
        ValueError: If a line carries an unknown kind, has too few
            fields for its kind, or has a whitespace-only reason.
    """
    if not path.exists():
        return set()
    keys: set[str] = set()
    with path.open(encoding="utf-8") as fp:
        for raw_line in fp:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            kind = line.split(":", 1)[0]
            if kind not in _BASELINE_FIELD_COUNTS:
                msg = f"unknown baseline kind: {kind!r} in line {line!r}"
                raise ValueError(msg)
            expected = _BASELINE_FIELD_COUNTS[kind]
            fields = line.split(":", expected - 1)
            if len(fields) < expected:
                msg = f"too few fields ({len(fields)} < {expected}): {line!r}"
                raise ValueError(msg)
            reason = fields[-1].strip()
            if not reason:
                msg = f"empty reason field in baseline line: {line!r}"
                raise ValueError(msg)
            key = ":".join(fields[:-1])
            keys.add(key)
    return keys


def write_baseline(path: Path, findings: list[str], reason: str) -> None:
    """Write a fresh baseline file from *findings*, applying *reason* to all entries.

    The header carries pointers to the schema files and the audit cluster
    that motivated the gate so future readers can reconstruct the why.
    The operator should hand-edit per-entry reasons after generation.
    """
    header = (
        "# Frozen baseline of intentional SQLite <-> Postgres schema drift.\n"
        "# Each non-comment line is `<kind>:<key fields>:<reason>` where the\n"
        "# trailing reason field is required and must be non-empty.\n"
        "#\n"
        "# scripts/check_schema_drift.py reads this file to suppress\n"
        "# findings at these exact entries. New findings NOT in this list\n"
        "# fail the pre-push hook.\n"
        "#\n"
        "# Regenerate (rare; requires explicit user approval) with:\n"
        "#   uv run python scripts/check_schema_drift.py --update-baseline\n"
        "#\n"
        "# Per #1750 / audit cluster #8 (2026-05-03).\n"
    )
    body = "\n".join(f"{key}:{reason}" for key in sorted(findings))
    path.write_text(f"{header}{body}\n", encoding="utf-8")


# ── CLI + main ──────────────────────────────────────────────────


def _build_argparser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-schema",
        type=Path,
        default=_DEFAULT_SQLITE_SCHEMA,
        help="Path to the SQLite schema.sql.",
    )
    parser.add_argument(
        "--postgres-schema",
        type=Path,
        default=_DEFAULT_POSTGRES_SCHEMA,
        help="Path to the Postgres schema.sql.",
    )
    parser.add_argument(
        "--sqlite-revisions",
        type=Path,
        default=_DEFAULT_SQLITE_REVISIONS,
        help="Path to the SQLite revisions/ directory.",
    )
    parser.add_argument(
        "--postgres-revisions",
        type=Path,
        default=_DEFAULT_POSTGRES_REVISIONS,
        help="Path to the Postgres revisions/ directory.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=_DEFAULT_BASELINE,
        help="Path to the baseline file.",
    )
    parser.add_argument(
        "--skip-migrations",
        action="store_true",
        help="Skip the migration-parity sub-check (used by unit tests).",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Rewrite the baseline file from current findings. "
            "REQUIRES EXPLICIT USER APPROVAL TO COMMIT."
        ),
    )
    parser.add_argument(
        "--explain",
        type=str,
        default=None,
        metavar="TABLE",
        help="Print the parsed column inventory for a single table and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Exit codes:
        0 -- no new drift (current findings ⊆ baseline).
        1 -- new drift detected; baseline needs an explicit update or
             the schemas need to be brought back into parity.
        2 -- baseline self-check failed (malformed line / empty reason).
    """
    args = _build_argparser().parse_args(argv)
    sqlite_text = args.sqlite_schema.read_text(encoding="utf-8")
    postgres_text = args.postgres_schema.read_text(encoding="utf-8")
    sqlite_tables, sqlite_indexes = parse_schema(sqlite_text, dialect="sqlite")
    postgres_tables, postgres_indexes = parse_schema(postgres_text, dialect="postgres")
    if args.explain:
        return _explain_table(args.explain, sqlite_tables, postgres_tables)
    findings = diff_schemas(
        sqlite_tables,
        sqlite_indexes,
        postgres_tables,
        postgres_indexes,
    )
    if not args.skip_migrations:
        findings.extend(diff_migrations(args.sqlite_revisions, args.postgres_revisions))
    findings_set = set(findings)
    if args.update_baseline:
        write_baseline(
            args.baseline,
            sorted(findings_set),
            reason="auto-generated; replace with audit-cited justification before commit",
        )
        print(
            f"wrote {len(findings_set)} entries to {args.baseline}",
            file=sys.stderr,
        )
        return 0
    try:
        baseline_keys = load_baseline(args.baseline)
    except ValueError as exc:
        print(f"baseline self-check failed: {exc}", file=sys.stderr)
        return 2
    new_drift = sorted(findings_set - baseline_keys)
    stale_baseline = sorted(baseline_keys - findings_set)
    for key in stale_baseline:
        print(
            f"WARNING: baseline entry no longer detected (drift resolved?): {key}",
            file=sys.stderr,
        )
    for key in new_drift:
        print(f"DRIFT: {key}", file=sys.stderr)
    if new_drift:
        print(
            f"\n{len(new_drift)} new schema-drift finding(s). "
            "Either fix the schema (preferred) or "
            "regenerate the baseline with --update-baseline + a per-entry "
            "justification (requires explicit user approval).",
            file=sys.stderr,
        )
        return 1
    return 0


def _explain_table(
    table_name: str,
    sqlite_tables: dict[str, NormalizedTable],
    postgres_tables: dict[str, NormalizedTable],
) -> int:
    """Pretty-print the parsed column inventory for *table_name*."""
    print(f"--- table {table_name!r} ---")
    for label, tables in (("sqlite", sqlite_tables), ("postgres", postgres_tables)):
        table = tables.get(table_name)
        if table is None:
            print(f"  {label}: <missing>")
            continue
        print(f"  {label}:")
        for col_name in sorted(table.columns):
            col = table.columns[col_name]
            print(
                f"    {col_name:30s}  raw={col.raw_type:24s}  "
                f"canonical={col.canonical_type.value}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
