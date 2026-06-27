"""Unit tests for ``scripts/check_cost_scope_purpose.py``.

Exercises the AST detection (a ``cost_recording_scope`` call missing the
``purpose=`` keyword), the ``purpose=None`` / ``purpose=<value>`` accept paths,
the ``**kwargs`` fail-closed path, the per-line
``# lint-allow: cost-scope-purpose`` suppression marker, and the baseline
load/write round-trip.

Tests load the script via :mod:`importlib` and call its private helpers
directly, matching the pattern in ``test_check_no_magic_numbers.py``.
"""

import importlib.util
import os
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every ``GIT_*`` env var for the duration of each test.

    The gate's ``git ls-files`` subprocess inherits this process's
    environment. Under a pre-push hook ``GIT_DIR`` / ``GIT_WORK_TREE`` /
    ``GIT_INDEX_FILE`` point at the real synthorg repo, which would let the
    scan escape the test's ``tmp_path`` sandbox and read the live tree. With
    them cleared, git resolves via ``cwd`` alone (a non-repo ``tmp_path``),
    so the gate's only filesystem access is the rglob fallback over the
    sandbox. A test must NEVER touch real repo data.
    """
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_cost_scope_purpose.py"


class _HitView(Protocol):
    """Structural view of the script's private ``_Hit`` class."""

    rel: str
    lineno: int
    col: int

    def baseline_key(self) -> str: ...
    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    _Hit: type

    @staticmethod
    def _scan_file(path: Path, rel: str) -> list[_HitView]: ...
    @staticmethod
    def _is_valid_marker(comment_token: str) -> bool: ...
    @staticmethod
    def _load_baseline(path: Path) -> set[str]: ...
    @staticmethod
    def _write_baseline(hits: list[_HitView], path: Path) -> None: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # _gate_source sibling); restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_cost_scope_purpose",
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


class WritePy(Protocol):
    """Callable signature of the ``write_py`` fixture."""

    def __call__(self, content: str, name: str = ...) -> Path: ...


@pytest.fixture
def write_py(tmp_path: Path) -> WritePy:
    """Helper that writes a Python source string to ``tmp_path/<name>``."""

    def _write(content: str, name: str = "sample.py") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


_CALL_MISSING = """\
async def run() -> None:
    async with cost_recording_scope(
        cost_tracker=tracker,
        agent_id=agent_id,
        task_id=task_id,
        call_category=category,
    ):
        await provider.complete(messages, model)
"""

_CALL_WITH_PURPOSE = """\
async def run() -> None:
    async with cost_recording_scope(
        cost_tracker=tracker,
        agent_id=agent_id,
        task_id=task_id,
        purpose=PromptPurposeId.MEMORY_RERANK,
        call_category=category,
    ):
        await provider.complete(messages, model)
"""

_CALL_PURPOSE_NONE = """\
async def run() -> None:
    async with cost_recording_scope(
        cost_tracker=tracker,
        agent_id=agent_id,
        task_id=task_id,
        purpose=None,
        call_category=category,
    ):
        await provider.complete(messages, model)
"""

_CALL_KWARGS_SPREAD = """\
async def run() -> None:
    async with cost_recording_scope(**params):
        await provider.complete(messages, model)
"""

_CALL_MARKER = """\
async def run() -> None:
    async with cost_recording_scope(  # lint-allow: cost-scope-purpose -- legacy
        cost_tracker=tracker,
        agent_id=agent_id,
        task_id=task_id,
        call_category=category,
    ):
        await provider.complete(messages, model)
"""

_CALL_ATTR_MISSING = """\
async def run() -> None:
    async with cr.cost_recording_scope(
        cost_tracker=tracker,
        agent_id=agent_id,
        task_id=task_id,
        call_category=category,
    ):
        await provider.complete(messages, model)
"""

_CALL_ALIAS_MISSING = """\
from synthorg.providers.cost_recording import cost_recording_scope as crs


async def run() -> None:
    async with crs(
        cost_tracker=tracker,
        agent_id=agent_id,
        task_id=task_id,
        call_category=category,
    ):
        await provider.complete(messages, model)
"""

_CALL_REBIND_MISSING = """\
from synthorg.providers.cost_recording import cost_recording_scope

crs = cost_recording_scope
crs2 = crs


async def run() -> None:
    async with crs2(
        cost_tracker=tracker,
        agent_id=agent_id,
        task_id=task_id,
        call_category=category,
    ):
        await provider.complete(messages, model)
"""


def test_missing_purpose_flagged(write_py: WritePy) -> None:
    path = write_py(_CALL_MISSING)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert hits[0].rel == "src/synthorg/foo.py"
    assert hits[0].lineno == 2


def test_explicit_purpose_not_flagged(write_py: WritePy) -> None:
    path = write_py(_CALL_WITH_PURPOSE)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


