"""Unit tests for the ``scripts/check_no_stubs.py`` convention gate."""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_no_stubs.py"


class _Violation(Protocol):
    """Structural view of the script's private ``Violation`` class."""

    file: str
    lineno: int
    detail: str


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    @staticmethod
    def _scan_file(path: Path, repo_root: Path) -> list[_Violation]: ...
    @staticmethod
    def _line_has_trailing_marker(line: str) -> bool: ...
    @staticmethod
    def main() -> int: ...


def _load() -> _ScriptModule:
    spec = importlib.util.spec_from_file_location("_check_no_stubs", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_ScriptModule, module)


gate = _load()


def _scan(tmp_path: Path, rel: str, source: str) -> list[_Violation]:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return gate._scan_file(target, tmp_path)


def test_concrete_raise_not_implemented_flagged(tmp_path: Path) -> None:
    source = "def f() -> None:\n    raise NotImplementedError\n"
    violations = _scan(tmp_path, "m.py", source)
    assert len(violations) == 1
    assert "NotImplementedError" in violations[0].detail


def test_raise_not_implemented_with_args_flagged(tmp_path: Path) -> None:
    source = 'def f() -> None:\n    raise NotImplementedError("nope")\n'
    assert len(_scan(tmp_path, "m.py", source)) == 1


def test_abstractmethod_raise_exempt(tmp_path: Path) -> None:
    source = (
        "from abc import ABC, abstractmethod\n\n\n"
        "class C(ABC):\n"
        "    @abstractmethod\n"
        "    def f(self) -> None:\n"
        "        raise NotImplementedError\n"
    )
    assert _scan(tmp_path, "m.py", source) == []


def test_overload_raise_exempt(tmp_path: Path) -> None:
    source = (
        "from typing import overload\n\n\n"
        "@overload\n"
        "def f(x: int) -> int:\n"
        "    raise NotImplementedError\n"
    )
    assert _scan(tmp_path, "m.py", source) == []


def test_lint_allow_marker_suppresses(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n"
        "    raise NotImplementedError  # lint-allow: no-stub -- documented gap\n"
    )
    assert _scan(tmp_path, "m.py", source) == []


def test_lint_allow_marker_requires_reason(tmp_path: Path) -> None:
    source = (
        "def f() -> None:\n    raise NotImplementedError  # lint-allow: no-stub --\n"
    )
    assert len(_scan(tmp_path, "m.py", source)) == 1


def test_empty_pass_body_flagged(tmp_path: Path) -> None:
    source = "def f() -> None:\n    pass\n"
    violations = _scan(tmp_path, "m.py", source)
    assert len(violations) == 1
    assert "pass" in violations[0].detail


def test_empty_ellipsis_body_flagged(tmp_path: Path) -> None:
    source = "def f() -> None:\n    ...\n"
    violations = _scan(tmp_path, "m.py", source)
    assert len(violations) == 1
    assert "..." in violations[0].detail


def test_protocol_ellipsis_body_exempt(tmp_path: Path) -> None:
    source = (
        "from typing import Protocol, runtime_checkable\n\n\n"
        "@runtime_checkable\n"
        "class P(Protocol):\n"
        "    def f(self) -> None:\n"
        '        """Docstring."""\n'
        "        ...\n"
    )
    assert _scan(tmp_path, "m.py", source) == []


def test_protocol_subscript_base_exempt(tmp_path: Path) -> None:
    source = (
        "from typing import Protocol, TypeVar\n\n\n"
        "T = TypeVar('T')\n\n\n"
        "class P(Protocol[T]):\n"
        "    def f(self) -> None:\n"
        "        ...\n"
    )
    assert _scan(tmp_path, "m.py", source) == []


def test_abstractmethod_ellipsis_body_exempt(tmp_path: Path) -> None:
    source = (
        "from abc import ABC, abstractmethod\n\n\n"
        "class C(ABC):\n"
        "    @abstractmethod\n"
        "    def f(self) -> None:\n"
        "        ...\n"
    )
    assert _scan(tmp_path, "m.py", source) == []


