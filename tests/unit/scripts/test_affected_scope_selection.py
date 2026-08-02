"""Scope selection for the two heavy pre-push runners.

A push is held to a five-minute budget, so neither runner may expand to a
whole-tree run: a cross-tree question is handed to CI and announced, while
the changed paths are still checked locally. These tests pin that split at
BOTH layers -- the pure classifier and the entry point that acts on it --
because a re-widening reintroduced at the entry point would leave a
classifier-only suite entirely green while every push paid a quarter of an
hour again.
"""

import importlib.util
import re
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FULL_SUITE = "tests/unit/"

# A test file guaranteed to exist, because it is the one asserting with it.
_THIS_TEST_FILE = Path(__file__).resolve().relative_to(_REPO_ROOT).as_posix()


class _TestsModule(Protocol):
    """Subset of ``scripts/run_affected_tests.py`` under test."""

    PYPROJECT: str

    @staticmethod
    def _affected_test_dirs(changed: list[str]) -> tuple[list[str], bool]: ...

    @staticmethod
    def count_affected_test_files(test_dirs: list[str]) -> int: ...

    @staticmethod
    def _run_tests() -> int: ...


class _MypyModule(Protocol):
    """Subset of ``scripts/run_affected_mypy.py`` under test."""

    GitError: type[Exception]

    @staticmethod
    def _affected_mypy_paths(changed: list[str]) -> tuple[list[str], bool]: ...

    @staticmethod
    def main() -> int: ...


def _load(name: str) -> object:
    script_path = _REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_TESTS_MOD = _load("run_affected_tests")
_MYPY_MOD = _load("run_affected_mypy")
_TESTS = cast(_TestsModule, _TESTS_MOD)
_MYPY = cast(_MypyModule, _MYPY_MOD)


def _norm(paths: list[str]) -> set[str]:
    """Return paths with a platform-independent separator.

    The runners build these with ``Path.relative_to``, so the separator is
    the platform's; the selection under test is which paths, not how they
    are spelled.

    Returns:
        The same paths using forward slashes.
    """
    return {p.replace("\\", "/") for p in paths}


# ── Classifier layer ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changed", "expected_dirs", "expected_deferred"),
    [
        pytest.param(
            ["pyproject.toml"],
            set(),
            False,
            id="dependency_bump_selects_nothing_and_does_not_widen",
        ),
        pytest.param(
            ["src/synthorg/observability/events/integrations.py"],
            {"tests/unit/observability"},
            False,
            id="event_constants_are_an_ordinary_leaf",
        ),
        pytest.param(
            ["src/synthorg/observability/logger.py"],
            {"tests/unit/observability"},
            True,
            id="the_rest_of_observability_still_defers",
        ),
        pytest.param(
            ["src/synthorg/core/agent.py"],
            {"tests/unit/core"},
            True,
            id="foundational_module_runs_own_tests_and_defers",
        ),
        pytest.param(
            ["src/synthorg/__init__.py"],
            set(),
            True,
            id="top_level_source_defers",
        ),
        pytest.param(
            ["src/synthorg/foo.py"],
            set(),
            True,
            id="an_unlisted_top_level_file_defers_rather_than_vanishing",
        ),
        pytest.param(
            ["tests/unit/conftest.py", "src/synthorg/tools/mcp/config.py"],
            {"tests/unit/tools"},
            True,
            id="conftest_defers_without_selecting_the_whole_tree",
        ),
        pytest.param(
            [_THIS_TEST_FILE],
            {_THIS_TEST_FILE},
            False,
            id="a_test_file_selects_only_itself",
        ),
        pytest.param(
            ["tests/unit/tools/test_deleted_by_this_push.py"],
            set(),
            False,
            id="a_deleted_test_file_selects_nothing",
        ),
        pytest.param(
            ["tests/unit/tools/_helper.py"],
            {"tests/unit/tools"},
            False,
            id="a_shared_test_helper_still_selects_its_package",
        ),
        pytest.param(
            ["src/synthorg/tools/mcp/config.py", "tests/unit/tools/test_thing.py"],
            {"tests/unit/tools"},
            False,
            id="a_selected_package_absorbs_its_own_test_file",
        ),
        pytest.param(
            ["tests/unit/test_cold_import.py"],
            {"tests/unit/test_cold_import.py"},
            False,
            id="a_tier_root_test_file_selects_itself_not_the_smoke_test",
        ),
        pytest.param(
            ["tests/unit/__init__.py"],
            {"tests/unit/test_smoke.py"},
            False,
            id="a_tier_root_non_test_file_falls_back_to_the_smoke_test",
        ),
        pytest.param(
            ["tests/_shared/ids.py"],
            set(),
            True,
            id="shared_test_infrastructure_defers",
        ),
        pytest.param(
            ["tests/e2e/test_wizard.py"],
            set(),
            False,
            id="another_tier_is_not_this_runners_business",
        ),
        pytest.param([], set(), False, id="no_changes_select_nothing"),
        pytest.param(
            [
                "src/synthorg/tools/mcp/config.py",
                "src/synthorg/observability/events/tool.py",
            ],
            {"tests/unit/tools", "tests/unit/observability"},
            False,
            id="every_affected_module_is_kept",
        ),
    ],
)
def test_test_dir_selection(
    changed: list[str], expected_dirs: set[str], expected_deferred: bool
) -> None:
    dirs, deferred = _TESTS._affected_test_dirs(changed)
    assert _norm(dirs) == expected_dirs
    assert deferred is expected_deferred


