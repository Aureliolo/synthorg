"""Unit tests for ``scripts/check_schema_drift.py``.

The gate compares ``src/synthorg/persistence/sqlite/schema.sql`` and
``src/synthorg/persistence/postgres/schema.sql`` for structural drift,
and the two ``revisions/`` directories for migration-file parity.  A
shrink-only baseline at ``scripts/schema_drift_baseline.txt`` freezes
intentional drift documented in the schema headers + audit cluster #8.

Tests load the script as a module (mirroring
``test_check_persistence_boundary.py`` / ``test_check_boundary_typed.py``)
and call its public helpers directly so we never spawn a subprocess
that would scan the real source tree.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_schema_drift.py"
_BASELINE_PATH = _REPO_ROOT / "scripts" / "schema_drift_baseline.txt"


def _load_script_module() -> ModuleType:
    """Import the script as a module so its public helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_schema_drift",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


# ── helpers ─────────────────────────────────────────────────────


def _diff(sqlite_sql: str, postgres_sql: str) -> list[str]:
    """Parse both inputs and return the list of canonical drift keys."""
    s_tables, s_indexes = _MODULE.parse_schema(sqlite_sql, dialect="sqlite")
    p_tables, p_indexes = _MODULE.parse_schema(postgres_sql, dialect="postgres")
    findings: list[str] = _MODULE.diff_schemas(s_tables, s_indexes, p_tables, p_indexes)
    return findings


def _write_baseline(tmp_path: Path, lines: list[str]) -> Path:
    """Materialise a baseline file under ``tmp_path`` and return its path."""
    path = tmp_path / "baseline.txt"
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


# ── 1. identical schemas ────────────────────────────────────────


def test_identical_schemas_produce_no_drift() -> None:
    """Two SQL strings expressing the same intent diff to an empty list."""
    sql = "CREATE TABLE t (id TEXT NOT NULL PRIMARY KEY);"
    assert _diff(sql, sql) == []


# ── 2. missing-table drift ──────────────────────────────────────


def test_missing_table_on_postgres_is_flagged() -> None:
    """A table present on sqlite but absent on postgres surfaces as drift.

    The side label answers "exists ONLY on this side" (matching the
    user-spec ``<sqlite_or_postgres_only>`` semantics): a sqlite-only
    table is reported as ``sqlite_only`` even though it is missing
    from postgres.
    """
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY);"
    postgres_sql = ""
    findings = _diff(sqlite_sql, postgres_sql)
    assert findings == ["table:t:sqlite_only"]


def test_missing_table_on_sqlite_is_flagged() -> None:
    """A table present on postgres but absent on sqlite is ``postgres_only``."""
    sqlite_sql = ""
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY);"
    findings = _diff(sqlite_sql, postgres_sql)
    assert findings == ["table:t:postgres_only"]


# ── 3. missing-column drift ─────────────────────────────────────


def test_missing_column_on_one_side_is_flagged() -> None:
    """An extra column on one side without the other lists as drift."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, created_at TIMESTAMPTZ);"
    findings = _diff(sqlite_sql, postgres_sql)
    assert "column:t:created_at:_:TIMESTAMPTZ" in findings


# ── 4. incompatible-type drift ──────────────────────────────────


def test_incompatible_column_type_is_flagged() -> None:
    """SQLite ``INTEGER`` paired with Postgres ``TEXT`` is a hard mismatch."""
    sqlite_sql = "CREATE TABLE t (id INTEGER PRIMARY KEY);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY);"
    findings = _diff(sqlite_sql, postgres_sql)
    assert "column:t:id:INTEGER:TEXT" in findings


# ── 5. baseline-listed drift passes ─────────────────────────────


def test_baselined_drift_passes(tmp_path: Path) -> None:
    """A finding listed in the baseline is suppressed; ``main`` exits 0."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, payload TEXT);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, payload JSONB);"
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    sqlite_path.write_text(sqlite_sql, encoding="utf-8")
    postgres_path.write_text(postgres_sql, encoding="utf-8")
    baseline_path = _write_baseline(
        tmp_path,
        [
            "# header",
            "column:t:payload:TEXT:JSONB:test reason",
        ],
    )
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--baseline",
            str(baseline_path),
            "--skip-migrations",
        ]
    )
    assert rc == 0


