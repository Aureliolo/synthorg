"""Unit tests for ``scripts/check_enum_check_constraint_parity.py``.

The gate exists because ``BlockedReason.NO_CAPABLE_AGENT`` shipped, was
written by two production paths, and appeared in neither backend's CHECK: a
subtask nobody could take could not be parked at all, and a live run ended
with two subtasks at ``created`` that nothing watched and nothing could move.

The shapes below are the ones that decide whether the gate is usable. Every
false positive it produced on first run was a conditional-invariant CHECK, so
those are pinned as passing cases rather than described in prose.

Tests load the script via :mod:`importlib` and call its helpers directly,
matching ``test_check_no_synthetic_cost_owner.py``.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Final, Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_enum_check_constraint_parity.py"


class _CheckView(Protocol):
    """Structural view of the script's private ``_CheckSet`` class."""

    rel: str
    lineno: int
    column: str
    values: frozenset[str]


class _EnumView(Protocol):
    """Structural view of the script's private ``_EnumVocabulary`` class."""

    rel: str
    name: str
    values: frozenset[str]


class _EnumFactory(Protocol):
    """The script's ``_EnumVocabulary`` constructor."""

    def __call__(self, *, rel: str, name: str, values: frozenset[str]) -> _EnumView: ...


class _CheckFactory(Protocol):
    """The script's ``_CheckSet`` constructor."""

    def __call__(
        self, *, rel: str, lineno: int, column: str, values: frozenset[str]
    ) -> _CheckView: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    # Call signatures rather than ``type[...]``: the views above are
    # Protocols, which cannot be instantiated, and what the tests need is the
    # script's own dataclass constructor behind a checked shape.
    _EnumVocabulary: _EnumFactory
    _CheckSet: _CheckFactory

    @staticmethod
    def _collect_checks(project_root: Path) -> list[_CheckView]: ...
    @staticmethod
    def _collect_enums(project_root: Path) -> list[_EnumView]: ...
    @staticmethod
    def _best_superset(
        check: _CheckView, enums: list[_EnumView]
    ) -> _EnumView | None: ...
    @staticmethod
    def _is_valid_marker(line: str) -> bool: ...
    @staticmethod
    def _ends_statement(line: str) -> bool: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # _gate_source sibling); restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_enum_check_constraint_parity",
            _SCRIPT_PATH,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cast(_ScriptModule, module)
    finally:
        sys.path[:] = saved


_MODULE = _load_script()


# The SQL in this file is the gate's INPUT, never a query anything runs: the
# fixtures ARE the thing under test. Assembled from these two constants so the
# DDL keywords appear once rather than in every case.
_CREATE_TABLE: Final[str] = (
    "CREATE TABLE"  # lint-allow: persistence-boundary -- gate fixture, never executed
)
_ALTER_TABLE: Final[str] = (
    "ALTER TABLE"  # lint-allow: persistence-boundary -- gate fixture, never executed
)
_CREATE_INDEX: Final[str] = (
    "CREATE INDEX"  # lint-allow: persistence-boundary -- gate fixture, never executed
)


def _table(*body: str) -> str:
    """Wrap fixture lines in a table definition the gate can scan.

    Returns:
        A complete, semicolon-terminated table definition.
    """
    inner = "\n".join(f"    {line}" for line in body)
    return f"{_CREATE_TABLE} t (\n{inner}\n);\n"


def _schema_root(tmp_path: Path, sqlite_sql: str, postgres_sql: str = "") -> Path:
    """Write a fake repo whose two declared schemas hold the given SQL.

    Returns:
        The project root the gate should be pointed at.
    """
    for backend, sql in (("sqlite", sqlite_sql), ("postgres", postgres_sql)):
        target = tmp_path / "src" / "synthorg" / "persistence" / backend
        target.mkdir(parents=True, exist_ok=True)
        (target / "schema.sql").write_text(sql, encoding="utf-8")
    return tmp_path