def test_type_checking_ellipsis_body_exempt(tmp_path: Path) -> None:
    source = (
        "from typing import TYPE_CHECKING\n\n\n"
        "if TYPE_CHECKING:\n"
        "    def sibling(self) -> int:\n"
        "        ...\n"
    )
    assert _scan(tmp_path, "m.py", source) == []


def test_real_body_not_flagged(tmp_path: Path) -> None:
    source = "def f() -> int:\n    return 1\n"
    assert _scan(tmp_path, "m.py", source) == []


def test_stub_class_name_flagged(tmp_path: Path) -> None:
    source = "class StubProvider:\n    def f(self) -> int:\n        return 1\n"
    violations = _scan(tmp_path, "m.py", source)
    assert len(violations) == 1
    assert "self-declares as a stub" in violations[0].detail


def test_stub_source_identifier_flagged(tmp_path: Path) -> None:
    source = 'SOURCE = "stub:calibrated-v1"\n'
    violations = _scan(tmp_path, "m.py", source)
    assert len(violations) == 1
    assert "stub source identifier" in violations[0].detail


def test_non_stub_string_not_flagged(tmp_path: Path) -> None:
    source = 'DESC = "Identified failure patterns (stub: empty)"\n'
    assert _scan(tmp_path, "m.py", source) == []


def test_stub_module_name_flagged(tmp_path: Path) -> None:
    violations = _scan(tmp_path, "benchmark_stub.py", "X = 1\n")
    assert any("self-declares as a stub" in v.detail for v in violations)


def test_stubs_plural_module_name_flagged(tmp_path: Path) -> None:
    violations = _scan(tmp_path, "integration_stubs.py", "X = 1\n")
    assert any("self-declares as a stub" in v.detail for v in violations)


def test_docstring_ellipsis_not_flagged(tmp_path: Path) -> None:
    source = 'def f() -> int:\n    """Example: x = ...\n    """\n    return 1\n'
    assert _scan(tmp_path, "m.py", source) == []


def test_docstring_only_body_not_flagged(tmp_path: Path) -> None:
    # A concrete method whose body is exactly a docstring is the sanctioned
    # host-provided mixin seam (the host overrides it); it is neither a bare
    # ``pass`` nor ``...`` stub, so it must not be flagged.
    source = (
        "class C:\n"
        "    def hook(self) -> None:\n"
        '        """Host-provided seam; overridden by the composing host."""\n'
    )
    assert _scan(tmp_path, "m.py", source) == []


def test_syntax_error_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "bad.py"
    target.write_text("def f(:\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        gate._scan_file(target, tmp_path)


def test_main_clean_tree_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src" / "synthorg").mkdir(parents=True)
    (tmp_path / "src" / "synthorg" / "ok.py").write_text(
        "def f() -> int:\n    return 1\n", encoding="utf-8"
    )
    monkeypatch.setattr("sys.argv", ["check_no_stubs.py", "--repo-root", str(tmp_path)])
    assert gate.main() == 0


def test_main_reports_violations_returns_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src" / "synthorg").mkdir(parents=True)
    (tmp_path / "src" / "synthorg" / "bad.py").write_text(
        "def f() -> None:\n    raise NotImplementedError\n", encoding="utf-8"
    )
    monkeypatch.setattr("sys.argv", ["check_no_stubs.py", "--repo-root", str(tmp_path)])
    assert gate.main() == 1


def test_main_returns_two_on_syntax_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src" / "synthorg").mkdir(parents=True)
    (tmp_path / "src" / "synthorg" / "broken.py").write_text(
        "def f(:\n", encoding="utf-8"
    )
    monkeypatch.setattr("sys.argv", ["check_no_stubs.py", "--repo-root", str(tmp_path)])
    assert gate.main() == 2