# ── 6. new (unbaselined) drift fails ────────────────────────────


def test_new_drift_without_baseline_entry_fails(tmp_path: Path) -> None:
    """A finding absent from the baseline trips the gate (exit 1)."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, payload TEXT);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, payload JSONB);"
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    sqlite_path.write_text(sqlite_sql, encoding="utf-8")
    postgres_path.write_text(postgres_sql, encoding="utf-8")
    baseline_path = _write_baseline(tmp_path, [])
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--baseline",
            str(baseline_path),
            "--skip-migrations",
        ]
    )
    assert rc == 1


# ── 7. JSONB-vs-TEXT diff is detected as drift (gated by baseline) ─


def test_jsonb_vs_text_is_flagged_as_drift() -> None:
    """``TEXT`` ↔ ``JSONB`` is a baseline-only equivalence, not default."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, payload TEXT);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, payload JSONB);"
    findings = _diff(sqlite_sql, postgres_sql)
    assert "column:t:payload:TEXT:JSONB" in findings


def test_text_vs_timestamptz_is_flagged_as_drift() -> None:
    """``TEXT`` ↔ ``TIMESTAMPTZ`` is also baseline-only."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, created_at TEXT);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, created_at TIMESTAMPTZ);"
    findings = _diff(sqlite_sql, postgres_sql)
    assert "column:t:created_at:TEXT:TIMESTAMPTZ" in findings


def test_integer_with_check_zero_one_matches_postgres_boolean() -> None:
    """Column-level ``CHECK (col IN (0, 1))`` collapses INTEGER to BOOLEAN.

    Sibling parametrized test ``test_default_equivalent_types_produce_no_drift``
    covers the rest of the type-equivalence families.
    """
    sqlite_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, "
        "is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1)));"
    )
    postgres_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, "
        "is_active BOOLEAN NOT NULL DEFAULT FALSE);"
    )
    assert _diff(sqlite_sql, postgres_sql) == []


# ── 8. bogus baseline entry fails self-check ────────────────────


def test_baseline_with_empty_reason_fails_self_check(tmp_path: Path) -> None:
    """A baseline line missing its trailing reason exits with code 2."""
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    sqlite_path.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);", encoding="utf-8")
    postgres_path.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);", encoding="utf-8")
    # 5-field column key with empty reason field after the trailing colon.
    baseline_path = _write_baseline(
        tmp_path,
        ["column:t:payload:TEXT:JSONB:"],
    )
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--baseline",
            str(baseline_path),
            "--skip-migrations",
        ]
    )
    assert rc == 2


def test_baseline_with_unknown_kind_fails_self_check(tmp_path: Path) -> None:
    """Lines whose first field is not a known kind also trip the self-check."""
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    sqlite_path.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);", encoding="utf-8")
    postgres_path.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);", encoding="utf-8")
    baseline_path = _write_baseline(
        tmp_path,
        ["unknownkind:t:payload:TEXT:JSONB:reason"],
    )
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--baseline",
            str(baseline_path),
            "--skip-migrations",
        ]
    )
    assert rc == 2


# ── index drift ─────────────────────────────────────────────────


def test_index_only_on_postgres_is_flagged() -> None:
    """A GIN index that exists only on Postgres surfaces as index drift."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, m TEXT);"
    postgres_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, m JSONB);"
        "CREATE INDEX idx_t_m_gin ON t USING GIN (m);"
    )
    findings = _diff(sqlite_sql, postgres_sql)
    assert "index:idx_t_m_gin:postgres_only" in findings


