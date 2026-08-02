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
Exit codes: 0 parity, 1 drift, 2 missing input file, 3 the Postgres
throwaway container could not be provisioned. 3 is split out from 1 so a
caller can retry a transient registry / daemon failure without retrying
a deterministic drift finding.

Usage::

    python scripts/check_schema_drift_revisions.py --backend sqlite
    python scripts/check_schema_drift_revisions.py --backend postgres
    python scripts/check_schema_drift_revisions.py --backend postgres --postgres-image synthorg-postgres-ci:18-alpine

The Postgres arm requires Docker (uses ``testcontainers``); the SQLite
arm runs against a temp file with no external dependency.
``--postgres-image`` points the throwaway container at an image already
present locally, which is how CI feeds in a pre-pulled one instead of
reaching a registry a second time; it defaults to the digest-pinned
reference and is rejected outright with ``--backend sqlite``, which has
no container to configure.
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

# Digest-pinned for supply-chain integrity; mirrors the pin in the
# start-postgres composite action so the drift gate and the test
# suites validate against the same Postgres image bytes. Whole ref on one
# line (not a digest split across two literals) with the marker directly
# above it, so the renovate.json custom manager matches it and bumps all
# three copies in the same PR.
_POSTGRES_TESTCONTAINER_IMAGE: Final[str] = (
    # renovate: datasource=docker depName=postgres
    "postgres:18-alpine@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"
)
_PROVISION_EXIT_CODE: Final[int] = 3
"""Exit code for "the throwaway Postgres container never came up".

Distinct from the drift code (1) so a caller can retry the transient
half of the gate. Pulling the image reaches Docker Hub at job runtime,
which times out often enough to fail the gate on its own; conflating
that with a drift finding forces a caller to choose between retrying a
deterministic failure three times or never retrying a blip at all.
"""


class SchemaDriftProvisionError(Exception):
    """The Postgres throwaway container could not be started."""


class SchemaDriftParseError(Exception):
    """A statement this gate must compare could not be identified.

    Distinct from finding drift: it means the comparison never happened
    for that object. Raised rather than skipped because the two sides are
    parsed by the same regex, so a silent miss removes the object from
    both and leaves nothing for the diff to notice.
    """


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


async def _dump_postgres_schema(revisions_path: Path, postgres_image: str) -> str:
    """Apply revisions to a Postgres testcontainer and return its schema dump."""
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError as exc:
        msg = (
            "testcontainers is required for the Postgres drift gate; "
            "install via the test dependency group: "
            "uv sync --group test"
        )
        raise SystemExit(msg) from exc

    pg = PostgresContainer(postgres_image)
    # Inside the cleanup-owning try so a start that fails AFTER Docker
    # created the container still reaches ``stop()``; a leaked container
    # would otherwise accumulate across the caller's provisioning retries.
    # The catch stays scoped to ``start()`` alone: widening it would fold a
    # migration or pg_dump failure into the retryable exit code and let a
    # real defect be retried away as a registry blip.
    try:
        try:
            pg.start()
        except Exception as exc:
            msg = (
                f"could not start the Postgres throwaway container "
                f"({type(exc).__name__}): {exc}"
            )
            raise SchemaDriftProvisionError(msg) from exc
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(_POSTGRES_DEFAULT_PORT)
        user = pg.username
        password = pg.password
        dbname = pg.dbname
        url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{dbname}"
        await migrations.migrate_apply(url, revisions_path=revisions_path)
        wrapped_container = pg.get_wrapped_container()
        if wrapped_container is None or wrapped_container.id is None:
            # Provisioning, not drift: the split exit code exists so a
            # caller can retry infrastructure without retrying a finding.
            msg = "Postgres testcontainer has no id; cannot run pg_dump."
            raise SchemaDriftProvisionError(msg)
        container_id = wrapped_container.id
        dump = await asyncio.to_thread(_run_pg_dump, container_id, user, dbname)
    finally:
        pg.stop()
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
        # A dump that never finished is a provisioning failure worth
        # retrying, not a schema finding to go and read.
        msg = (
            f"pg_dump timed out after {_PG_DUMP_TIMEOUT_SECONDS}s "
            f"against container {container_id}"
        )
        raise SchemaDriftProvisionError(msg) from exc


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
                # This regex is the only thing that sees trigger and function
                # DDL at all (sqlglot drops it through its Command fallback).
                # A name it cannot read would drop the object from BOTH sides
                # of the comparison, so drift in it would be undetectable and
                # nothing would say the check had been skipped.
                msg = f"could not extract a function name from: {body[:120]!r}"
                raise SchemaDriftParseError(msg)
            name = match.group(1)
            findings[f"function:{name}"] = _TriggerOrFunction(
                kind="function",
                name=name,
                body_normalised=_normalise_whitespace(body),
            )
        elif upper.startswith(("CREATE TRIGGER", "CREATE CONSTRAINT TRIGGER")):
            match = _TRIGGER_NAME_PATTERN.search(body)
            if match is None:
                msg = f"could not extract a trigger name from: {body[:120]!r}"
                raise SchemaDriftParseError(msg)
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


