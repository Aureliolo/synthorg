"""Tests for the error-code-uniqueness AST gate."""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


class _GateModule(Protocol):
    """Subset of ``scripts/check_error_code_uniqueness.py`` the tests exercise."""

    SHAREABLE_CODES: frozenset[str]

    @staticmethod
    def _line_has_trailing_marker(line: str) -> bool: ...
    @staticmethod
    def _scan_tree(project_root: Path, scan_root: Path) -> list[str]: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_module() -> _GateModule:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "check_error_code_uniqueness.py"
    spec = importlib.util.spec_from_file_location(
        "check_error_code_uniqueness",
        script_path,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()

_DOMAIN_ERRORS = (
    "class DomainError(Exception):\n"
    "    error_code = ErrorCode.INTERNAL_ERROR\n"
    "\n"
    "class NotFoundError(DomainError):\n"
    "    error_code = ErrorCode.RESOURCE_NOT_FOUND\n"
)


def _make_project(tmp_path: Path, files: dict[str, str]) -> tuple[Path, Path]:
    """Materialise a synthetic ``src/synthorg/`` tree under *tmp_path*."""
    project_root = tmp_path
    src_root = project_root / "src" / "synthorg"
    (src_root / "core").mkdir(parents=True)
    (src_root / "__init__.py").write_text("", encoding="utf-8")
    (src_root / "core" / "__init__.py").write_text("", encoding="utf-8")
    (src_root / "core" / "domain_errors.py").write_text(
        _DOMAIN_ERRORS, encoding="utf-8"
    )
    for rel, content in files.items():
        target = project_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        for parent in target.parents:
            if parent == project_root:
                break
            init = parent / "__init__.py"
            if not init.exists():
                init.write_text("", encoding="utf-8")
    return project_root, src_root


# ── real tree ────────────────────────────────────────────────────


def test_real_tree_passes() -> None:
    """The committed src/synthorg tree has no duplicate-code violations."""
    assert _MODULE.main([]) == 0


# ── suppression marker ───────────────────────────────────────────


def test_marker_requires_justification() -> None:
    bare = "class Foo(DomainError):  # lint-allow: error-code-uniqueness --"
    full = (
        "class Foo(DomainError):  # lint-allow: error-code-uniqueness "
        "-- documented cross-layer twin"
    )
    assert _MODULE._line_has_trailing_marker(bare) is False
    assert _MODULE._line_has_trailing_marker(full) is True


# ── violation detection ──────────────────────────────────────────


def test_unrelated_classes_same_specific_code_flagged(tmp_path: Path) -> None:
    """Two unrelated classes sharing one non-generic code is a violation."""
    project_root, src_root = _make_project(
        tmp_path,
        {
            "src/synthorg/a/errors.py": (
                "from synthorg.core.domain_errors import NotFoundError\n"
                "class WidgetNotFoundError(NotFoundError):\n"
                "    error_code = ErrorCode.WIDGET_NOT_FOUND\n"
            ),
            "src/synthorg/b/errors.py": (
                "from synthorg.core.domain_errors import NotFoundError\n"
                "class GadgetNotFoundError(NotFoundError):\n"
                "    error_code = ErrorCode.WIDGET_NOT_FOUND\n"
            ),
        },
    )
    messages = _MODULE._scan_tree(project_root, src_root)
    assert any("WIDGET_NOT_FOUND" in m for m in messages), messages


def test_inheritance_alias_allowed(tmp_path: Path) -> None:
    """A subclass re-declaring an ancestor's code is an allowed alias."""
    project_root, src_root = _make_project(
        tmp_path,
        {
            "src/synthorg/a/errors.py": (
                "from synthorg.core.domain_errors import NotFoundError\n"
                "class WidgetNotFoundError(NotFoundError):\n"
                "    error_code = ErrorCode.WIDGET_NOT_FOUND\n"
                "class SpecificWidgetNotFoundError(WidgetNotFoundError):\n"
                "    error_code = ErrorCode.WIDGET_NOT_FOUND\n"
            ),
        },
    )
    assert _MODULE._scan_tree(project_root, src_root) == []


def test_inheritance_via_aliased_module_import_allowed(tmp_path: Path) -> None:
    """A base referenced through an aliased module import resolves correctly.

    ``from synthorg.core import domain_errors`` then inheriting from
    ``domain_errors.NotFoundError`` must resolve the ancestor so that
    re-declaring its code stays an allowed inheritance alias rather than
    reading as two unrelated classes sharing one specific code.
    """
    project_root, src_root = _make_project(
        tmp_path,
        {
            "src/synthorg/a/errors.py": (
                "from synthorg.core import domain_errors\n"
                "class WidgetNotFoundError(domain_errors.NotFoundError):\n"
                "    error_code = ErrorCode.RESOURCE_NOT_FOUND\n"
            ),
        },
    )
    assert _MODULE._scan_tree(project_root, src_root) == []


def test_shareable_code_allowed(tmp_path: Path) -> None:
    """A generic category fallback may be carried by unrelated classes."""
    project_root, src_root = _make_project(
        tmp_path,
        {
            "src/synthorg/a/errors.py": (
                "from synthorg.core.domain_errors import DomainError\n"
                "class AlphaError(DomainError):\n"
                "    error_code = ErrorCode.INTERNAL_ERROR\n"
            ),
            "src/synthorg/b/errors.py": (
                "from synthorg.core.domain_errors import DomainError\n"
                "class BetaError(DomainError):\n"
                "    error_code = ErrorCode.INTERNAL_ERROR\n"
            ),
        },
    )
    assert _MODULE._scan_tree(project_root, src_root) == []


def test_lint_allow_suppresses(tmp_path: Path) -> None:
    """A justified lint-allow on one sibling clears the violation."""
    project_root, src_root = _make_project(
        tmp_path,
        {
            "src/synthorg/a/errors.py": (
                "from synthorg.core.domain_errors import NotFoundError\n"
                "class WidgetNotFoundError(NotFoundError):\n"
                "    error_code = ErrorCode.WIDGET_NOT_FOUND\n"
            ),
            "src/synthorg/b/errors.py": (
                "from synthorg.core.domain_errors import NotFoundError\n"
                "class GadgetNotFoundError("
                "  # lint-allow: error-code-uniqueness -- intentional twin\n"
                "    NotFoundError\n"
                "):\n"
                "    error_code = ErrorCode.WIDGET_NOT_FOUND\n"
            ),
        },
    )
    assert _MODULE._scan_tree(project_root, src_root) == []
