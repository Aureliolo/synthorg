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
5. Diff triggers / functions via regex extraction.  The sqlglot-based
   parser drops trigger DDL through its ``Command`` fallback, so this
   script pulls trigger and function bodies out of the SQL directly.

Exits 0 on parity, non-zero on drift, prints a structured finding list.

Usage::

    python scripts/check_schema_drift_revisions.py --backend sqlite
    python scripts/check_schema_drift_revisions.py --backend postgres

The Postgres arm requires Docker (uses ``testcontainers``); the SQLite
arm runs against a temp file with no external dependency.
"""

import argparse
import asyncio
import re
import shutil
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
_POSTGRES_DEFAULT_PORT: Final[int] = 5432
"""Default Postgres listener port inside the testcontainer.

Hoisted out of the call site so the magic-number gate
(``scripts/check_no_magic_numbers.py``) does not need a literal-5432
allowlist for this script and so the container contract is visible
at the top of the file.
"""

_PG_DUMP_TIMEOUT_SECONDS: Final[int] = 60
"""Wall-clock cap on a single ``pg_dump`` invocation.

A hung ``docker exec ... pg_dump`` (container deadlock, lost network)
would otherwise wait for the job-level CI timeout, which can be tens
of minutes. Bounding it here makes the drift gate fail fast with a
clear ``TimeoutExpired`` cause instead of an opaque CI cancellation.
"""

_DOLLAR_QUOTE_OPEN: Final[re.Pattern[str]] = re.compile(
    r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$"
)
"""Match a Postgres dollar-quote delimiter at the current position.

PostgreSQL dollar quoting accepts both the bare ``$$`` form and a
named ``$tag$`` form where the tag follows identifier rules
(letters / digits / underscores, leading non-digit). The splitter
needs the full delimiter string so the matching close delimiter can
be located verbatim.
"""

_POSTGRES_DUMP_PRELUDE_LINES: Final[tuple[str, ...]] = (
    "SET ",
    "SELECT pg_catalog.set_config",
    "--",
    "\\restrict",
    "\\unrestrict",
    "\\connect",
)
"""``pg_dump`` output lines we strip before feeding to sqlglot.

The prelude is environmental noise (search_path / row_security / etc.)
that the declared ``schema.sql`` does not contain.  Stripping it keeps
the parser focused on actual schema.
"""

_TRIGGER_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"CREATE\s+(?:CONSTRAINT\s+)?TRIGGER\s+(?:\w+\.)?(\w+)\b",
    re.IGNORECASE,
)
_FUNCTION_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:\w+\.)?(\w+)\s*\(",
    re.IGNORECASE,
)
_ALTER_TABLE_ADD_PK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:\w+\.)?(\w+)\s+"
    r"ADD\s+CONSTRAINT\s+\w+\s+PRIMARY\s+KEY\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_ALTER_TABLE_ADD_UNIQUE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:\w+\.)?(\w+)\s+"
    r"ADD\s+CONSTRAINT\s+\w+\s+UNIQUE\s*\(([^)]+)\)",
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
        port = pg.get_exposed_port(_POSTGRES_DEFAULT_PORT)
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
    """Invoke ``pg_dump`` inside the running Postgres testcontainer.

    Wraps ``subprocess.TimeoutExpired`` in a :class:`SystemExit` so the
    drift gate surfaces a clear failure when ``docker exec`` stalls,
    rather than waiting out the job-level CI timeout.
    """
    try:
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
            timeout=_PG_DUMP_TIMEOUT_SECONDS,
        ).stdout
    except subprocess.TimeoutExpired as exc:
        msg = (
            f"pg_dump timed out after {_PG_DUMP_TIMEOUT_SECONDS}s "
            f"against container {container_id}"
        )
        raise SystemExit(msg) from exc


_YOYO_TABLE_PREFIXES: Final[tuple[str, ...]] = ("_yoyo", "yoyo_")


def _drop_yoyo_internals(
    tables: dict[str, NormalizedTable],
) -> dict[str, NormalizedTable]:
    """Filter out yoyo's own bookkeeping tables from a parsed schema."""
    return {
        name: table
        for name, table in tables.items()
        if not name.startswith(_YOYO_TABLE_PREFIXES)
    }


