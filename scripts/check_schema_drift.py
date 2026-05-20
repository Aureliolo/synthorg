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

Coverage by finding kind:

- ``column``: per-column type drift after type-equivalence normalisation.
- ``nullable``: per-column NOT NULL constraint drift.
- ``pk``: PRIMARY KEY column-list drift.
- ``unique``: one-sided UNIQUE constraint drift (named or inline).
- ``index``: one-sided index drift.
- ``index_columns``: shared-name index with different indexed columns.
- ``index_attr``: shared-name index with different ``unique`` / ``where``
  / ``using`` shape.
- ``table``: one-sided table drift.
- ``migration``: one-sided migration filename suffix.

Type-equivalence table (SQLite <-> Postgres):

- TEXT <-> TEXT, VARCHAR, CHAR (default).
- INTEGER <-> BIGINT, INT, SMALLINT, BIGSERIAL, SERIAL (default).
- INTEGER (with CHECK col IN (0, 1)) <-> BOOLEAN (default).
  The CHECK can be either a column-level constraint OR a table-level
  check expression that references the column.
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
- Table-level ``CHECK`` predicates other than the boolean idiom: the
  same predicate renders differently across dialects, and the
  conformance suite catches semantic mismatches behaviourally.
- ``FOREIGN KEY`` / ``REFERENCES`` action clauses: column-level vs
  table-level FK forms produce different ASTs that are hard to
  canonicalise; conformance tests catch real divergence.

Usage::

    python scripts/check_schema_drift.py
    python scripts/check_schema_drift.py --update-baseline
    python scripts/check_schema_drift.py --explain tasks

Implementation is split across sibling private modules to stay under
the 800-line per-file ceiling (CLAUDE.md):

- ``_schema_drift_models``: dataclasses, type families, baseline format.
- ``_schema_drift_parser``: sqlglot parse + per-column / per-index
  normalisation + boolean-via-CHECK detection.