def test_purpose_none_not_flagged(write_py: WritePy) -> None:
    path = write_py(_CALL_PURPOSE_NONE)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


def test_kwargs_spread_fails_closed(write_py: WritePy) -> None:
    path = write_py(_CALL_KWARGS_SPREAD)
    assert len(_MODULE._scan_file(path, "src/synthorg/foo.py")) == 1


def test_attribute_call_missing_purpose_flagged(write_py: WritePy) -> None:
    path = write_py(_CALL_ATTR_MISSING)
    assert len(_MODULE._scan_file(path, "src/synthorg/foo.py")) == 1


def test_aliased_import_call_missing_purpose_flagged(write_py: WritePy) -> None:
    path = write_py(_CALL_ALIAS_MISSING)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert hits[0].lineno == 5


def test_rebound_alias_call_missing_purpose_flagged(write_py: WritePy) -> None:
    path = write_py(_CALL_REBIND_MISSING)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    assert hits[0].lineno == 8


def test_lint_allow_marker_suppresses(write_py: WritePy) -> None:
    path = write_py(_CALL_MARKER)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("# lint-allow: cost-scope-purpose -- why", True),
        ("# lint-allow: cost-scope-purpose", False),
        ("# lint-allow: cost-scope-purpose -- ", False),
        ("# unrelated comment", False),
    ],
    ids=["valid-with-reason", "missing-dash", "empty-reason", "unrelated"],
)
def test_marker_requires_justification(comment: str, expected: bool) -> None:
    assert _MODULE._is_valid_marker(comment) is expected


def test_baseline_round_trips(write_py: WritePy, tmp_path: Path) -> None:
    path = write_py(_CALL_MISSING)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
    assert len(hits) == 1
    baseline = tmp_path / "baseline.txt"
    _MODULE._write_baseline(hits, baseline)
    loaded = _MODULE._load_baseline(baseline)
    assert loaded == {hits[0].baseline_key()}


def test_missing_baseline_is_empty(tmp_path: Path) -> None:
    assert _MODULE._load_baseline(tmp_path / "absent.txt") == set()


def test_malformed_baseline_raises(tmp_path: Path) -> None:
    bad = tmp_path / "baseline.txt"
    bad.write_text("not-a-valid-entry\n", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline failed validation"):
        _MODULE._load_baseline(bad)


def _make_repo(tmp_path: Path, *, violation: bool) -> Path:
    """Lay out a throwaway repo tree; return the violating source path."""
    src = tmp_path / "src" / "synthorg"
    src.mkdir(parents=True)
    foo = src / "foo.py"
    if violation:
        foo.write_text(_CALL_MISSING, encoding="utf-8")
    return foo


def _write_repo_baseline(tmp_path: Path, baseline: str) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "cost_scope_purpose_baseline.txt").write_text(
        baseline, encoding="utf-8"
    )


def test_main_clean_tree_returns_zero(tmp_path: Path) -> None:
    _make_repo(tmp_path, violation=False)
    assert _MODULE.main(["--repo-root", str(tmp_path)]) == 0


def test_main_new_violation_returns_one(tmp_path: Path) -> None:
    _make_repo(tmp_path, violation=True)
    assert _MODULE.main(["--repo-root", str(tmp_path)]) == 1


def test_main_baselined_violation_returns_zero(tmp_path: Path) -> None:
    foo = _make_repo(tmp_path, violation=True)
    hits = _MODULE._scan_file(foo, "src/synthorg/foo.py")
    _write_repo_baseline(tmp_path, "\n".join(h.baseline_key() for h in hits) + "\n")
    assert _MODULE.main(["--repo-root", str(tmp_path)]) == 0


def test_main_corrupt_baseline_returns_two(tmp_path: Path) -> None:
    _make_repo(tmp_path, violation=True)
    _write_repo_baseline(tmp_path, "not-a-valid-entry\n")
    assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2


def test_main_stale_baseline_entry_returns_two(tmp_path: Path) -> None:
    foo = _make_repo(tmp_path, violation=True)
    hits = _MODULE._scan_file(foo, "src/synthorg/foo.py")
    live = "\n".join(h.baseline_key() for h in hits)
    stale = "src/synthorg/gone.py:1:0"
    _write_repo_baseline(tmp_path, f"{live}\n{stale}\n")
    assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2


def test_main_bad_repo_root_returns_two() -> None:
    assert _MODULE.main(["--repo-root", "/no/such/path/xyzzy"]) == 2


def test_hit_rejects_illegal_coordinates() -> None:
    hit_cls = _MODULE._Hit
    hit_cls(rel="src/synthorg/foo.py", lineno=1, col=0)  # valid
    for kwargs in (
        {"rel": "", "lineno": 1, "col": 0},
        {"rel": "x", "lineno": 0, "col": 0},
        {"rel": "x", "lineno": 1, "col": -1},
    ):
        with pytest.raises(ValueError, match="must"):
            hit_cls(**kwargs)
