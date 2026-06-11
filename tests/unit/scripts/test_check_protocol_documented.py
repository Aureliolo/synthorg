"""Unit tests for ``scripts/check_protocol_documented.py``."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_protocol_documented.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_protocol_documented", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── find_protocols ──────────────────────────────────────────────


def test_finds_basic_protocol_subclass(tmp_path: Path) -> None:
    src = (
        "from typing import Protocol\n"
        "class Foo(Protocol):\n"
        '    """Foo does X with non-trivial detail."""\n'
        "    def do(self) -> int: ...\n"
    )
    path = _write(tmp_path, "src/synthorg/foo.py", src)
    findings = _GATE.find_protocols(path)
    assert len(findings) == 1
    assert findings[0].name == "Foo"
    assert findings[0].has_docstring is True


def test_finds_runtime_checkable_protocol(tmp_path: Path) -> None:
    src = (
        "from typing import Protocol, runtime_checkable\n"
        "@runtime_checkable\n"
        "class Foo(Protocol):\n"
        '    """Foo does X with non-trivial detail."""\n'
    )
    path = _write(tmp_path, "src/synthorg/foo.py", src)
    findings = _GATE.find_protocols(path)
    assert len(findings) == 1
    assert findings[0].has_docstring is True


def test_protocol_without_docstring_marked(tmp_path: Path) -> None:
    src = (
        "from typing import Protocol\n"
        "class Foo(Protocol):\n"
        "    def do(self) -> int: ...\n"
    )
    path = _write(tmp_path, "src/synthorg/foo.py", src)
    findings = _GATE.find_protocols(path)
    assert len(findings) == 1
    assert findings[0].has_docstring is False


def test_trivial_docstring_marked_as_missing(tmp_path: Path) -> None:
    src = 'from typing import Protocol\nclass Foo(Protocol):\n    """TODO"""\n'
    path = _write(tmp_path, "src/synthorg/foo.py", src)
    findings = _GATE.find_protocols(path)
    assert len(findings) == 1
    assert findings[0].has_docstring is False


def test_non_protocol_class_ignored(tmp_path: Path) -> None:
    src = "class Foo:\n    def do(self) -> int: ...\n"
    path = _write(tmp_path, "src/synthorg/foo.py", src)
    findings = _GATE.find_protocols(path)
    assert findings == []


# ── Suppression marker ──────────────────────────────────────────


def test_suppression_marker_with_reason_accepts(tmp_path: Path) -> None:
    src = (
        "from typing import Protocol\n"
        "class Foo(Protocol):  # lint-allow: protocol-doc -- vendored stub\n"
        "    def do(self) -> int: ...\n"
    )
    path = _write(tmp_path, "src/synthorg/foo.py", src)
    findings = _GATE.find_protocols(path)
    assert len(findings) == 1
    assert findings[0].suppressed is True


def test_suppression_marker_without_reason_rejected(tmp_path: Path) -> None:
    src = (
        "from typing import Protocol\n"
        "class Foo(Protocol):  # lint-allow: protocol-doc\n"
        "    def do(self) -> int: ...\n"
    )
    path = _write(tmp_path, "src/synthorg/foo.py", src)
    findings = _GATE.find_protocols(path)
    assert len(findings) == 1
    assert findings[0].suppressed is False


# ── End-to-end check ────────────────────────────────────────────


def _empty_baseline(project: Path) -> Path:
    baseline = project / "scripts" / "_protocol_doc_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    return baseline


def test_check_passes_on_documented_protocol(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/synthorg/foo.py",
        (
            "from typing import Protocol\n"
            "class Foo(Protocol):\n"
            '    """Foo does X with non-trivial detail."""\n'
        ),
    )
    baseline = _empty_baseline(tmp_path)
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert violations == []


def test_check_fails_on_undocumented_protocol(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/synthorg/foo.py",
        (
            "from typing import Protocol\n"
            "class Foo(Protocol):\n"
            "    def do(self) -> int: ...\n"
        ),
    )
    baseline = _empty_baseline(tmp_path)
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert len(violations) == 1


def test_baselined_undocumented_protocol_passes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/synthorg/foo.py",
        (
            "from typing import Protocol\n"
            "class Foo(Protocol):\n"
            "    def do(self) -> int: ...\n"
        ),
    )
    baseline = tmp_path / "scripts" / "_protocol_doc_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("src/synthorg/foo.py:2:Foo\n", encoding="utf-8")
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert violations == []


# ── write_baseline idempotence ──────────────────────────────────


def test_write_baseline_is_idempotent(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/synthorg/foo.py",
        (
            "from typing import Protocol\n"
            "class Foo(Protocol):\n"
            "    def do(self) -> int: ...\n"
        ),
    )
    baseline = tmp_path / "scripts" / "_protocol_doc_baseline.txt"
    baseline.parent.mkdir(parents=True)
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline)
    first = baseline.read_text(encoding="utf-8")
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline)
    second = baseline.read_text(encoding="utf-8")
    assert first == second
