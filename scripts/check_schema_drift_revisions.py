#!/usr/bin/env python3
"""CI gate: declared ``schema.sql`` matches accumulated ``revisions/*.sql``.

For one backend at a time:

1. Apply every revision under ``src/synthorg/persistence/<backend>/revisions/``
   to a throwaway database via :mod:`synthorg.persistence.migrations` (yoyo).
2. Dump the resulting schema (``sqlite_master`` for SQLite; ``pg_dump
   --schema-only`` for Postgres).
3. Parse both the dumped schema and the declared ``schema.sql`` via the
   existing :func:`scripts._schema_drift_parser.parse_schema`.
4. Diff tables / columns / indexes via a strict same-backend comparator.
5. Diff triggers / functions via regex extraction (the sqlglot-based
   parser deliberately drops trigger DDL via the ``Command`` fallback).

Exits 0 on parity, non-zero on drift, prints a structured finding list.

Usage::

    python scripts/check_schema_drift_revisions.py --backend sqlite
    python scripts/check_schema_drift_revisions.py --backend postgres

The Postgres arm requires Docker (uses ``testcontainers``); the SQLite
arm runs against a temp file with no external dependency.

Replaces the Atlas ``schema diff --env ci`` step that was paywalled
behind Atlas Pro because of trigger DDL in ``schema.sql``.
"""

import argparse
import asyncio
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _schema_drift_models import (  # type: ignore[import-not-found]
        NormalizedIndex,
        NormalizedTable,
        install_sqlglot_filter,
    )
    from _schema_drift_parser import parse_schema  # type: ignore[import-not-found]
else:
    from scripts._schema_drift_models import (
        NormalizedIndex,
        NormalizedTable,
        install_sqlglot_filter,
    )
    from scripts._schema_drift_parser import parse_schema

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from synthorg.persistence import migrations

install_sqlglot_filter()

BackendName = Literal["sqlite", "postgres"]

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_SCHEMA_PATHS: Final[dict[str, Path]] = {
    "sqlite": _REPO_ROOT / "src" / "synthorg" / "persistence" / "sqlite" / "schema.sql",
    "postgres": (
        _REPO_ROOT / "src" / "synthorg" / "persistence" / "postgres" / "schema.sql"
    ),
}
_REVISION_PATHS: Final[dict[str, Path]] = {
    "sqlite": (
        _REPO_ROOT / "src" / "synthorg" / "persistence" / "sqlite" / "revisions"
    ),
    "postgres": (
        _REPO_ROOT / "src" / "synthorg" / "persistence" / "postgres" / "revisions"
    ),
}

_POSTGRES_TESTCONTAINER_IMAGE: Final[str] = "postgres:18-alpine"
_POSTGRES_DUMP_PRELUDE_LINES: Final[tuple[str, ...]] = (
    "SET ",
    "SELECT pg_catalog.set_config",
    "--",
    "",
)
"""``pg_dump`` output lines we strip before feeding to sqlglot.

The prelude is environmental noise (search_path / row_security / etc.)
that the declared ``schema.sql`` does not contain.  Stripping it keeps
the parser focused on actual schema.
"""

_TRIGGER_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"CREATE\s+(?:CONSTRAINT\s+)?TRIGGER\s+(\w+)\b",
    re.IGNORECASE,
)
_FUNCTION_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(\w+)\s*\(",
    re.IGNORECASE,
)
_WHITESPACE_RUN: Final[re.Pattern[str]] = re.compile(r"\s+")


@dataclass(frozen=True)
class _TriggerOrFunction:
    """A name + normalised body extracted from raw SQL."""

    kind: str
    name: str
    body_normalised: str


# ── Schema dump helpers ─────────────────────────────────────────


async def _dump_sqlite_schema(revisions_path: Path) -> str:
    """Apply revisions to a temp SQLite DB and return its schema dump."""
    with tempfile.TemporaryDirectory(prefix="drift-sqlite-") as tmp:
        db_path = Path(tmp) / "drift.db"
        url = migrations.to_sqlite_url(str(db_path))
        await migrations.migrate_apply(url, revisions_path=revisions_path)
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type IN ('table', 'index', 'trigger', 'view') "
                "AND sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE '_yoyo%' "
                "AND name NOT LIKE 'yoyo%' "
                "ORDER BY type, name"
            ).fetchall()
        finally:
            conn.close()
    return ";\n".join(row[0] for row in rows) + ";\n"