def _enum_root(tmp_path: Path, name: str, source: str) -> Path:
    """Write a fake repo whose scanned tree holds one Python module.

    Initialised as a git repository with the module tracked, because
    ``_collect_enums`` reaches the tree through ``_git_tracked_python_files``:
    a plain directory fails ``git ls-files``, warns, and falls back to
    ``rglob``, so every test here would cover the fallback and leave the
    branch that actually runs in CI untested.

    Returns:
        The project root the gate should be pointed at.
    """
    target = tmp_path / "src" / "synthorg"
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
    subprocess.run(  # noqa: S603
        ["git", "add", "--", f"src/synthorg/{name}"],  # noqa: S607
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def _untracked_enum_root(tmp_path: Path, name: str, source: str) -> Path:
    """Write the same tree with no git repository around it.

    Returns:
        The project root the gate should be pointed at.
    """
    target = tmp_path / "src" / "synthorg"
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_text(source, encoding="utf-8")
    return tmp_path


class TestVocabularyShape:
    """Only a whole-body ``col IN (...)`` counts as a column vocabulary."""

    def test_plain_vocabulary_is_collected(self, tmp_path: Path) -> None:
        root = _schema_root(
            tmp_path,
            _table("status TEXT CHECK (status IN ('a', 'b', 'c'))"),
        )
        checks = _MODULE._collect_checks(root)
        assert [c.column for c in checks] == ["status"]
        assert checks[0].values == frozenset({"a", "b", "c"})

    def test_null_guarded_vocabulary_is_collected(self, tmp_path: Path) -> None:
        root = _schema_root(
            tmp_path,
            _table(
                "status TEXT CHECK (",
                "    status IS NULL OR status IN ('a', 'b')",
                ")",
            ),
        )
        checks = _MODULE._collect_checks(root)
        assert [c.column for c in checks] == ["status"]

    def test_conditional_invariant_is_not_a_vocabulary(self, tmp_path: Path) -> None:
        """The false positive that appeared eleven times on the first run.

        A per-branch consistency rule names a subset on purpose; it makes no
        claim about what the column may hold.
        """
        root = _schema_root(
            tmp_path,
            _table(
                "CHECK (",
                "    (status = 'pending' AND decided_at IS NULL)",
                "    OR (status IN ('approved', 'rejected')",
                "        AND decided_at IS NOT NULL)",
                ")",
            ),
        )
        assert _MODULE._collect_checks(root) == []

    def test_not_in_exclusion_is_not_a_vocabulary(self, tmp_path: Path) -> None:
        root = _schema_root(
            tmp_path,
            _table("CHECK (status NOT IN ('expired', 'cancelled'))"),
        )
        assert _MODULE._collect_checks(root) == []

    def test_single_literal_is_a_constant_not_a_vocabulary(
        self, tmp_path: Path
    ) -> None:
        root = _schema_root(tmp_path, _table("kind TEXT CHECK (kind IN ('only'))"))
        assert _MODULE._collect_checks(root) == []

    def test_mismatched_null_guard_is_not_a_vocabulary(self, tmp_path: Path) -> None:
        """A guard on a different column makes the body a compound predicate."""
        root = _schema_root(
            tmp_path,
            _table("CHECK (other IS NULL OR status IN ('a', 'b'))"),
        )
        assert _MODULE._collect_checks(root) == []


class TestSuppression:
    """The marker is scoped to the CHECK's own statement."""

    def test_marker_suppresses_the_check(self, tmp_path: Path) -> None:
        root = _schema_root(
            tmp_path,
            _table(
                "-- lint-allow: enum-check-parity -- narrowed on purpose",
                "status TEXT CHECK (status IN ('a', 'b'))",
            ),
        )
        assert _MODULE._collect_checks(root) == []

    def test_marker_without_a_reason_does_not_suppress(self, tmp_path: Path) -> None:
        root = _schema_root(
            tmp_path,
            _table(
                "-- lint-allow: enum-check-parity",
                "status TEXT CHECK (status IN ('a', 'b'))",
            ),
        )
        assert len(_MODULE._collect_checks(root)) == 1

    def test_prose_semicolon_does_not_end_the_statement(self) -> None:
        """A ``--`` line ending in ``;`` is prose, not a terminator.

        Reading one as a terminator cut the suppression scope short of the
        very marker it was meant to carry.
        """
        assert not _MODULE._ends_statement("    -- a clause; and another")
        assert _MODULE._ends_statement(f"{_CREATE_INDEX} i ON t (c);")
        assert _MODULE._ends_statement(f"{_ALTER_TABLE} t ADD c TEXT; -- note")


class TestWhichEnumsAreCollected:
    """The Python half of the comparison, which decides what a CHECK is held to."""

    def test_a_str_enum_is_collected(self, tmp_path: Path) -> None:
        root = _enum_root(
            tmp_path,
            "reasons.py",
            (
                "from enum import StrEnum\n"
                "class BlockedReason(StrEnum):\n"
                '    FIRST = "first"\n'
                '    SECOND = "second"\n'
            ),
        )
        collected = {e.name: e.values for e in _MODULE._collect_enums(root)}
        assert collected["BlockedReason"] == frozenset({"first", "second"})

    def test_a_single_member_enum_is_not_a_vocabulary(self, tmp_path: Path) -> None:
        """One value is a constant; a CHECK naming it says nothing about drift."""
        root = _enum_root(
            tmp_path,
            "single.py",
            ('from enum import StrEnum\nclass Only(StrEnum):\n    ONE = "one"\n'),
        )
        assert [e for e in _MODULE._collect_enums(root) if e.name == "Only"] == []

    def test_a_plain_enum_is_not_collected(self, tmp_path: Path) -> None:
        """The values written into a column are strings, so only StrEnum applies."""
        root = _enum_root(
            tmp_path,
            "plain.py",
            (
                "from enum import Enum\n"
                "class Numbered(Enum):\n"
                "    FIRST = 1\n"
                "    SECOND = 2\n"
            ),
        )
        assert [e for e in _MODULE._collect_enums(root) if e.name == "Numbered"] == []

    def test_a_tree_outside_git_still_scans(self, tmp_path: Path) -> None:
        """The fallback the other tests no longer exercise.

        ``git ls-files`` is the path CI takes, so the fixtures track their
        module; this keeps the widening ``rglob`` fallback covered, since a
        tree the command cannot answer for must still be scanned rather than
        read as holding no enums at all.
        """
        root = _untracked_enum_root(
            tmp_path,
            "loose.py",
            (
                "from enum import StrEnum\n"
                "class Loose(StrEnum):\n"
                '    FIRST = "first"\n'
                '    SECOND = "second"\n'
            ),
        )
        collected = {e.name: e.values for e in _MODULE._collect_enums(root)}
        assert collected["Loose"] == frozenset({"first", "second"})


class TestWhichEnumACheckIsHeldTo:
    """An exact match ends the question; only a strict superset is drift."""

    def _enum(self, name: str, *values: str) -> _EnumView:
        return _MODULE._EnumVocabulary(
            rel=f"src/synthorg/{name.lower()}.py",
            name=name,
            values=frozenset(values),
        )

    def _check(self, *values: str) -> _CheckView:
        return _MODULE._CheckSet(
            rel="schema.sql",
            lineno=1,
            column="status",
            values=frozenset(values),
        )

    def test_an_exact_match_reports_nothing(self) -> None:
        """A whole enum is right even when a larger one also contains it."""
        enums = [
            self._enum("RiskLevel", "low", "medium", "high", "critical"),
            self._enum("Severity", "low", "medium", "high", "critical", "info"),
        ]
        exact = self._check("low", "medium", "high", "critical")

        assert _MODULE._best_superset(exact, enums) is None

    def test_the_tightest_superset_wins(self) -> None:
        """The loosest would name an enum the column has nothing to do with."""
        enums = [
            self._enum("Narrow", "a", "b", "c"),
            self._enum("Wide", "a", "b", "c", "d", "e", "f"),
        ]
        best = _MODULE._best_superset(self._check("a", "b"), enums)
        assert best is not None
        assert best.name == "Narrow"

    def test_a_vocabulary_no_enum_contains_is_not_this_gates_question(self) -> None:
        """Dead SQL vocabulary fails no write; the gate asks the other direction."""
        enums = [self._enum("Known", "a", "b")]
        assert _MODULE._best_superset(self._check("x", "y"), enums) is None


class TestRealTree:
    """The live repository satisfies its own gate."""

    def test_live_tree_is_clean(self) -> None:
        assert _MODULE.main(["--repo-root", str(_REPO_ROOT)]) == 0
