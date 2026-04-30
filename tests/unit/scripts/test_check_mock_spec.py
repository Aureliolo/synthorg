"""Tests for the bare-Mock pre-commit gate."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


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
def write_test_file(tmp_path: Path) -> object:
    """Return a small helper that writes a Python source file."""

    def _write(content: str, name: str = "sample.py") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


def test_bare_mock_detected(write_test_file: object) -> None:
    src = "from unittest.mock import AsyncMock\nx = AsyncMock()\n"
    path = write_test_file(src)  # type: ignore[operator]
    hits = _MODULE._scan_file(path)  # type: ignore[attr-defined]
    assert len(hits) == 1
    assert hits[0][0] == 2  # line 2


def test_specced_mock_ignored(write_test_file: object) -> None:
    src = (
        "from unittest.mock import AsyncMock\nclass Foo: ...\nx = AsyncMock(spec=Foo)\n"
    )
    path = write_test_file(src)  # type: ignore[operator]
    assert _MODULE._scan_file(path) == []  # type: ignore[attr-defined]


def test_positional_first_arg_treated_as_spec(write_test_file: object) -> None:
    """``Mock(SomeClass)`` is conventionally a spec hint; not a bare mock."""
    src = "from unittest.mock import Mock\nclass Foo: ...\nx = Mock(Foo)\n"
    path = write_test_file(src)  # type: ignore[operator]
    assert _MODULE._scan_file(path) == []  # type: ignore[attr-defined]


def test_attribute_call_form_detected(write_test_file: object) -> None:
    """``mock.MagicMock()`` is the same offence as a bare-name call."""
    src = "import unittest.mock as mock\nx = mock.MagicMock()\n"
    path = write_test_file(src)  # type: ignore[operator]
    hits = _MODULE._scan_file(path)  # type: ignore[attr-defined]
    assert len(hits) == 1


def test_non_mock_calls_not_flagged(write_test_file: object) -> None:
    """A bare ``list()`` / ``dict()`` is not in scope."""
    src = "x = list()\ny = dict()\n"
    path = write_test_file(src)  # type: ignore[operator]
    assert _MODULE._scan_file(path) == []  # type: ignore[attr-defined]


def test_empty_splat_args_treated_as_bare(write_test_file: object) -> None:
    """``Mock(*())`` and ``Mock(**{})`` are still bare calls."""
    src = (
        "from unittest.mock import AsyncMock\n"
        "x = AsyncMock(*())\n"
        "y = AsyncMock(**{})\n"
        "z = AsyncMock(*[], **{})\n"
    )
    path = write_test_file(src)  # type: ignore[operator]
    hits = _MODULE._scan_file(path)  # type: ignore[attr-defined]
    assert len(hits) == 3
    assert {lineno for lineno, _ in hits} == {2, 3, 4}


def test_non_empty_splat_not_flagged(write_test_file: object) -> None:
    """Real splats with content are not bare calls."""
    src = (
        "from unittest.mock import AsyncMock\n"
        "args = (1, 2)\n"
        "x = AsyncMock(*args)\n"
        "y = AsyncMock(*[1])\n"
        "z = AsyncMock(**{'a': 1})\n"
    )
    path = write_test_file(src)  # type: ignore[operator]
    assert _MODULE._scan_file(path) == []  # type: ignore[attr-defined]


def test_unparseable_file_raises(write_test_file: object) -> None:
    src = "def broken(:\n"
    path = write_test_file(src)  # type: ignore[operator]
    with pytest.raises(_MODULE.InspectionError):  # type: ignore[attr-defined]
        _MODULE._scan_file(path)  # type: ignore[attr-defined]
