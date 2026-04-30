"""Tests for the bare-Mock pre-commit gate."""

import importlib.util
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Callable signature for the ``write_test_file`` fixture; precise typing
# here lets call sites drop their ``# type: ignore[operator]`` markers.
WriteTestFile = Callable[..., Path]


def _load_module() -> object:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "check_mock_spec.py"
    spec = importlib.util.spec_from_file_location("check_mock_spec", script_path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


@pytest.fixture
def write_test_file(tmp_path: Path) -> WriteTestFile:
    """Return a small helper that writes a Python source file."""

    def _write(content: str, name: str = "sample.py") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


def test_bare_mock_detected(write_test_file: WriteTestFile) -> None:
    src = "from unittest.mock import AsyncMock\nx = AsyncMock()\n"
    path = write_test_file(src)
    hits = _MODULE._scan_file(path)  # type: ignore[attr-defined]
    assert len(hits) == 1
    assert hits[0][0] == 2  # line 2


def test_specced_mock_ignored(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import AsyncMock\nclass Foo: ...\nx = AsyncMock(spec=Foo)\n"
    )
    path = write_test_file(src)
    assert _MODULE._scan_file(path) == []  # type: ignore[attr-defined]


def test_positional_first_arg_treated_as_spec(write_test_file: WriteTestFile) -> None:
    """``Mock(SomeClass)`` is conventionally a spec hint; not a bare mock."""
    src = "from unittest.mock import Mock\nclass Foo: ...\nx = Mock(Foo)\n"
    path = write_test_file(src)
    assert _MODULE._scan_file(path) == []  # type: ignore[attr-defined]


def test_attribute_call_form_detected(write_test_file: WriteTestFile) -> None:
    """``mock.MagicMock()`` is the same offence as a bare-name call."""
    src = "import unittest.mock as mock\nx = mock.MagicMock()\n"
    path = write_test_file(src)
    hits = _MODULE._scan_file(path)  # type: ignore[attr-defined]
    assert len(hits) == 1


def test_non_mock_calls_not_flagged(write_test_file: WriteTestFile) -> None:
    """A bare ``list()`` / ``dict()`` is not in scope."""
    src = "x = list()\ny = dict()\n"
    path = write_test_file(src)
    assert _MODULE._scan_file(path) == []  # type: ignore[attr-defined]


def test_empty_splat_args_treated_as_bare(write_test_file: WriteTestFile) -> None:
    """``Mock(*())`` and ``Mock(**{})`` are still bare calls."""
    src = (
        "from unittest.mock import AsyncMock\n"
        "x = AsyncMock(*())\n"
        "y = AsyncMock(**{})\n"
        "z = AsyncMock(*[], **{})\n"
    )
    path = write_test_file(src)
    hits = _MODULE._scan_file(path)  # type: ignore[attr-defined]
    assert len(hits) == 3
    assert {lineno for lineno, _ in hits} == {2, 3, 4}


def test_non_empty_splat_not_flagged(write_test_file: WriteTestFile) -> None:
    """Real splats with content are not bare calls."""
    src = (
        "from unittest.mock import AsyncMock\n"
        "args = (1, 2)\n"
        "x = AsyncMock(*args)\n"
        "y = AsyncMock(*[1])\n"
        "z = AsyncMock(**{'a': 1})\n"
    )
    path = write_test_file(src)
    assert _MODULE._scan_file(path) == []  # type: ignore[attr-defined]


def test_unparseable_file_raises(write_test_file: WriteTestFile) -> None:
    src = "def broken(:\n"
    path = write_test_file(src)
    with pytest.raises(_MODULE.InspectionError):  # type: ignore[attr-defined]
        _MODULE._scan_file(path)  # type: ignore[attr-defined]


def test_kwargs_without_spec_flagged(write_test_file: WriteTestFile) -> None:
    """``Mock(name="x")``, ``Mock(return_value=42)`` etc. don't declare a spec."""
    src = (
        "from unittest.mock import AsyncMock, MagicMock, Mock\n"
        "a = AsyncMock(name='x')\n"
        "b = MagicMock(return_value=42)\n"
        "c = Mock(side_effect=ValueError)\n"
        "d = Mock(wraps=object())\n"
    )
    path = write_test_file(src)
    hits = _MODULE._scan_file(path)  # type: ignore[attr-defined]
    assert len(hits) == 4
    assert {lineno for lineno, _ in hits} == {2, 3, 4, 5}


def test_spec_kwarg_ignored(write_test_file: WriteTestFile) -> None:
    """``spec=`` and ``spec_set=`` keyword args declare the interface."""
    src = (
        "from unittest.mock import AsyncMock, Mock\n"
        "class Foo: ...\n"
        "a = AsyncMock(spec=Foo, name='x')\n"
        "b = Mock(spec_set=Foo, return_value=42)\n"
    )
    path = write_test_file(src)
    assert _MODULE._scan_file(path) == []  # type: ignore[attr-defined]


def test_shared_dir_excluded_via_pre_commit_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cmd_scan_paths`` skips ``tests/_shared/`` like ``--scan-all`` does.

    Pre-commit feeds individual paths to ``cmd_scan_paths``; the
    exclusion needs to apply at that entry point too, not only at the
    walk-the-tree entry point.
    """
    tests_root = tmp_path / "tests"
    shared = tests_root / "_shared"
    shared.mkdir(parents=True)
    bad_file = shared / "fake.py"
    bad_file.write_text(
        "from unittest.mock import AsyncMock\nx = AsyncMock()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_MODULE, "_TESTS_ROOT", tests_root)
    monkeypatch.setattr(_MODULE, "_load_baseline", set)
    rc = _MODULE.cmd_scan_paths([str(bad_file)])  # type: ignore[attr-defined]
    assert rc == 0