async def _dump_postgres_schema(revisions_path: Path) -> str:
    """Apply revisions to a Postgres testcontainer and return its schema dump."""
    try:
        from testcontainers.postgres import (
            PostgresContainer,  # type: ignore[import-untyped]
        )
    except ImportError as exc:
        msg = (
            "testcontainers is required for the Postgres drift gate; "
            "install via the test dependency group: "
            "uv sync --group test"
        )
        raise SystemExit(msg) from exc

    with PostgresContainer(_POSTGRES_TESTCONTAINER_IMAGE) as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        user = pg.username
        password = pg.password
        dbname = pg.dbname
        url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{dbname}"
        await migrations.migrate_apply(url, revisions_path=revisions_path)
        dump = await asyncio.to_thread(
            _run_pg_dump, pg.get_wrapped_container().id, user, dbname
        )
    return _strip_postgres_dump_prelude(dump)


def _run_pg_dump(container_id: str, user: str, dbname: str) -> str:
    """Invoke ``pg_dump`` inside the running Postgres testcontainer."""
    return subprocess.run(
        [
            "docker",
            "exec",
            container_id,
            "pg_dump",
            "--schema-only",
            "--no-owner",
            "--no-acl",
            "--no-comments",
            "-U",
            user,
            "-d",
            dbname,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _strip_postgres_dump_prelude(dump: str) -> str:
    """Drop ``SET`` / ``set_config`` / comment / blank lines from a pg_dump."""
    kept_lines = [
        line
        for line in dump.splitlines()
        if not any(
            line.lstrip().startswith(prefix) for prefix in _POSTGRES_DUMP_PRELUDE_LINES
        )
    ]
    return "\n".join(kept_lines) + "\n"


# ── Trigger / function regex extraction ────────────────────────


def _extract_triggers_and_functions(sql_text: str) -> dict[str, _TriggerOrFunction]:
    """Pull ``CREATE TRIGGER`` and ``CREATE FUNCTION`` blocks by name.

    Each statement is taken as the text from ``CREATE`` up to the
    first standalone semicolon at the top level.  For Postgres
    ``$$...$$`` function bodies, the dollar-quoted block is consumed
    in one piece so embedded semicolons do not terminate the
    statement early.

    Body text is normalised by collapsing whitespace runs to single
    spaces and stripping leading / trailing whitespace, so
    cosmetic-only differences (different indentation in
    ``schema.sql`` vs ``pg_dump`` output) do not register as drift.
    """
    findings: dict[str, _TriggerOrFunction] = {}
    statements = _split_top_level_statements(sql_text)
    for stmt in statements:
        upper = stmt.lstrip().upper()
        if upper.startswith(("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")):
            match = _FUNCTION_NAME_PATTERN.search(stmt)
            if match is None:
                continue
            name = match.group(1)
            findings[f"function:{name}"] = _TriggerOrFunction(
                kind="function",
                name=name,
                body_normalised=_normalise_whitespace(stmt),
            )
        elif upper.startswith(("CREATE TRIGGER", "CREATE CONSTRAINT TRIGGER")):
            match = _TRIGGER_NAME_PATTERN.search(stmt)
            if match is None:
                continue
            name = match.group(1)
            findings[f"trigger:{name}"] = _TriggerOrFunction(
                kind="trigger",
                name=name,
                body_normalised=_normalise_whitespace(stmt),
            )
    return findings


def _split_top_level_statements(sql_text: str) -> list[str]:
    """Split *sql_text* on top-level ``;`` boundaries, respecting ``$$`` quoting."""
    statements: list[str] = []
    buf: list[str] = []
    in_dollar = False
    i = 0
    text = sql_text
    while i < len(text):
        ch = text[i]
        if not in_dollar and text.startswith("$$", i):
            in_dollar = True
            buf.append("$$")
            i += 2
            continue
        if in_dollar and text.startswith("$$", i):
            in_dollar = False
            buf.append("$$")
            i += 2
            continue
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _normalise_whitespace(text: str) -> str:
    """Collapse all whitespace runs (spaces, tabs, newlines) to a single space."""
    return _WHITESPACE_RUN.sub(" ", text).strip()


# ── Diff layer (strict same-backend) ───────────────────────────


def _diff_tables(
    declared: dict[str, NormalizedTable],
    actual: dict[str, NormalizedTable],
) -> list[str]:
    """Return per-table drift findings using strict equality."""
    findings: list[str] = []
    declared_names = set(declared)
    actual_names = set(actual)
    findings.extend(
        f"table:{name}:missing_from_revisions"
        for name in sorted(declared_names - actual_names)
    )
    findings.extend(
        f"table:{name}:missing_from_declared"
        for name in sorted(actual_names - declared_names)
    )
    for name in sorted(declared_names & actual_names):
        d = declared[name]
        a = actual[name]
        d_cols = set(d.columns)
        a_cols = set(a.columns)
        findings.extend(
            f"column:{name}.{col}:declared({d.columns[col].raw_type}):missing_from_revisions"
            for col in sorted(d_cols - a_cols)
        )
        findings.extend(
            f"column:{name}.{col}:revisions({a.columns[col].raw_type}):missing_from_declared"
            for col in sorted(a_cols - d_cols)
        )
        for col in sorted(d_cols & a_cols):
            d_col = d.columns[col]
            a_col = a.columns[col]
            if d_col.canonical_type != a_col.canonical_type:
                findings.append(
                    f"column:{name}.{col}:type:declared({d_col.raw_type}):revisions({a_col.raw_type})"
                )
            if d_col.nullable != a_col.nullable:
                findings.append(
                    f"column:{name}.{col}:nullable:declared({d_col.nullable}):revisions({a_col.nullable})"
                )
        if d.primary_key != a.primary_key:
            findings.append(
                f"pk:{name}:declared({','.join(d.primary_key) or '_'}):revisions({','.join(a.primary_key) or '_'})"
            )
    return findings


def _diff_indexes(
    declared: dict[str, NormalizedIndex],
    actual: dict[str, NormalizedIndex],
) -> list[str]:
    """Return per-index drift findings using strict equality."""
    findings: list[str] = []
    findings.extend(
        f"index:{name}:missing_from_revisions"
        for name in sorted(set(declared) - set(actual))
    )
    findings.extend(
        f"index:{name}:missing_from_declared"
        for name in sorted(set(actual) - set(declared))
    )
    for name in sorted(set(declared) & set(actual)):
        d = declared[name]
        a = actual[name]
        if d.columns != a.columns:
            findings.append(
                f"index_cols:{name}:declared({','.join(d.columns)}):revisions({','.join(a.columns)})"
            )
        if d.unique != a.unique:
            findings.append(
                f"index_unique:{name}:declared({d.unique}):revisions({a.unique})"
            )
    return findings


def _diff_triggers(
    declared: dict[str, _TriggerOrFunction],
    actual: dict[str, _TriggerOrFunction],
) -> list[str]:
    """Return per-trigger / per-function drift findings.

    Compares by normalised body text.  The aim is structural equality;
    cosmetic whitespace differences are tolerated.
    """
    findings: list[str] = []
    findings.extend(
        f"{key}:missing_from_revisions" for key in sorted(set(declared) - set(actual))
    )
    findings.extend(
        f"{key}:missing_from_declared" for key in sorted(set(actual) - set(declared))
    )
    findings.extend(
        f"{key}:body_diff"
        for key in sorted(set(declared) & set(actual))
        if declared[key].body_normalised != actual[key].body_normalised
    )
    return findings


# ── Entry point ────────────────────────────────────────────────


async def _main(backend: BackendName) -> int:
    schema_path = _SCHEMA_PATHS[backend]
    revisions_path = _REVISION_PATHS[backend]
    if not schema_path.is_file():
        print(f"declared schema not found: {schema_path}", file=sys.stderr)
        return 2
    if not revisions_path.is_dir():
        print(f"revisions dir not found: {revisions_path}", file=sys.stderr)
        return 2

    declared_sql = schema_path.read_text(encoding="utf-8")
    if backend == "sqlite":
        actual_sql = await _dump_sqlite_schema(revisions_path)
    else:
        actual_sql = await _dump_postgres_schema(revisions_path)

    declared_tables, declared_indexes = parse_schema(declared_sql, backend)
    actual_tables, actual_indexes = parse_schema(actual_sql, backend)
    declared_triggers = _extract_triggers_and_functions(declared_sql)
    actual_triggers = _extract_triggers_and_functions(actual_sql)

    findings = (
        _diff_tables(declared_tables, actual_tables)
        + _diff_indexes(declared_indexes, actual_indexes)
        + _diff_triggers(declared_triggers, actual_triggers)
    )

    if not findings:
        print(f"OK: {backend} declared schema matches accumulated revisions.")
        return 0

    print(f"DRIFT: {backend} declared schema does not match accumulated revisions.")
    print(f"  declared: {schema_path}")
    print(f"  revisions: {revisions_path}")
    print(f"  {len(findings)} finding(s):")
    for f in findings:
        print(f"    - {f}")
    return 1


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=["sqlite", "postgres"],
        required=True,
        help="Which backend's schema + revisions to check.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args + run the drift check for one backend."""
    args = _build_argparser().parse_args(argv)
    return asyncio.run(_main(args.backend))


if __name__ == "__main__":
    sys.exit(main())