def _drop_yoyo_index_internals(
    indexes: dict[str, NormalizedIndex],
) -> dict[str, NormalizedIndex]:
    """Filter out yoyo's own bookkeeping indexes from a parsed schema."""
    return {
        name: idx
        for name, idx in indexes.items()
        if not idx.table.startswith(_YOYO_TABLE_PREFIXES)
        and not name.startswith(_YOYO_TABLE_PREFIXES)
    }


def _patch_constraints_from_alter(
    tables: dict[str, NormalizedTable],
    sql_text: str,
) -> dict[str, NormalizedTable]:
    """Update parsed *tables* with PK / UNIQUE info from ALTER TABLE statements.

    pg_dump emits ``PRIMARY KEY`` and ``UNIQUE`` constraints as
    ``ALTER TABLE x ADD CONSTRAINT ... PRIMARY KEY (cols)`` separate
    from the original ``CREATE TABLE``.  The shared parser only reads
    inline constraints, so we post-process the raw SQL here to find
    the ALTER additions and overlay them onto the existing
    NormalizedTable entries.

    Returns a fresh dict; the input is not mutated.
    """
    patched = dict(tables)
    for match in _ALTER_TABLE_ADD_PK_PATTERN.finditer(sql_text):
        table_name = match.group(1)
        cols = tuple(c.strip().strip('"') for c in match.group(2).split(","))
        existing = patched.get(table_name)
        if existing is None:
            continue
        if existing.primary_key:
            continue
        patched[table_name] = NormalizedTable(
            name=existing.name,
            columns=existing.columns,
            primary_key=cols,
            uniques=existing.uniques,
        )
    for match in _ALTER_TABLE_ADD_UNIQUE_PATTERN.finditer(sql_text):
        table_name = match.group(1)
        cols = tuple(c.strip().strip('"') for c in match.group(2).split(","))
        existing = patched.get(table_name)
        if existing is None:
            continue
        if cols in existing.uniques:
            continue
        patched[table_name] = NormalizedTable(
            name=existing.name,
            columns=existing.columns,
            primary_key=existing.primary_key,
            uniques=frozenset(existing.uniques | {cols}),
        )
    return patched


def _strip_postgres_dump_prelude(dump: str) -> str:
    """Drop ``SET`` / ``set_config`` / comment / psql-meta / blank lines from a pg_dump."""
    kept_lines = []
    for raw in dump.splitlines():
        stripped = raw.lstrip()
        if not stripped:
            continue
        if any(stripped.startswith(prefix) for prefix in _POSTGRES_DUMP_PRELUDE_LINES):
            continue
        kept_lines.append(raw)
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
        body = _strip_leading_comments(stmt).lstrip()
        upper = body.upper()
        if upper.startswith(("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")):
            match = _FUNCTION_NAME_PATTERN.search(body)
            if match is None:
                continue
            name = match.group(1)
            findings[f"function:{name}"] = _TriggerOrFunction(
                kind="function",
                name=name,
                body_normalised=_normalise_whitespace(body),
            )
        elif upper.startswith(("CREATE TRIGGER", "CREATE CONSTRAINT TRIGGER")):
            match = _TRIGGER_NAME_PATTERN.search(body)
            if match is None:
                continue
            name = match.group(1)
            findings[f"trigger:{name}"] = _TriggerOrFunction(
                kind="trigger",
                name=name,
                body_normalised=_normalise_whitespace(body),
            )
    return findings


def _split_top_level_statements(sql_text: str) -> list[str]:
    """Split *sql_text* on top-level ``;`` boundaries.

    Respects two grouping constructs that legitimately contain
    semicolons in their bodies:

    * Postgres dollar quoting (both ``$$ ... $$`` and tagged
      ``$tag$ ... $tag$`` variants -- a matching close delimiter
      must repeat the exact opening tag).
    * SQLite trigger bodies (``BEGIN ... END;``).

    A semicolon inside either grouping does not terminate the
    enclosing statement; only a top-level semicolon does.
    """
    statements: list[str] = []
    buf: list[str] = []
    current_dollar_tag: str | None = None
    begin_depth = 0
    i = 0
    text = sql_text
    while i < len(text):
        ch = text[i]
        if current_dollar_tag is None:
            open_match = _DOLLAR_QUOTE_OPEN.match(text, i)
            if open_match is not None:
                current_dollar_tag = open_match.group(0)
                buf.append(current_dollar_tag)
                i += len(current_dollar_tag)
                continue
        elif text.startswith(current_dollar_tag, i):
            buf.append(current_dollar_tag)
            i += len(current_dollar_tag)
            current_dollar_tag = None
            continue
        if current_dollar_tag is None and _is_keyword_at(text, i, "BEGIN"):
            begin_depth += 1
            buf.append("BEGIN")
            i += len("BEGIN")
            continue
        if (
            current_dollar_tag is None
            and begin_depth > 0
            and _is_keyword_at(text, i, "END")
        ):
            begin_depth -= 1
            buf.append("END")
            i += len("END")
            continue
        if ch == ";" and current_dollar_tag is None and begin_depth == 0:
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