def test_identical_indexes_produce_no_drift() -> None:
    """Same-named, same-shape indexes match across backends."""
    sqlite_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT);"
        "CREATE INDEX idx_t_name ON t(name);"
    )
    postgres_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT);"
        "CREATE INDEX idx_t_name ON t(name);"
    )
    assert _diff(sqlite_sql, postgres_sql) == []


# ── 9. migration parity (matching) ──────────────────────────────


def test_migration_parity_matching_suffixes_pass(tmp_path: Path) -> None:
    """Same suffix on both backends → no migration drift."""
    sqlite_dir = tmp_path / "sqlite" / "revisions"
    postgres_dir = tmp_path / "postgres" / "revisions"
    sqlite_dir.mkdir(parents=True)
    postgres_dir.mkdir(parents=True)
    (sqlite_dir / "20260101000000_add_x.sql").write_text("-- noop\n", encoding="utf-8")
    (postgres_dir / "20260101000001_add_x.sql").write_text(
        "-- noop\n", encoding="utf-8"
    )
    findings = _MODULE.diff_migrations(sqlite_dir, postgres_dir)
    assert findings == []


# ── 10. migration parity (one-sided) ────────────────────────────


def test_migration_parity_sqlite_only_is_flagged(tmp_path: Path) -> None:
    """Migration with no Postgres sibling shows up as drift."""
    sqlite_dir = tmp_path / "sqlite" / "revisions"
    postgres_dir = tmp_path / "postgres" / "revisions"
    sqlite_dir.mkdir(parents=True)
    postgres_dir.mkdir(parents=True)
    (sqlite_dir / "20260101000000_alone.sql").write_text("-- noop\n", encoding="utf-8")
    findings = _MODULE.diff_migrations(sqlite_dir, postgres_dir)
    assert "migration:alone:sqlite_only" in findings


def test_migration_parity_postgres_only_is_flagged(tmp_path: Path) -> None:
    """Migration with no SQLite sibling shows up as drift."""
    sqlite_dir = tmp_path / "sqlite" / "revisions"
    postgres_dir = tmp_path / "postgres" / "revisions"
    sqlite_dir.mkdir(parents=True)
    postgres_dir.mkdir(parents=True)
    (postgres_dir / "20260101000000_alone.sql").write_text(
        "-- noop\n", encoding="utf-8"
    )
    findings = _MODULE.diff_migrations(sqlite_dir, postgres_dir)
    assert "migration:alone:postgres_only" in findings


def test_migration_parity_baseline_file_is_ignored(tmp_path: Path) -> None:
    """``00000000000000_baseline.sql`` is the squash baseline; ignored."""
    sqlite_dir = tmp_path / "sqlite" / "revisions"
    postgres_dir = tmp_path / "postgres" / "revisions"
    sqlite_dir.mkdir(parents=True)
    postgres_dir.mkdir(parents=True)
    (sqlite_dir / "00000000000000_baseline.sql").write_text(
        "-- noop\n", encoding="utf-8"
    )
    findings = _MODULE.diff_migrations(sqlite_dir, postgres_dir)
    assert findings == []


# ── 11. baselined migration drift passes ────────────────────────


def test_baselined_migration_drift_passes(tmp_path: Path) -> None:
    """Migration drift listed in the baseline does not trip ``main``."""
    sqlite_dir = tmp_path / "sqlite" / "revisions"
    postgres_dir = tmp_path / "postgres" / "revisions"
    sqlite_dir.mkdir(parents=True)
    postgres_dir.mkdir(parents=True)
    (sqlite_dir / "20260101000000_alone.sql").write_text("-- noop\n", encoding="utf-8")
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    sqlite_path.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);", encoding="utf-8")
    postgres_path.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);", encoding="utf-8")
    baseline_path = _write_baseline(
        tmp_path,
        ["migration:alone:sqlite_only:test reason"],
    )
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--sqlite-revisions",
            str(sqlite_dir),
            "--postgres-revisions",
            str(postgres_dir),
            "--baseline",
            str(baseline_path),
        ]
    )
    assert rc == 0


