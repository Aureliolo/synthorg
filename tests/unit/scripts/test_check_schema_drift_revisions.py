"""Tests for the revisions-vs-schema drift gate's revision folding.

The gate applies every revision to a throwaway database and compares the
result against the declared ``schema.sql``. yoyo commits once per
migration and each commit is an fsync, so applying the SQLite set one
file at a time cost 27.5s against 1.4s for the same statements applied
together. Folding is only legitimate if it cannot change the answer, so
the load-bearing test here applies a small revision set both ways and
asserts the resulting schema is identical.
"""

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


# A quoted identifier is legal SQL that the gate's ``\w+`` name group cannot
# read, so these stand in for every spelling the name regexes will not see.
_UNNAMEABLE_FUNCTION = (
    'CREATE FUNCTION "audit trail"() '  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
    "RETURNS trigger AS $$ BEGIN END; $$;"
)
_UNNAMEABLE_TRIGGER = (
    'CREATE TRIGGER "on widget" AFTER INSERT ON widget '  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
    "FOR EACH ROW EXECUTE FUNCTION f();"
)
_READABLE_PAIR = (
    "CREATE FUNCTION touch_widget() "  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
    "RETURNS trigger AS $$ BEGIN END; $$;\n"
    "CREATE TRIGGER widget_touched AFTER UPDATE ON widget "  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
    "FOR EACH ROW EXECUTE FUNCTION touch_widget();\n"
)


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


async def _apply(revisions_path: Path) -> str:
    """Apply *revisions_path* to a throwaway database and dump its schema.

    Awaited from an ``async def`` test rather than wrapped in
    ``asyncio.run``: pytest-asyncio's loop factory is what applies this
    suite's Windows ``SelectorEventLoop`` pin, and it only governs loops it
    creates. A loop opened here directly would take the platform default.

    Returns:
        The resulting schema, as ``CREATE`` statements.
    """
    with tempfile.TemporaryDirectory(prefix="fold-test-") as tmp:
        db = Path(tmp) / "drift.db"
        await migrations.migrate_apply(
            migrations.to_sqlite_url(str(db)), revisions_path=revisions_path
        )
        return _dump(db)


class TestFoldRevisions:
    """Folding must be a pure speed change, never a semantic one."""

    async def test_folded_schema_matches_unfolded(self, tmp_path: Path) -> None:
        # The whole justification for folding, asserted directly.
        source = tmp_path / "revisions"
        _seed(source)
        folded = _MODULE._fold_revisions(source)
        try:
            assert await _apply(folded) == await _apply(source)
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


class TestUnreadableTriggerAndFunctionNames:
    """A name this gate cannot read must stop the run, not vanish.

    The name regex is the only thing that sees trigger and function DDL at
    all, so an object it cannot name drops out of BOTH sides of the
    comparison. Skipping it would report "no drift" for an object that was
    never compared, which is the one outcome worse than a failure.
    """

    def test_an_unreadable_function_name_is_a_parse_error(self) -> None:
        with pytest.raises(_MODULE.SchemaDriftParseError) as caught:
            _MODULE._extract_triggers_and_functions(_UNNAMEABLE_FUNCTION)

        assert "function name" in str(caught.value)
        assert "audit trail" in str(caught.value)

    def test_an_unreadable_trigger_name_is_a_parse_error(self) -> None:
        with pytest.raises(_MODULE.SchemaDriftParseError) as caught:
            _MODULE._extract_triggers_and_functions(_UNNAMEABLE_TRIGGER)

        assert "trigger name" in str(caught.value)
        assert "on widget" in str(caught.value)

    def test_both_gate_failures_share_one_catchable_base(self) -> None:
        # A caller that wants "the gate could not do its job" as distinct
        # from "the gate found drift" has one name to catch, and adding a
        # third failure mode cannot silently escape it.
        assert issubclass(_MODULE.SchemaDriftParseError, _MODULE.SchemaDriftError)
        assert issubclass(_MODULE.SchemaDriftProvisionError, _MODULE.SchemaDriftError)

    def test_a_readable_pair_is_extracted(self) -> None:
        # The negative cases above prove nothing on their own: a matcher
        # that raised unconditionally would satisfy both.
        found = _MODULE._extract_triggers_and_functions(_READABLE_PAIR)

        assert set(found) == {"function:touch_widget", "trigger:widget_touched"}