def _is_keyword_at(text: str, idx: int, keyword: str) -> bool:
    """True iff *keyword* (case-insensitive) starts at *idx* on word boundaries."""
    end = idx + len(keyword)
    if end > len(text):
        return False
    if text[idx:end].upper() != keyword:
        return False
    if idx > 0 and (text[idx - 1].isalnum() or text[idx - 1] == "_"):
        return False
    return not (end < len(text) and (text[end].isalnum() or text[end] == "_"))


def _strip_leading_comments(text: str) -> str:
    """Drop leading whitespace, ``--`` line comments, and ``/* */`` blocks."""
    pos = 0
    n = len(text)
    while pos < n:
        if text[pos].isspace():
            pos += 1
            continue
        if text.startswith("--", pos):
            newline = text.find("\n", pos)
            if newline == -1:
                return ""
            pos = newline + 1
            continue
        if text.startswith("/*", pos):
            close = text.find("*/", pos + 2)
            if close == -1:
                return ""
            pos = close + 2
            continue
        break
    return text[pos:]


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
        # UNIQUE constraints populated by ``_patch_constraints_from_alter``
        # were not being compared. SQLite's ``sql IS NOT NULL`` dump filter
        # hides implicit unique indexes, so without an explicit diff a
        # revision that drops a UNIQUE constraint passes the gate silently.
        d_uniques = sorted(",".join(cols) for cols in d.uniques)
        a_uniques = sorted(",".join(cols) for cols in a.uniques)
        if d_uniques != a_uniques:
            findings.append(
                f"unique:{name}:declared({'|'.join(d_uniques) or '_'}):"
                f"revisions({'|'.join(a_uniques) or '_'})"
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


def _wrap_schema_as_revisions(schema_text: str) -> Path:
    """Write *schema_text* into a fresh tmp dir as a single yoyo revision.

    Returns the temp directory path.  The caller is responsible for
    deleting it (use :func:`shutil.rmtree`).
    """
    import tempfile as _t

    tmp_dir = Path(_t.mkdtemp(prefix="drift-declared-"))
    (tmp_dir / "00000000000000_declared.sql").write_text(schema_text, encoding="utf-8")
    return tmp_dir


async def _dump_via_yoyo(
    backend: BackendName,
    revisions_path: Path,
) -> str:
    """Apply *revisions_path* to a fresh DB of the right backend, return dump."""
    if backend == "sqlite":
        return await _dump_sqlite_schema(revisions_path)
    return await _dump_postgres_schema(revisions_path)


async def _main(backend: BackendName) -> int:
    schema_path = _SCHEMA_PATHS[backend]
    revisions_path = _REVISION_PATHS[backend]
    if not schema_path.is_file():
        print(f"declared schema not found: {schema_path}", file=sys.stderr)
        return 2
    if not revisions_path.is_dir():
        print(f"revisions dir not found: {revisions_path}", file=sys.stderr)
        return 2

    schema_text = schema_path.read_text(encoding="utf-8")

    declared_tmp = _wrap_schema_as_revisions(schema_text)
    try:
        declared_sql = await _dump_via_yoyo(backend, declared_tmp)
    finally:
        shutil.rmtree(declared_tmp, ignore_errors=True)
    actual_sql = await _dump_via_yoyo(backend, revisions_path)

    declared_tables, declared_indexes = parse_schema(declared_sql, backend)
    actual_tables, actual_indexes = parse_schema(actual_sql, backend)
    declared_tables = _drop_yoyo_internals(declared_tables)
    declared_indexes = _drop_yoyo_index_internals(declared_indexes)
    actual_tables = _drop_yoyo_internals(actual_tables)
    actual_indexes = _drop_yoyo_index_internals(actual_indexes)
    declared_tables = _patch_constraints_from_alter(declared_tables, declared_sql)
    actual_tables = _patch_constraints_from_alter(actual_tables, actual_sql)
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