@pytest.mark.parametrize(
    ("changed", "expected_deferred"),
    [
        pytest.param(
            ["src/synthorg/observability/events/integrations.py"],
            False,
            id="event_constants_are_an_ordinary_leaf",
        ),
        pytest.param(
            ["src/synthorg/observability/logger.py"],
            True,
            id="the_rest_of_observability_still_defers",
        ),
        pytest.param(
            ["src/synthorg/core/agent.py"], True, id="foundational_module_defers"
        ),
        pytest.param(["tests/unit/conftest.py"], True, id="conftest_defers"),
    ],
)
def test_mypy_path_selection(changed: list[str], expected_deferred: bool) -> None:
    paths, deferred = _MYPY._affected_mypy_paths(changed)
    assert deferred is expected_deferred
    assert _FULL_SUITE not in _norm(paths)


def test_a_gate_test_file_does_not_select_every_other_gates_tests() -> None:
    """The package this file lives in is a fifth of the unit tier.

    ``tests/unit/scripts`` is the one unit package with no source package
    to scope against, so every gate's tests share it. Selecting the
    package for a one-file change spent 115s of a 300s push budget
    running tests that the change could not reach.
    """
    dirs, deferred = _TESTS._affected_test_dirs([_THIS_TEST_FILE])

    assert _norm(dirs) == {_THIS_TEST_FILE}
    assert deferred is False
    assert _TESTS.count_affected_test_files(dirs) == 1


def test_observability_module_and_leaf_are_distinguished() -> None:
    """The leaf carve-out must not exempt the whole blast-radius module.

    Loosening the match from the (module, subpackage) pair to the module
    alone would silently drop every ``observability`` change out of
    deferral tracking, which no per-case assertion above would catch.
    """
    leaf, leaf_deferred = _TESTS._affected_test_dirs(
        ["src/synthorg/observability/events/tool.py"]
    )
    root, root_deferred = _TESTS._affected_test_dirs(
        ["src/synthorg/observability/logger.py"]
    )
    assert leaf_deferred is False
    assert root_deferred is True
    assert _norm(leaf) == _norm(root)


@pytest.mark.parametrize(
    "carve_out",
    [
        pytest.param(
            "src/synthorg/observability/events/tool.py", id="blast_radius_leaf"
        ),
        pytest.param("src/synthorg/observability/logger.py", id="blast_radius_module"),
        pytest.param("src/synthorg/__init__.py", id="top_level_source"),
        pytest.param("src/synthorg/foo.py", id="unlisted_top_level_source"),
        pytest.param("src/synthorg/../secrets.py", id="traversal_segment"),
        pytest.param("tests/unit/conftest.py", id="shared_test_infrastructure"),
        pytest.param("tests/_shared/ids.py", id="tierless_test_helper"),
        pytest.param("tests/_typeguard_checker.py", id="tierless_test_module"),
    ],
)
def test_both_runners_agree_on_what_defers(carve_out: str) -> None:
    """The two runners must never disagree about a push's coverage.

    They answer the same question in two languages, and both police the
    same budget. A carve-out added to one and not the other does not
    fail: it silently makes "what this push checked" mean two things.
    """
    _, tests_deferred = _TESTS._affected_test_dirs([carve_out])
    _, mypy_deferred = _MYPY._affected_mypy_paths([carve_out])
    assert tests_deferred is mypy_deferred


@pytest.mark.parametrize(
    "hostile",
    [
        "src/synthorg/../../etc/passwd.py",
        "src/synthorg/../secrets.py",
        "tests/unit/../../outside.py",
        "tests/unit/scripts/../../../outside/test_evil.py",
    ],
)
def test_traversal_segments_never_resolve_to_a_path(hostile: str) -> None:
    """The module-name regex is the only barrier against a crafted path.

    A path component the classifier accepted would be joined onto
    ``tests/unit`` (or ``src/synthorg``) and handed to pytest/mypy. The
    segment may degrade to an in-bounds default, but it must never carry
    the traversal through: every selected path stays under the tree the
    runner owns.
    """
    dirs, _ = _TESTS._affected_test_dirs([hostile])
    paths, _ = _MYPY._affected_mypy_paths([hostile])
    for selected in (*dirs, *paths):
        assert ".." not in Path(selected).parts
        resolved = (_REPO_ROOT / selected).resolve()
        assert resolved.is_relative_to(_REPO_ROOT)


# ── Entry-point layer ─────────────────────────────────────────────


