"""Tests for the revisions-vs-schema drift gate's revision folding.

The gate applies every revision to a throwaway database and compares the
result against the declared ``schema.sql``. yoyo commits once per
migration and each commit is an fsync, so applying the SQLite set one
file at a time cost 27.5s against 1.4s for the same statements applied
together. Folding is only legitimate if it cannot change the answer, so
the load-bearing test here applies a small revision set both ways and
asserts the resulting schema is identical.
"""

import asyncio
import importlib.util
import shutil
import sqlite3  # lint-allow: persistence-boundary -- reads the schema the gate builds
import tempfile
from pathlib import Path
from types import ModuleType

import pytest

from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    script = _REPO_ROOT / "scripts" / "check_schema_drift_revisions.py"
    spec = importlib.util.spec_from_file_location(
        "_check_schema_drift_revisions", script
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load()

# Deliberately ordered so a fold that sorted wrongly would produce a
# different schema: the third revision alters the table the first created.
# The DDL is this test's subject rather than a query it issues: the gate
# under test folds revision files, so there has to be a revision file.
_REVISIONS = {
    "20260101000000_first.sql": "CREATE TABLE widget (id TEXT PRIMARY KEY);\n",  # lint-allow: persistence-boundary -- migration fixture the gate folds  # noqa: E501
    "20260102000000_second.sql": "CREATE TABLE gadget (id TEXT PRIMARY KEY);\n",  # lint-allow: persistence-boundary -- migration fixture the gate folds  # noqa: E501
    "20260103000000_third.sql": "ALTER TABLE widget ADD COLUMN label TEXT;\n",  # lint-allow: persistence-boundary -- migration fixture the gate folds  # noqa: E501
}


def _seed(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in _REVISIONS.items():
        (directory / name).write_text(body, encoding="utf-8")


def _dump(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND sql IS NOT NULL AND name NOT LIKE '_yoyo%' "
            "AND name NOT LIKE 'yoyo%' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return ";\n".join(row[0] for row in rows)


def _apply(revisions_path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="fold-test-") as tmp:
        db = Path(tmp) / "drift.db"
        asyncio.run(
            migrations.migrate_apply(
                migrations.to_sqlite_url(str(db)), revisions_path=revisions_path
            )
        )
        return _dump(db)


class TestFoldRevisions:
    """Folding must be a pure speed change, never a semantic one."""

    def test_folded_schema_matches_unfolded(self, tmp_path: Path) -> None:
        # The whole justification for folding, asserted directly.
        source = tmp_path / "revisions"
        _seed(source)
        folded = _MODULE._fold_revisions(source)
        try:
            assert _apply(folded) == _apply(source)
        finally:
            shutil.rmtree(folded, ignore_errors=True)

    def test_produces_exactly_one_revision(self, tmp_path: Path) -> None:
        source = tmp_path / "revisions"
        _seed(source)
        folded = _MODULE._fold_revisions(source)
        try:
            assert len(list(folded.glob("*.sql"))) == 1
        finally:
            shutil.rmtree(folded, ignore_errors=True)

    def test_concatenates_in_lexicographic_order(self, tmp_path: Path) -> None:
        # yoyo applies in this order, so a fold that reordered would apply
        # an ALTER before the CREATE it depends on.
        source = tmp_path / "revisions"
        _seed(source)
        folded = _MODULE._fold_revisions(source)
        try:
            body = next(folded.glob("*.sql")).read_text(encoding="utf-8")
        finally:
            shutil.rmtree(folded, ignore_errors=True)
        # Read out of the fixture rather than restated: an assertion that
        # spelled the statements again would keep passing after the fixture
        # changed underneath it.
        positions = [body.index(sql.strip()) for sql in _REVISIONS.values()]
        assert positions == sorted(positions)

    def test_ignores_non_sql_files(self, tmp_path: Path) -> None:
        # The revisions directory carries an __init__.py; sweeping it into
        # the SQL would make the fold unparseable.
        source = tmp_path / "revisions"
        _seed(source)
        (source / "__init__.py").write_text("# package marker\n", encoding="utf-8")
        folded = _MODULE._fold_revisions(source)
        try:
            body = next(folded.glob("*.sql")).read_text(encoding="utf-8")
        finally:
            shutil.rmtree(folded, ignore_errors=True)
        assert "package marker" not in body
