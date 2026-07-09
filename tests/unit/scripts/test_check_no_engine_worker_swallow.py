"""Unit tests for ``scripts/check_no_engine_worker_swallow.py``."""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "check_no_engine_worker_swallow.py"
)


class _Violation(Protocol):
    file: str
    lineno: int
    detail: str


class _ScriptModule(Protocol):
    @staticmethod
    def _scan_file(path: Path, repo_root: Path) -> list[_Violation]: ...
    @staticmethod
    def _line_has_marker(line: str) -> bool: ...
    @staticmethod
    def main() -> int: ...


def _load() -> _ScriptModule:
    spec = importlib.util.spec_from_file_location("_check_swallow", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_ScriptModule, module)


gate = _load()


def _scan(tmp_path: Path, source: str) -> list[_Violation]:
    base = tmp_path / "src" / "synthorg" / "engine"
    base.mkdir(parents=True, exist_ok=True)
    target = base / "m.py"
    target.write_text(source, encoding="utf-8")
    return gate._scan_file(target, tmp_path)


def test_broad_swallow_without_raise_flagged(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        logger.warning('boom')\n"
    )
    assert len(_scan(tmp_path, source)) == 1


def test_broad_swallow_that_reraises_is_clean(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        logger.warning('boom')\n"
        "        raise\n"
    )
    assert _scan(tmp_path, source) == []


def test_broad_swallow_raising_typed_error_is_clean(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n"
        "    try:\n"
        "        g()\n"
        "    except Exception as exc:\n"
        "        raise EngineError('wrapped') from exc\n"
    )
    assert _scan(tmp_path, source) == []


def test_marker_on_reraise_line_suppresses(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n"
        "    try:\n"
        "        g()\n"
        "    except Exception as exc:  # noqa: BLE001\n"
        "        reraise_critical(exc)  # lint-allow: swallow-ok -- best-effort ping\n"
        "        logger.warning('boom')\n"
    )
    assert _scan(tmp_path, source) == []


def test_marker_on_standalone_comment_line_suppresses(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        # lint-allow: swallow-ok -- best-effort notification\n"
        "        logger.warning('boom')\n"
    )
    assert _scan(tmp_path, source) == []


def test_marker_requires_non_empty_reason(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        logger.warning('boom')  # lint-allow: swallow-ok --\n"
    )
    assert len(_scan(tmp_path, source)) == 1


def test_narrow_except_not_flagged(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError:\n"
        "        logger.warning('boom')\n"
    )
    assert _scan(tmp_path, source) == []


def test_bare_except_without_raise_flagged(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n"
        "    try:\n"
        "        g()\n"
        "    except:  # noqa: E722\n"
        "        logger.warning('boom')\n"
    )
    assert len(_scan(tmp_path, source)) == 1


def test_tuple_including_exception_flagged(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n"
        "    try:\n"
        "        g()\n"
        "    except (ValueError, Exception):\n"
        "        logger.warning('boom')\n"
    )
    assert len(_scan(tmp_path, source)) == 1


def test_critical_reraise_pair_is_clean(tmp_path: Path) -> None:
    # PEP 758 unparenthesised critical re-raise: has a raise, not a swallow.
    source = (
        "def f() -> None:\n"
        "    try:\n"
        "        g()\n"
        "    except MemoryError, RecursionError:\n"
        "        raise\n"
    )
    assert _scan(tmp_path, source) == []


def test_syntax_error_fails_closed(tmp_path: Path) -> None:
    base = tmp_path / "src" / "synthorg" / "workers"
    base.mkdir(parents=True, exist_ok=True)
    target = base / "bad.py"
    target.write_text("def f(:\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        gate._scan_file(target, tmp_path)


def test_main_clean_tree_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "src" / "synthorg" / "engine"
    base.mkdir(parents=True, exist_ok=True)
    (base / "ok.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_no_engine_worker_swallow.py", "--repo-root", str(tmp_path)],
    )
    assert gate.main() == 0