- ``_schema_drift_compare``: diff layer + migration parity.
- ``_schema_drift_baseline``: baseline file load + write.
"""

import argparse
import sys
from pathlib import Path
from typing import Final

from sqlglot.errors import ParseError, TokenError

# Sibling-import dance (mirrors ``scripts/check_setting_to_startup_trace.py``).
# Standalone CLI invocation needs the script's parent directory on
# sys.path; package-style invocation (e.g. tests loading via importlib)
# uses the ``scripts.`` prefix instead.
if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _schema_drift_baseline import (  # type: ignore[import-not-found]
        load_baseline,
        load_baseline_with_reasons,
        write_baseline,
    )
    from _schema_drift_compare import (  # type: ignore[import-not-found]
        diff_migrations,
        diff_schemas,
    )
    from _schema_drift_models import (  # type: ignore[import-not-found]
        DEFAULT_BASELINE,
        DEFAULT_POSTGRES_REVISIONS,
        DEFAULT_POSTGRES_SCHEMA,
        DEFAULT_SQLITE_REVISIONS,
        DEFAULT_SQLITE_SCHEMA,
        NormalizedIndex,
        NormalizedTable,
        install_sqlglot_filter,
        yn,
    )
    from _schema_drift_parser import parse_schema  # type: ignore[import-not-found]
else:  # package-style invocation (e.g. ``import scripts.check_schema_drift``).
    from scripts._schema_drift_baseline import (
        load_baseline,
        load_baseline_with_reasons,
        write_baseline,
    )
    from scripts._schema_drift_compare import diff_migrations, diff_schemas
    from scripts._schema_drift_models import (
        DEFAULT_BASELINE,
        DEFAULT_POSTGRES_REVISIONS,
        DEFAULT_POSTGRES_SCHEMA,
        DEFAULT_SQLITE_REVISIONS,
        DEFAULT_SQLITE_SCHEMA,
        NormalizedIndex,
        NormalizedTable,
        install_sqlglot_filter,
        yn,
    )
    from scripts._schema_drift_parser import parse_schema


install_sqlglot_filter()

# Re-export the public API so the unit-test module
# ``tests/unit/scripts/test_check_schema_drift.py`` can keep using
# ``_MODULE.parse_schema`` etc. without learning about the split.
__all__ = (
    "diff_migrations",
    "diff_schemas",
    "load_baseline",
    "main",
    "parse_schema",
    "write_baseline",
)


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _add_path_args(parser)
    _add_mode_args(parser)
    return parser


def _add_path_args(parser: argparse.ArgumentParser) -> None:
    """Register the schema/revisions/baseline path arguments."""
    parser.add_argument(
        "--sqlite-schema",
        type=Path,
        default=DEFAULT_SQLITE_SCHEMA,
        help="Path to the SQLite schema.sql.",
    )
    parser.add_argument(
        "--postgres-schema",
        type=Path,
        default=DEFAULT_POSTGRES_SCHEMA,
        help="Path to the Postgres schema.sql.",
    )
    parser.add_argument(
        "--sqlite-revisions",
        type=Path,
        default=DEFAULT_SQLITE_REVISIONS,
        help="Path to the SQLite revisions/ directory.",
    )
    parser.add_argument(
        "--postgres-revisions",
        type=Path,
        default=DEFAULT_POSTGRES_REVISIONS,
        help="Path to the Postgres revisions/ directory.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="Path to the baseline file.",
    )


def _add_mode_args(parser: argparse.ArgumentParser) -> None:
    """Register the gate-mode arguments (skip / update / explain)."""
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
        "--justification",
        type=str,
        default=None,
        metavar="TEXT",
        help=(
            "Audit-cited justification applied to every newly registered "
            "drift entry AND any existing entry still holding the placeholder, "
            "so --update-baseline never leaves an unresolvable placeholder "
            "behind. Without it, new entries fall back to the placeholder."
        ),
    )
    parser.add_argument(
        "--explain",
        type=str,
        default=None,
        metavar="TABLE",
        help="Print the parsed column inventory for a single table and exit.",
    )


def _read_schemas(
    sqlite_path: Path,
    postgres_path: Path,
) -> tuple[str, str] | int:
    """Read both schema files; return their text or an exit code."""
    try:
        sqlite_text = sqlite_path.read_text(encoding="utf-8")
        postgres_text = postgres_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        print(f"schema file not found: {exc.filename}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"failed to read schema file: {exc}", file=sys.stderr)
        return 2
    return sqlite_text, postgres_text


def _parse_both(
    sqlite_text: str,
    postgres_text: str,
) -> (
    tuple[
        dict[str, NormalizedTable],
        dict[str, NormalizedIndex],
        dict[str, NormalizedTable],
        dict[str, NormalizedIndex],
    ]
    | int
):
    """Parse both schema texts; return parsed state or an exit code on failure."""
    try:
        sqlite_tables, sqlite_indexes = parse_schema(sqlite_text, dialect="sqlite")
        postgres_tables, postgres_indexes = parse_schema(
            postgres_text, dialect="postgres"
        )
    except (ParseError, TokenError) as exc:
        print(f"schema parsing failed: {exc}", file=sys.stderr)
        return 2
    return sqlite_tables, sqlite_indexes, postgres_tables, postgres_indexes


_NEW_DRIFT_REMEDIATION: Final[str] = (
    "Either fix the schema (preferred) or regenerate the baseline with "
    '--update-baseline --justification "<audit-cited reason>" '
    "(requires explicit user approval)."
)
_PLACEHOLDER_BASELINE_REASON: Final[str] = (
    "auto-generated; replace with audit-cited justification before commit"
)


def _report_findings(new_drift: list[str], stale_baseline: list[str]) -> int:
    """Print stale + new drift; return exit code (0 ok / 1 new drift)."""
    for key in stale_baseline:
        print(
            f"WARNING: baseline entry no longer detected (drift resolved?): {key}",
            file=sys.stderr,
        )
    for key in new_drift:
        print(f"DRIFT: {key}", file=sys.stderr)
    if not new_drift:
        return 0
    print(
        f"\n{len(new_drift)} new schema-drift finding(s). {_NEW_DRIFT_REMEDIATION}",
        file=sys.stderr,
    )
    return 1


def _do_update_baseline(
    baseline_path: Path,
    findings_set: set[str],
    justification: str | None = None,
) -> int:
    """Persist the baseline file; return exit code.

    Existing reasons are preserved across regeneration: keys still in
    ``findings_set`` keep whatever reason the operator already wrote;
    new keys get ``justification`` when supplied, else the placeholder
    reason. When ``justification`` is given it also backfills any
    existing entry still holding the placeholder, so a single
    operator-approved run never leaves an unresolvable placeholder
    behind (the gap that made placeholders un-fixable for non-human
    authors).
    """
    try:
        existing_reasons = load_baseline_with_reasons(baseline_path)
    except ValueError, OSError:
        existing_reasons = {}
    default_reason = justification or _PLACEHOLDER_BASELINE_REASON
    reasons = dict(existing_reasons)
    if justification is not None:
        for key, reason in existing_reasons.items():
            if reason == _PLACEHOLDER_BASELINE_REASON:
                reasons[key] = justification
    try:
        write_baseline(
            baseline_path,
            sorted(findings_set),
            default_reason=default_reason,
            reasons=reasons,
        )
    except OSError as exc:
        print(
            f"failed to write baseline: {exc}. "
            f"Ensure the parent directory exists: {baseline_path.parent}",
            file=sys.stderr,
        )
        return 2
    print(
        f"wrote {len(findings_set)} entries to {baseline_path}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Exit codes:
        0 -- no new drift (current findings ⊆ baseline).
        1 -- new drift detected.
        2 -- input error (file missing / parse failure / malformed
             baseline / write failure).
    """
    args = _build_argparser().parse_args(argv)
    schema_texts = _read_schemas(args.sqlite_schema, args.postgres_schema)
    if isinstance(schema_texts, int):
        return schema_texts
    parsed = _parse_both(*schema_texts)
    if isinstance(parsed, int):
        return parsed
    sqlite_tables, sqlite_indexes, postgres_tables, postgres_indexes = parsed
    if args.explain:
        return _explain_table(args.explain, sqlite_tables, postgres_tables)
    findings = diff_schemas(
        sqlite_tables, sqlite_indexes, postgres_tables, postgres_indexes
    )
    if not args.skip_migrations:
        findings.extend(diff_migrations(args.sqlite_revisions, args.postgres_revisions))
    findings_set = set(findings)
    if args.update_baseline:
        return _do_update_baseline(args.baseline, findings_set, args.justification)
    try:
        baseline_keys = load_baseline(args.baseline)
    except ValueError as exc:
        print(f"baseline self-check failed: {exc}", file=sys.stderr)
        return 2
    return _report_findings(
        sorted(findings_set - baseline_keys),
        sorted(baseline_keys - findings_set),
    )


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
        print(
            f"  {label}: pk=({','.join(table.primary_key) or '_'})  "
            f"uniques={sorted(table.uniques)}"
        )
        for col_name, col in sorted(table.columns.items()):
            print(
                f"    {col_name:30s}  raw={col.raw_type:24s}  "
                f"canonical={col.canonical_type.value:12s}  "
                f"nullable={yn(nullable=col.nullable)}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
