"""Unit tests for ``scripts/check_no_module_level_io.py``."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_module_level_io.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_no_module_level_io", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name


def _write(tmp_path: Path, content: str, name: str = "x.py") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ── Forbidden top-level calls ───────────────────────────────────


def test_top_level_open_flagged(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(_write(tmp_path, "open('foo')\n"))
    assert len(findings) == 1


def test_top_level_subprocess_run_flagged(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(
        _write(tmp_path, "import subprocess\nsubprocess.run(['x'])\n")
    )
    assert len(findings) == 1


def test_top_level_requests_get_flagged(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(
        _write(tmp_path, "import requests\nrequests.get('http://x')\n")
    )
    assert len(findings) == 1


def test_top_level_httpx_client_flagged(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(_write(tmp_path, "import httpx\nhttpx.Client()\n"))
    assert len(findings) == 1


def test_top_level_socket_flagged(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(
        _write(tmp_path, "import socket\nsocket.socket()\n")
    )
    assert len(findings) == 1


def test_top_level_path_read_text_flagged(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(
        _write(tmp_path, "from pathlib import Path\nPath('x').read_text()\n")
    )
    assert len(findings) == 1


def test_top_level_urllib_urlopen_flagged(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(
        _write(
            tmp_path,
            "import urllib.request\nurllib.request.urlopen('http://x')\n",
        )
    )
    assert len(findings) == 1


# ── Function-body calls allowed ─────────────────────────────────


def test_call_inside_function_allowed(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(
        _write(
            tmp_path,
            "def f():\n    open('foo')\n    return 1\n",
        )
    )
    assert findings == []


def test_call_inside_class_method_allowed(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(
        _write(
            tmp_path,
            "class A:\n    def m(self):\n        open('foo')\n",
        )
    )
    assert findings == []


# ── __main__ block allowed ──────────────────────────────────────


def test_call_inside_main_block_allowed(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(
        _write(
            tmp_path,
            'if __name__ == "__main__":\n    open("foo")\n',
        )
    )
    assert findings == []


def test_call_in_else_of_main_block_flagged(tmp_path: Path) -> None:
    """Else of a ``__main__`` guard executes on import; it must be scanned."""
    findings = _GATE.find_module_io(
        _write(
            tmp_path,
            (
                'if __name__ == "__main__":\n'
                '    open("ok-main-body")\n'
                "else:\n"
                '    open("flag-me")\n'
            ),
        )
    )
    assert len(findings) == 1
    assert findings[0].line == 4


# ── Suppression marker ──────────────────────────────────────────


def test_suppression_with_reason_accepts(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(
        _write(
            tmp_path,
            "open('foo')  # lint-allow: module-io -- preload at import\n",
        )
    )
    assert len(findings) == 1
    assert findings[0].suppressed is True


def test_suppression_without_reason_rejected(tmp_path: Path) -> None:
    findings = _GATE.find_module_io(
        _write(tmp_path, "open('foo')  # lint-allow: module-io\n")
    )
    assert len(findings) == 1
    assert findings[0].suppressed is False


# ── End-to-end check + baseline ─────────────────────────────────


def test_check_passes_clean_tree(tmp_path: Path) -> None:
    (tmp_path / "src" / "synthorg").mkdir(parents=True)
    (tmp_path / "src" / "synthorg" / "foo.py").write_text(
        "def f():\n    open('x')\n", encoding="utf-8"
    )
    baseline = tmp_path / "scripts" / "_module_level_io_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    assert _GATE.check(project_root=tmp_path, baseline_path=baseline) == []


def test_check_fails_top_level_open(tmp_path: Path) -> None:
    (tmp_path / "src" / "synthorg").mkdir(parents=True)
    (tmp_path / "src" / "synthorg" / "foo.py").write_text(
        "open('x')\n", encoding="utf-8"
    )
    baseline = tmp_path / "scripts" / "_module_level_io_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert len(violations) == 1


def test_baselined_violation_passes(tmp_path: Path) -> None:
    (tmp_path / "src" / "synthorg").mkdir(parents=True)
    (tmp_path / "src" / "synthorg" / "foo.py").write_text(
        "open('x')\n", encoding="utf-8"
    )
    baseline = tmp_path / "scripts" / "_module_level_io_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("src/synthorg/foo.py:1:open\n", encoding="utf-8")
    assert _GATE.check(project_root=tmp_path, baseline_path=baseline) == []