# ── 12. real-repo regression ────────────────────────────────────


def test_gate_runs_green_against_real_repo() -> None:
    """The shipped gate + baseline must pass on the actual schemas."""
    rc = _MODULE.main([])
    assert rc == 0


# ── baseline parser unit tests ──────────────────────────────────


def test_load_baseline_strips_comments_and_blanks(tmp_path: Path) -> None:
    """Comment lines and blanks are not treated as keys."""
    path = _write_baseline(
        tmp_path,
        [
            "# top comment",
            "",
            "column:t:c:TEXT:JSONB:reason here",
            "   ",
            "# trailing comment",
        ],
    )
    keys = _MODULE.load_baseline(path)
    assert keys == {"column:t:c:TEXT:JSONB"}


def test_load_baseline_preserves_colons_inside_reason(tmp_path: Path) -> None:
    """Colons in the reason field do not corrupt the key parse."""
    path = _write_baseline(
        tmp_path,
        ["column:t:c:TEXT:JSONB:see audit cluster #8: payload column"],
    )
    keys = _MODULE.load_baseline(path)
    assert keys == {"column:t:c:TEXT:JSONB"}


def test_load_baseline_understands_index_kind(tmp_path: Path) -> None:
    """``index`` kind has 4 fields (kind:name:side:reason)."""
    path = _write_baseline(
        tmp_path,
        ["index:idx_x_gin:postgres_only:GIN-only on PG"],
    )
    keys = _MODULE.load_baseline(path)
    assert keys == {"index:idx_x_gin:postgres_only"}


def test_load_baseline_understands_table_kind(tmp_path: Path) -> None:
    """``table`` kind has 4 fields (kind:name:side:reason)."""
    path = _write_baseline(
        tmp_path,
        ["table:legacy:sqlite_only:legacy table"],
    )
    keys = _MODULE.load_baseline(path)
    assert keys == {"table:legacy:sqlite_only"}


def test_load_baseline_understands_migration_kind(tmp_path: Path) -> None:
    """``migration`` kind has 4 fields (kind:suffix:side:reason)."""
    path = _write_baseline(
        tmp_path,
        ["migration:json_check_constraints:sqlite_only:tightening"],
    )
    keys = _MODULE.load_baseline(path)
    assert keys == {"migration:json_check_constraints:sqlite_only"}


def test_load_baseline_rejects_too_few_fields(tmp_path: Path) -> None:
    """Lines short on fields raise ``ValueError`` rather than silently dropping."""
    path = _write_baseline(tmp_path, ["column:t:c:TEXT"])
    with pytest.raises(ValueError, match="too few fields"):
        _MODULE.load_baseline(path)


def test_load_baseline_rejects_unknown_kind(tmp_path: Path) -> None:
    """Lines with an unrecognised first field raise ``ValueError``."""
    path = _write_baseline(tmp_path, ["bogus:t:c:TEXT:JSONB:r"])
    with pytest.raises(ValueError, match="unknown kind"):
        _MODULE.load_baseline(path)


def test_load_baseline_rejects_empty_reason(tmp_path: Path) -> None:
    """A trailing colon with whitespace-only reason is rejected."""
    path = _write_baseline(tmp_path, ["column:t:c:TEXT:JSONB:   "])
    with pytest.raises(ValueError, match="empty reason"):
        _MODULE.load_baseline(path)


# ── live shipped baseline file is internally valid ──────────────


def test_shipped_baseline_loads_without_error() -> None:
    """The committed baseline file is well-formed."""
    if not _BASELINE_PATH.exists():
        pytest.skip("baseline not yet generated")
    keys = _MODULE.load_baseline(_BASELINE_PATH)
    valid_kinds = {
        "column",
        "nullable",
        "pk",
        "unique",
        "index",
        "index_columns",
        "index_attr",
        "table",
        "migration",
    }
    # Every key starts with a known kind prefix.
    assert all(k.split(":", 1)[0] in valid_kinds for k in keys)