class _Spy:
    """Records the paths an entry point chose to check."""

    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.run_all: list[bool] = []
        self.returncode = returncode

    def pytest(self, paths: list[str], *, run_all: bool = False) -> int:
        self.calls.append(list(paths))
        self.run_all.append(run_all)
        return self.returncode

    def mypy(self, paths: list[str]) -> int:
        self.calls.append(list(paths))
        return self.returncode


def test_deferred_change_does_not_run_the_full_suite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entry point must not re-widen what the classifier narrowed.

    This is the regression the whole change exists to prevent, and it
    lives here rather than in the classifier: reintroducing a full-suite
    branch would leave every classifier assertion above untouched.
    """
    spy = _Spy()
    monkeypatch.setattr(
        _TESTS_MOD, "_resolve_changed_files", lambda: ["src/synthorg/core/agent.py"]
    )
    monkeypatch.setattr(_TESTS_MOD, "_run_pytest", spy.pytest)

    assert _TESTS._run_tests() == 0
    assert spy.calls
    for selected in spy.calls:
        assert _FULL_SUITE not in _norm(selected)
    assert spy.run_all == [False]


def _force_cold_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the mypy runner take its cold path.

    A warm daemon checks the full scope outright, so the scoping under
    test only governs a run with no daemon to answer. Left live, the call
    would also spawn a real ``dmypy`` and build the whole graph.
    """
    monkeypatch.setattr(_MYPY_MOD, "_daemon_opted_out", lambda: True)


def test_deferred_change_does_not_run_full_mypy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _Spy()
    _force_cold_path(monkeypatch)
    monkeypatch.setattr(_MYPY_MOD, "merge_base", lambda: "base")
    monkeypatch.setattr(
        _MYPY_MOD, "changed_files", lambda _base: ["src/synthorg/core/agent.py"]
    )
    monkeypatch.setattr(_MYPY_MOD, "_run_mypy", spy.mypy)
    monkeypatch.setattr(_MYPY_MOD, "_run_full", lambda: pytest.fail("widened to full"))
    monkeypatch.setattr("sys.argv", ["run_affected_mypy.py"])

    assert _MYPY.main() == 0
    assert spy.calls
    for selected in spy.calls:
        assert "src/" not in selected


def test_unknowable_file_list_runs_the_full_suite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to scope means check everything, not check nothing.

    Reporting a green push that inspected no code is worse than a slow
    one, so the fail-safe is the full suite.
    """
    spy = _Spy()
    monkeypatch.setattr(_TESTS_MOD, "_resolve_changed_files", lambda: None)
    monkeypatch.setattr(_TESTS_MOD, "_run_pytest", spy.pytest)

    assert _TESTS._run_tests() == 0
    assert spy.calls == [[_FULL_SUITE]]
    assert spy.run_all == [True]


def _record_full_run(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Replace the mypy full-tree run with a recorder.

    Returns:
        The list the stubbed run appends to when it fires.
    """
    called: list[bool] = []

    def _run_full() -> int:
        called.append(True)
        return 0

    monkeypatch.setattr(_MYPY_MOD, "_run_full", _run_full)
    return called


def test_unknowable_file_list_runs_full_mypy(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_merge_base() -> str:
        msg = "origin/main unavailable"
        raise _MYPY.GitError(msg)

    _force_cold_path(monkeypatch)
    monkeypatch.setattr(_MYPY_MOD, "merge_base", _no_merge_base)
    called = _record_full_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["run_affected_mypy.py"])

    assert _MYPY.main() == 0
    assert called == [True]


def test_full_flag_runs_the_ci_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    called = _record_full_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["run_affected_mypy.py", "--full"])

    assert _MYPY.main() == 0
    assert called == [True]


def test_pytest_config_change_announces_the_deferral(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pyproject edit must not read like an unrelated doc-only push.

    It carries pytest's own configuration, so the run says out loud that
    the suite is CI's to answer rather than printing the generic
    nothing-to-do line.
    """
    monkeypatch.setattr(
        _TESTS_MOD, "_resolve_changed_files", lambda: [_TESTS.PYPROJECT]
    )
    monkeypatch.setattr(_TESTS_MOD, "_run_pytest", _Spy().pytest)

    _TESTS._run_tests()
    out = capsys.readouterr().out
    assert "pyproject.toml changed" in out
    assert "deferred to CI" in out


def test_deferral_never_claims_a_run_that_did_not_happen(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A conftest-only change defers AND selects nothing.

    Telling the reader "the affected tests still run here" when none do
    is worse than silence: it is an explicit assurance that no
    verification backs.
    """
    monkeypatch.setattr(
        _TESTS_MOD, "_resolve_changed_files", lambda: ["tests/unit/conftest.py"]
    )
    monkeypatch.setattr(_TESTS_MOD, "_run_pytest", _Spy().pytest)

    _TESTS._run_tests()
    out = capsys.readouterr().out
    assert "NOTHING runs locally" in out
    assert not re.search(r"affected tests still run here", out)