def _fold_revisions(revisions_path: Path) -> Path:
    """Concatenate every revision into one synthetic revision, in order.

    yoyo commits once per migration, and each commit is an fsync against a
    real file, so applying the revisions one at a time costs far more in
    transaction overhead than the DDL itself: measured at 27.5s for the
    SQLite set against 1.4s for the same statements applied together, for
    a byte-identical schema dump.

    This is the same trick :func:`_wrap_schema_as_revisions` already plays
    on the declared side, so both halves of the comparison are now built
    the same way. What the gate asserts is the schema the revisions
    *accumulate to*; that each revision also applies cleanly in isolation
    is a migration-correctness property, and it is the real ``migrate_apply``
    in the conformance suite and at app startup that proves it.

    Returns:
        A temp directory holding the single folded revision. The caller
        deletes it.
    """
    folded = Path(tempfile.mkdtemp(prefix="drift-folded-"))
    bodies = [
        path.read_text(encoding="utf-8")
        for path in sorted(revisions_path.glob("*.sql"))
    ]
    (folded / "00000000000000_folded.sql").write_text(
        "\n".join(bodies), encoding="utf-8"
    )
    return folded


async def _dump_via_yoyo(
    backend: BackendName,
    revisions_path: Path,
    postgres_image: str,
) -> str:
    """Apply *revisions_path* to a fresh DB of the right backend, return dump."""
    if backend == "sqlite":
        return await _dump_sqlite_schema(revisions_path)
    return await _dump_postgres_schema(revisions_path, postgres_image)


async def _dump_accumulated(
    backend: BackendName,
    revisions_path: Path,
    postgres_image: str,
) -> str:
    """Dump the schema the revisions accumulate to.

    SQLite folds the revisions into one transaction (see
    :func:`_fold_revisions`). Postgres does not: some DDL there is not
    transactional, so folding could change what actually runs, and that
    arm's cost is dominated by starting a container anyway.
    """
    if backend != "sqlite":
        return await _dump_via_yoyo(backend, revisions_path, postgres_image)
    folded = _fold_revisions(revisions_path)
    try:
        return await _dump_via_yoyo(backend, folded, postgres_image)
    finally:
        shutil.rmtree(folded, ignore_errors=True)


async def _main(backend: BackendName, postgres_image: str) -> int:
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
        declared_sql = await _dump_via_yoyo(backend, declared_tmp, postgres_image)
    finally:
        shutil.rmtree(declared_tmp, ignore_errors=True)
    actual_sql = await _dump_accumulated(backend, revisions_path, postgres_image)

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
    # CI pre-pulls the image through the docker-pull-resilient action, which
    # may serve it from a Docker Hub mirror and can only hand back a plain
    # local tag (a local image cannot be tagged to a digest reference). Taking
    # the reference as an argument lets that already-present tag be used
    # instead of a second, unretried pull of the digest-pinned default.
    parser.add_argument(
        "--postgres-image",
        default=_POSTGRES_TESTCONTAINER_IMAGE,
        help=(
            "Postgres image reference for the throwaway container "
            "(default: the digest-pinned Docker Hub image). "
            "Only meaningful with --backend postgres."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args + run the drift check for one backend."""
    parser = _build_argparser()
    args = parser.parse_args(argv)
    # Refuse rather than ignore: the SQLite arm starts no container, so
    # silently discarding the override would let a caller believe it had
    # pinned an image the run never used.
    if (
        args.backend == "sqlite"
        and args.postgres_image != _POSTGRES_TESTCONTAINER_IMAGE
    ):
        parser.error("--postgres-image is only valid with --backend postgres")
    try:
        return asyncio.run(_main(args.backend, args.postgres_image))
    except SchemaDriftProvisionError as exc:
        # Printed rather than raised so the retryable case reads as one
        # legible line instead of a Docker traceback a caller must parse.
        print(f"PROVISION-FAILED: {exc}", file=sys.stderr)
        return _PROVISION_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