def test_load_baseline_error_messages_carry_line_number(tmp_path: Path) -> None:
    """ValueError messages must reference the offending line number."""
    path = _write_baseline(
        tmp_path,
        [
            "# header",
            "column:t:c:TEXT:JSONB:good entry",
            "bogus:line:here:reason",
        ],
    )
    with pytest.raises(ValueError, match="line 3"):
        _MODULE.load_baseline(path)


# ── nullability + PK + UNIQUE constraint diffing ──────────────────


def test_not_null_drift_is_flagged() -> None:
    """A column NOT NULL on one side and nullable on the other surfaces drift."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT NOT NULL);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT);"
    findings = _diff(sqlite_sql, postgres_sql)
    assert "nullable:t:name:N:Y" in findings


def test_matching_nullability_produces_no_drift() -> None:
    """Same NOT NULL across both sides produces no nullability finding."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT NOT NULL);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT NOT NULL);"
    assert _diff(sqlite_sql, postgres_sql) == []


def test_pk_drift_is_flagged() -> None:
    """Different PRIMARY KEY column lists across backends surface as ``pk:`` finding."""
    sqlite_sql = "CREATE TABLE t (a TEXT, b TEXT, PRIMARY KEY (a));"
    postgres_sql = "CREATE TABLE t (a TEXT, b TEXT, PRIMARY KEY (a, b));"
    findings = _diff(sqlite_sql, postgres_sql)
    assert "pk:t:a:a,b" in findings


def test_pk_drift_one_side_missing_pk() -> None:
    """A PK on one side and none on the other surfaces with ``_`` placeholder."""
    sqlite_sql = "CREATE TABLE t (a TEXT PRIMARY KEY);"
    postgres_sql = "CREATE TABLE t (a TEXT);"
    findings = _diff(sqlite_sql, postgres_sql)
    assert "pk:t:a:_" in findings


def test_unique_drift_is_flagged_one_sided() -> None:
    """A UNIQUE constraint on one side only surfaces as ``unique:`` finding."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT UNIQUE);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT);"
    findings = _diff(sqlite_sql, postgres_sql)
    assert "unique:t:name:sqlite_only" in findings


def test_matching_unique_constraints_produce_no_drift() -> None:
    """Same UNIQUE shape on both sides produces no finding."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT UNIQUE);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT UNIQUE);"
    assert _diff(sqlite_sql, postgres_sql) == []


# ── boolean-via-CHECK detection edge cases ────────────────────────


def test_boolean_check_in_table_level_collapses_to_bool() -> None:
    """A table-level CHECK(col IN (0, 1)) collapses INTEGER to BOOLEAN."""
    sqlite_sql = (
        "CREATE TABLE t ("
        "  id TEXT PRIMARY KEY, "
        "  is_active INTEGER NOT NULL DEFAULT 0, "
        "  CHECK (is_active IN (0, 1))"
        ");"
    )
    postgres_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, "
        "is_active BOOLEAN NOT NULL DEFAULT FALSE);"
    )
    assert _diff(sqlite_sql, postgres_sql) == []


def test_boolean_check_with_reversed_in_order() -> None:
    """``CHECK(col IN (1, 0))`` collapses to BOOLEAN (order irrelevant)."""
    sqlite_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, "
        "flag INTEGER NOT NULL DEFAULT 0 CHECK(flag IN (1, 0)));"
    )
    postgres_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, flag BOOLEAN NOT NULL DEFAULT FALSE);"
    )
    assert _diff(sqlite_sql, postgres_sql) == []


def test_boolean_check_with_smallint() -> None:
    """SMALLINT + CHECK(col IN (0, 1)) collapses to BOOLEAN."""
    sqlite_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, "
        "flag SMALLINT NOT NULL DEFAULT 0 CHECK(flag IN (0, 1)));"
    )
    postgres_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, flag BOOLEAN NOT NULL DEFAULT FALSE);"
    )
    assert _diff(sqlite_sql, postgres_sql) == []


