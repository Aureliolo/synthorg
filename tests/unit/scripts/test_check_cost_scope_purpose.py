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
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_cost_scope_purpose.py"


class _Hit(Protocol):
    """Structural view of the script's private ``_Hit`` class."""

    rel: str
    lineno: int
    col: int

    def baseline_key(self) -> str: ...
    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    @staticmethod
    def _scan_file(path: Path, rel: str) -> list[_Hit]: ...
    @staticmethod
    def _is_valid_marker(comment_token: str) -> bool: ...
    @staticmethod
    def _load_baseline(path: Path) -> set[str]: ...
    @staticmethod
    def _write_baseline(hits: list[_Hit], path: Path) -> None: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    spec = importlib.util.spec_from_file_location(
        "_check_cost_scope_purpose",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_ScriptModule, module)


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


def test_lint_allow_marker_suppresses(write_py: WritePy) -> None:
    path = write_py(_CALL_MARKER)
    assert _MODULE._scan_file(path, "src/synthorg/foo.py") == []


def test_marker_requires_justification() -> None:
    assert _MODULE._is_valid_marker("# lint-allow: cost-scope-purpose -- why")
    assert not _MODULE._is_valid_marker("# lint-allow: cost-scope-purpose")
    assert not _MODULE._is_valid_marker("# lint-allow: cost-scope-purpose -- ")
    assert not _MODULE._is_valid_marker("# unrelated comment")


def test_baseline_round_trips(write_py: WritePy, tmp_path: Path) -> None:
    path = write_py(_CALL_MISSING)
    hits = _MODULE._scan_file(path, "src/synthorg/foo.py")
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
