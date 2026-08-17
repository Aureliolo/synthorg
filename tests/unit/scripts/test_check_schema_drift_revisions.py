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
from scripts._schema_drift_models import NormalizedTable

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


class TestParseFailureReachesTheCliBoundary:
    """Raising is only half of it; the code a caller reads is the other."""

    def test_a_parse_error_becomes_its_own_exit_code(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Uncaught, this leaves through the interpreter's default handler,
        # which exits 1: the drift code. A caller would then go looking for
        # a finding list that was never produced.
        async def _raise(_backend: str, _image: str) -> int:
            msg = "could not extract a trigger name from: 'CREATE TRIGGER ...'"
            raise _MODULE.SchemaDriftParseError(msg)

        monkeypatch.setattr(_MODULE, "_main", _raise)

        code = _MODULE.main(["--backend", "sqlite"])

        assert code == _MODULE._PARSE_EXIT_CODE
        assert code != _MODULE._PROVISION_EXIT_CODE, "a retry would repeat it"
        assert "PARSE-FAILED" in capsys.readouterr().err

    def test_a_provision_error_keeps_its_own_exit_code(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The retryable branch must stay reachable: one handler swallowing
        # both would make CI retry a deterministic parse failure three times.
        async def _raise(_backend: str, _image: str) -> int:
            msg = "container never came up"
            raise _MODULE.SchemaDriftProvisionError(msg)

        monkeypatch.setattr(_MODULE, "_main", _raise)

        code = _MODULE.main(["--backend", "sqlite"])

        assert code == _MODULE._PROVISION_EXIT_CODE
        assert "PROVISION-FAILED" in capsys.readouterr().err


# The gate builds an EMPTY database, so every fixture below has zero rows.
# That is the point: a row-reporting check has nothing to report here, and
# these are the structural breakages it therefore has to catch some other way.
_RESOLVED = """
CREATE TABLE parent (id TEXT PRIMARY KEY);
CREATE TABLE child (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(id));
"""  # lint-allow: persistence-boundary -- DDL fixture the gate inspects
_DROPPED_PARENT_TABLE = """
CREATE TABLE child (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES gone(id));
"""  # lint-allow: persistence-boundary -- DDL fixture the gate inspects
_RENAMED_PARENT_KEY = """
CREATE TABLE parent (id TEXT PRIMARY KEY);
CREATE TABLE child (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(was_id));
"""  # lint-allow: persistence-boundary -- DDL fixture the gate inspects
_TWO_BROKEN_TABLES = """
CREATE TABLE parent (id TEXT PRIMARY KEY);
CREATE TABLE a_child (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(was_id));
CREATE TABLE z_child (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES also_gone(id));
"""  # lint-allow: persistence-boundary -- DDL fixture the gate inspects


def _schema(ddl: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(ddl)
    return conn


class TestReferencesResolve:
    """An empty database still has to prove its references are structural."""

    def test_a_resolved_schema_passes(self) -> None:
        conn = _schema(_RESOLVED)
        try:
            _MODULE._assert_references_resolve(conn)
        finally:
            conn.close()

    def test_a_dropped_parent_table_is_refused(self) -> None:
        # `PRAGMA foreign_key_check` reports this as no violation at all,
        # because with no rows there is no row to violate anything. Only the
        # declared reference list carries the parent name here.
        conn = _schema(_DROPPED_PARENT_TABLE)
        try:
            with pytest.raises(
                _MODULE.SchemaDriftReferenceError, match="child -> gone"
            ):
                _MODULE._assert_references_resolve(conn)
        finally:
            conn.close()

    def test_a_renamed_parent_key_is_refused_as_the_typed_error(self) -> None:
        # SQLite raises `foreign key mismatch` for this rather than reporting
        # a row, so an unguarded call leaves through OperationalError and the
        # gate reports a crash where it should report a finding.
        conn = _schema(_RENAMED_PARENT_KEY)
        try:
            with pytest.raises(
                _MODULE.SchemaDriftReferenceError, match="foreign key mismatch"
            ):
                _MODULE._assert_references_resolve(conn)
        finally:
            conn.close()

    def test_one_mismatch_does_not_mask_the_next_table(self) -> None:
        # The whole-database pragma aborts on the first mismatch, so a single
        # bad table would hide every table after it and an operator would fix
        # one breakage per CI run.
        conn = _schema(_TWO_BROKEN_TABLES)
        try:
            with pytest.raises(_MODULE.SchemaDriftReferenceError) as caught:
                _MODULE._assert_references_resolve(conn)
        finally:
            conn.close()

        detail = str(caught.value)
        assert "a_child" in detail
        assert "z_child -> also_gone" in detail


def _one_table(sql: str) -> dict[str, NormalizedTable]:
    """Parse one CREATE TABLE the way the gate does.

    Returns:
        The normalised tables, keyed by name.
    """
    tables: dict[str, NormalizedTable] = _MODULE.parse_schema(sql, "sqlite")[0]
    return tables


# Every pair below produces two tables identical in column names, types,
# nullability, keys and indexes, and different in what they refuse, what they
# write when nobody says, or what a delete does. The DDL is this suite's
# subject rather than a query it issues.
_CHECKED = "CREATE TABLE t (id TEXT PRIMARY KEY, reason TEXT CHECK (reason IN ('a', 'b')));"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_UNCHECKED = "CREATE TABLE t (id TEXT PRIMARY KEY, reason TEXT);"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_NARROW_CHECK = "CREATE TABLE t (reason TEXT CHECK (reason IN ('a')));"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_WIDE_CHECK = "CREATE TABLE t (reason TEXT CHECK (reason IN ('a', 'b')));"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_WITH_DEFAULT = "CREATE TABLE t (id TEXT PRIMARY KEY, tally INTEGER DEFAULT 0);"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_NO_DEFAULT = "CREATE TABLE t (id TEXT PRIMARY KEY, tally INTEGER);"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_CASCADING = "CREATE TABLE t (parent TEXT REFERENCES p (id) ON DELETE CASCADE);"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_RESTRICTING = "CREATE TABLE t (parent TEXT REFERENCES p (id) ON DELETE RESTRICT);"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_STATED_NO_ACTION = "CREATE TABLE t (parent TEXT REFERENCES p (id) ON DELETE NO ACTION);"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_UNSTATED_ACTION = "CREATE TABLE t (parent TEXT REFERENCES p (id));"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_NO_REFERENCE = "CREATE TABLE t (parent TEXT);"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
_EVERY_DIMENSION = (
    "CREATE TABLE t ("  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
    "  id TEXT PRIMARY KEY,"
    "  tally INTEGER NOT NULL DEFAULT 0,"
    "  parent TEXT REFERENCES p (id) ON DELETE CASCADE,"
    "  reason TEXT CHECK (reason IN ('a', 'b'))"
    ");"
)
_ALTER_ADDED_KEY = (
    "CREATE TABLE t (id TEXT, reason TEXT CHECK (reason IN ('a')));"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
    "\nALTER TABLE t ADD CONSTRAINT t_pkey PRIMARY KEY (id);"
)
_ALTER_ADDED_REFERENCE = (
    "CREATE TABLE t (parent TEXT);"  # lint-allow: persistence-boundary -- DDL fixture the gate parses  # noqa: E501
    "\nALTER TABLE ONLY t ADD CONSTRAINT t_parent_fkey "
    "FOREIGN KEY (parent) REFERENCES p(id) ON DELETE CASCADE;"
)


class TestTheRebuildDimensions:
    """What a hand-retyped table rebuild loses, and shapes alone cannot see."""

    def test_a_dropped_check_is_drift(self) -> None:
        findings = _MODULE._diff_tables(_one_table(_CHECKED), _one_table(_UNCHECKED))
        assert any("check:t:missing_from_revisions" in f for f in findings)

    def test_a_widened_check_is_drift(self) -> None:
        """Same column, same type, a value the archive now accepts."""
        assert _MODULE._diff_tables(_one_table(_NARROW_CHECK), _one_table(_WIDE_CHECK))

    def test_a_dropped_default_is_drift(self) -> None:
        findings = _MODULE._diff_tables(
            _one_table(_WITH_DEFAULT), _one_table(_NO_DEFAULT)
        )
        assert any("t.tally:default" in f for f in findings)

    def test_a_changed_delete_action_is_drift(self) -> None:
        """The one difference that decides what a delete does."""
        findings = _MODULE._diff_tables(
            _one_table(_CASCADING), _one_table(_RESTRICTING)
        )
        assert any("fk:t.parent:on_delete" in f for f in findings)

    def test_an_unstated_action_reads_as_the_standard_default(self) -> None:
        """So a rebuild that simply drops the clause is caught, not excused."""
        assert (
            _MODULE._diff_tables(
                _one_table(_STATED_NO_ACTION), _one_table(_UNSTATED_ACTION)
            )
            == []
        )

    def test_a_dropped_reference_is_drift(self) -> None:
        findings = _MODULE._diff_tables(
            _one_table(_UNSTATED_ACTION), _one_table(_NO_REFERENCE)
        )
        assert any("fk:t.parent:missing_from_revisions" in f for f in findings)

    def test_an_identical_table_is_not_drift(self) -> None:
        assert (
            _MODULE._diff_tables(
                _one_table(_EVERY_DIMENSION), _one_table(_EVERY_DIMENSION)
            )
            == []
        )

    def test_a_patched_table_keeps_its_checks(self) -> None:
        """A field added to the dataclass must survive the ALTER overlay.

        The overlay rebuilds each table it touches, so a field it forgets is
        silently emptied on BOTH sides and its comparison then always passes.
        """
        patched = _MODULE._patch_constraints_from_alter(
            _one_table(_ALTER_ADDED_KEY), _ALTER_ADDED_KEY
        )
        assert patched["t"].primary_key == ("id",)
        assert patched["t"].checks

    def test_an_alter_added_reference_is_read(self) -> None:
        """Which is the only way Postgres's own dump spells one."""
        patched = _MODULE._patch_constraints_from_alter(
            _one_table(_ALTER_ADDED_REFERENCE), _ALTER_ADDED_REFERENCE
        )
        (foreign_key,) = patched["t"].foreign_keys
        assert foreign_key.columns == ("parent",)
        assert foreign_key.ref_table == "p"
        assert foreign_key.on_delete == "CASCADE"
        assert foreign_key.on_update == _MODULE.NO_ACTION