def test_boolean_check_with_string_literals_does_not_collapse() -> None:
    """``CHECK(col IN ('0', '1'))`` is TEXT-stored, NOT the SQLite boolean idiom."""
    sqlite_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, "
        "flag TEXT NOT NULL DEFAULT '0' CHECK(flag IN ('0', '1')));"
    )
    postgres_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, flag BOOLEAN NOT NULL DEFAULT FALSE);"
    )
    findings = _diff(sqlite_sql, postgres_sql)
    assert "column:t:flag:TEXT:BOOLEAN" in findings


# ── parametrized type-equivalence families ────────────────────────


@pytest.mark.parametrize(
    ("sqlite_type", "postgres_type"),
    [
        ("TEXT", "TEXT"),
        ("TEXT", "VARCHAR"),
        ("TEXT", "VARCHAR(64)"),
        ("TEXT", "CHAR"),
        ("INTEGER", "INTEGER"),
        ("INTEGER", "BIGINT"),
        ("INTEGER", "SMALLINT"),
        ("INTEGER", "BIGSERIAL"),
        ("INTEGER", "SERIAL"),
        ("REAL", "DOUBLE PRECISION"),
        ("REAL", "FLOAT"),
        ("REAL", "NUMERIC"),
        ("REAL", "DECIMAL"),
        ("BLOB", "BYTEA"),
    ],
)
def test_default_equivalent_types_produce_no_drift(
    sqlite_type: str, postgres_type: str
) -> None:
    """Types in the same default family must not produce drift findings."""
    sqlite_sql = f"CREATE TABLE t (id TEXT PRIMARY KEY, x {sqlite_type});"
    postgres_sql = f"CREATE TABLE t (id TEXT PRIMARY KEY, x {postgres_type});"
    assert _diff(sqlite_sql, postgres_sql) == []


# ── index attribute parity ────────────────────────────────────────


def test_index_unique_flag_drift_is_flagged() -> None:
    """A shared-name index with different UNIQUE flag surfaces as ``index_attr:``."""
    sqlite_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT);"
        "CREATE INDEX idx_name ON t(name);"
    )
    postgres_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT);"
        "CREATE UNIQUE INDEX idx_name ON t(name);"
    )
    findings = _diff(sqlite_sql, postgres_sql)
    assert "index_attr:idx_name:unique:N:Y" in findings


def test_index_using_drift_is_flagged() -> None:
    """A shared-name index with different ``USING`` method surfaces as drift."""
    sqlite_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, m TEXT);CREATE INDEX idx_m ON t(m);"
    )
    postgres_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, m JSONB);"
        "CREATE INDEX idx_m ON t USING GIN (m);"
    )
    findings = _diff(sqlite_sql, postgres_sql)
    # The column type drift produces its own finding; we only assert
    # the index_attr finding here.
    assert "index_attr:idx_m:using:BTREE:GIN" in findings


def test_index_columns_drift_is_flagged() -> None:
    """A shared-name index with different indexed columns surfaces drift."""
    sqlite_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, a TEXT, b TEXT);"
        "CREATE INDEX idx_ab ON t(a, b);"
    )
    postgres_sql = (
        "CREATE TABLE t (id TEXT PRIMARY KEY, a TEXT, b TEXT);"
        "CREATE INDEX idx_ab ON t(b, a);"
    )
    findings = _diff(sqlite_sql, postgres_sql)
    assert "index_columns:idx_ab:a,b:b,a" in findings


# ── --update-baseline round-trip ──────────────────────────────────


def test_update_baseline_writes_round_trippable_format(tmp_path: Path) -> None:
    """``--update-baseline`` writes a file that ``load_baseline`` can read back."""
    sqlite_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, payload TEXT);"
    postgres_sql = "CREATE TABLE t (id TEXT PRIMARY KEY, payload JSONB);"
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    sqlite_path.write_text(sqlite_sql, encoding="utf-8")
    postgres_path.write_text(postgres_sql, encoding="utf-8")
    baseline_path = tmp_path / "baseline.txt"
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--baseline",
            str(baseline_path),
            "--skip-migrations",
            "--update-baseline",
        ]
    )
    assert rc == 0
    assert baseline_path.exists()
    content = baseline_path.read_text(encoding="utf-8")
    assert content.startswith("# Frozen baseline")
    keys = _MODULE.load_baseline(baseline_path)
    assert "column:t:payload:TEXT:JSONB" in keys


def test_update_baseline_fails_when_parent_dir_missing(tmp_path: Path) -> None:
    """Writing to a path whose parent does not exist exits 2 with a clear message."""
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    sqlite_path.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);", encoding="utf-8")
    postgres_path.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);", encoding="utf-8")
    bad_path = tmp_path / "no_such_dir" / "baseline.txt"
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--baseline",
            str(bad_path),
            "--skip-migrations",
            "--update-baseline",
        ]
    )
    assert rc == 2


# ── --explain output ──────────────────────────────────────────────


def test_explain_table_prints_inventory_for_existing_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--explain TABLE`` prints column inventory for both backends."""
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    sqlite_path.write_text(
        "CREATE TABLE t (id TEXT PRIMARY KEY, count INTEGER NOT NULL);",
        encoding="utf-8",
    )
    postgres_path.write_text(
        "CREATE TABLE t (id TEXT PRIMARY KEY, count BIGINT NOT NULL);",
        encoding="utf-8",
    )
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--explain",
            "t",
            "--skip-migrations",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "sqlite:" in captured.out
    assert "postgres:" in captured.out
    assert "id" in captured.out
    assert "count" in captured.out


def test_explain_table_handles_missing_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--explain TABLE`` reports ``<missing>`` when the table is absent."""
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    sqlite_path.write_text("", encoding="utf-8")
    postgres_path.write_text("", encoding="utf-8")
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--explain",
            "missing_table",
            "--skip-migrations",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "<missing>" in captured.out


# ── stale-baseline warning ────────────────────────────────────────


def test_stale_baseline_entry_warns_but_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Baseline entries no longer detected print warning to stderr but pass."""
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    sqlite_path.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);", encoding="utf-8")
    postgres_path.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);", encoding="utf-8")
    baseline_path = _write_baseline(
        tmp_path,
        ["column:t:gone_column:TEXT:JSONB:resolved drift"],
    )
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--baseline",
            str(baseline_path),
            "--skip-migrations",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "gone_column" in captured.err


# ── error handling ────────────────────────────────────────────────


def test_missing_schema_file_exits_with_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-existent schema path exits 2 with a clear stderr message."""
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(tmp_path / "no_sqlite.sql"),
            "--postgres-schema",
            str(tmp_path / "no_postgres.sql"),
            "--baseline",
            str(tmp_path / "baseline.txt"),
            "--skip-migrations",
        ]
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "schema file not found" in captured.err


def test_malformed_sql_exits_with_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SQL that sqlglot cannot parse exits 2 with a clear stderr message."""
    sqlite_path = tmp_path / "sqlite.sql"
    postgres_path = tmp_path / "postgres.sql"
    # An unterminated string literal is a tokenisation error.
    sqlite_path.write_text(
        "CREATE TABLE t (id TEXT DEFAULT 'unterminated", encoding="utf-8"
    )
    postgres_path.write_text("CREATE TABLE t (id TEXT);", encoding="utf-8")
    rc = _MODULE.main(
        [
            "--sqlite-schema",
            str(sqlite_path),
            "--postgres-schema",
            str(postgres_path),
            "--baseline",
            str(tmp_path / "baseline.txt"),
            "--skip-migrations",
        ]
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "schema parsing failed" in captured.err
