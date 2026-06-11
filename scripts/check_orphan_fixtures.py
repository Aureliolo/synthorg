r"""Detect orphan pytest fixtures in ``tests/**/conftest.py``.

An orphan fixture is one that:

1. Is declared via ``@pytest.fixture`` (or ``@fixture``) in a
   ``conftest.py`` under ``tests/``.
2. Is NOT referenced by any:
   - argument name in a test or fixture under ``tests/``,
   - string literal passed to ``request.getfixturevalue(...)``,
   - string literal passed to ``@pytest.mark.usefixtures(...)``,
   - fixture defined inside a module named in a ``pytest_plugins`` list
     (such modules are imported by pytest and their fixtures are
     injected into the collecting package).
3. Is NOT declared with ``autouse=True`` (autouse fixtures are always
   live, even without explicit references).

Orphans are reported as ``file:line name`` with the function-def line
number so editors jump to the right place.

## Usage

Pre-push gate -- runs on every push::

    uv run python scripts/check_orphan_fixtures.py

Fails closed: a new orphan fixture (exit 1) or a conftest/test file that
cannot be parsed (exit 2, since an unscanned file could hide a reference
that would otherwise clear an orphan).

## Per-line opt-out

Add ``# lint-allow: orphan-fixture -- <non-empty justification>`` as a
trailing comment on the decorator line to silence a single report
(mirrors the convention used by ``check_persistence_boundary.py``).
"""

import argparse
import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

_SUPPRESSION_MARKER: Final[str] = "lint-allow: orphan-fixture"

# Fixtures consumed by the pytest framework itself (or by a plugin)
# rather than by user code.  These are never referenced as function
# arguments and must not be flagged.
_KNOWN_FRAMEWORK_FIXTURES: Final[frozenset[str]] = frozenset(
    {
        "event_loop",
        "event_loop_policy",
    }
)


@dataclass(frozen=True)
class Orphan:
    """A single orphan-fixture report."""

    file: str
    line: int
    name: str


# ── AST helpers ──────────────────────────────────────────────────


def _is_pytest_fixture_decorator(node: ast.expr) -> bool:
    """Return True iff *node* is a ``@pytest.fixture`` / ``@fixture`` decorator.

    Accepts both the call form (``@pytest.fixture(...)``) and the bare
    attribute / name form (``@pytest.fixture``, ``@fixture``).
    """
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute) and target.attr == "fixture":
        return isinstance(target.value, ast.Name) and target.value.id == "pytest"
    return bool(isinstance(target, ast.Name) and target.id == "fixture")


def _fixture_metadata(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, bool] | None:
    """Return ``(declared_name, autouse)`` if *func* is a pytest fixture.

    The declared name is the function name unless ``name="..."`` is
    supplied in the decorator call.  ``autouse`` is True when the
    decorator sets ``autouse=True``.
    """
    for dec in func.decorator_list:
        if not _is_pytest_fixture_decorator(dec):
            continue
        declared_name = func.name
        autouse = False
        if isinstance(dec, ast.Call):
            for kw in dec.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        declared_name = kw.value.value
                elif kw.arg == "autouse" and isinstance(kw.value, ast.Constant):
                    autouse = bool(kw.value.value)
        return declared_name, autouse
    return None


def _line_has_suppression(file_path: Path, decorator_line: int) -> bool:
    """Return True iff the decorator line carries a justified opt-out."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return False
    lines = text.splitlines()
    if not 1 <= decorator_line <= len(lines):
        return False
    line = lines[decorator_line - 1]
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        comment = tok.string.lstrip("#").strip()
        if not comment.startswith(_SUPPRESSION_MARKER):
            continue
        suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
        if suffix.startswith("--"):
            justification = suffix[2:].strip()
            if justification:
                return True
    return False


# ── Fixture declarations ─────────────────────────────────────────


@dataclass(frozen=True)
class _Declaration:
    file: Path
    func_line: int
    decorator_line: int
    name: str
    fixture_dep_names: frozenset[str]
    autouse: bool


def _collect_declarations(
    conftest_path: Path,
    skipped: list[tuple[Path, str]] | None = None,
) -> list[_Declaration]:
    """Return every pytest fixture declared in *conftest_path*.

    Unreadable or unparseable files are appended to *skipped* (when
    provided) as ``(path, reason)`` so the caller can surface the
    incomplete scan instead of silently dropping files.
    """
    try:
        source = conftest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        if skipped is not None:
            skipped.append((conftest_path, f"read failed: {type(exc).__name__}"))
        return []
    try:
        tree = ast.parse(source, filename=str(conftest_path))
    except SyntaxError as exc:
        if skipped is not None:
            skipped.append((conftest_path, f"parse failed: line {exc.lineno}"))
        return []
    declarations: list[_Declaration] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        meta = _fixture_metadata(node)
        if meta is None:
            continue
        declared_name, autouse = meta
        decorator_line = min(
            (getattr(dec, "lineno", node.lineno) for dec in node.decorator_list),
            default=node.lineno,
        )
        dep_names = frozenset(
            arg.arg for arg in node.args.args if arg.arg not in {"self", "request"}
        )
        declarations.append(
            _Declaration(
                file=conftest_path,
                func_line=node.lineno,
                decorator_line=decorator_line,
                name=declared_name,
                fixture_dep_names=dep_names,
                autouse=autouse,
            )
        )
    return declarations


# ── Reference collection ─────────────────────────────────────────


class _ReferenceVisitor(ast.NodeVisitor):
    """Collect every name that might refer to a fixture."""

    def __init__(self) -> None:
        self.argument_names: set[str] = set()
        self.string_references: set[str] = set()
        self.pytest_plugins: set[str] = set()
        self.imported_names: set[str] = set()

    # Every function / test parameter is a potential fixture reference.
    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for arg in node.args.args:
            if arg.arg not in {"self", "cls", "request"}:
                self.argument_names.add(arg.arg)
        self.generic_visit(node)

    @override
    def visit_AsyncFunctionDef(
        self,
        node: ast.AsyncFunctionDef,
    ) -> None:
        for arg in node.args.args:
            if arg.arg not in {"self", "cls", "request"}:
                self.argument_names.add(arg.arg)
        self.generic_visit(node)

    # ``request.getfixturevalue("foo")`` and
    # ``pytest.mark.usefixtures("foo", ...)``.
    @override
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and (
            func.attr == "getfixturevalue"
            or (
                func.attr == "usefixtures"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "mark"
            )
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    self.string_references.add(arg.value)
        self.generic_visit(node)

    # Module-level ``pytest_plugins = [...]`` or ``pytest_plugins = ("...",)``.
    @override
    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "pytest_plugins":
                self._extract_plugin_names(node.value)
        self.generic_visit(node)

    # ``from foo import X, Y as Z`` -- a conftest re-exporting fixtures
    # from a sibling conftest relies on the imported name being visible
    # at module scope.  Treat the imported name (and its alias) as a
    # live fixture reference.
    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.imported_names.add(alias.name)
            if alias.asname is not None:
                self.imported_names.add(alias.asname)
        self.generic_visit(node)

    def _extract_plugin_names(self, value: ast.expr) -> None:
        if isinstance(value, (ast.List, ast.Tuple)):
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    self.pytest_plugins.add(elt.value)


@dataclass(frozen=True)
class _References:
    argument_names: frozenset[str]
    string_references: frozenset[str]
    pytest_plugin_modules: frozenset[str]
    imported_names: frozenset[str]


def _collect_references(
    test_files: list[Path],
    skipped: list[tuple[Path, str]] | None = None,
) -> _References:
    """Walk every ``*.py`` under the test tree and collect references.

    Unreadable or unparseable files are recorded in *skipped* (when
    provided) so the scan result never pretends a corrupted file was
    fully analysed.
    """
    visitor = _ReferenceVisitor()
    for path in test_files:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            if skipped is not None:
                skipped.append((path, f"read failed: {type(exc).__name__}"))
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            if skipped is not None:
                skipped.append((path, f"parse failed: line {exc.lineno}"))
            continue
        visitor.visit(tree)
    return _References(
        argument_names=frozenset(visitor.argument_names),
        string_references=frozenset(visitor.string_references),
        pytest_plugin_modules=frozenset(visitor.pytest_plugins),
        imported_names=frozenset(visitor.imported_names),
    )


# ── Plugin-module suppression ───────────────────────────────────


def _file_is_under_plugin_module(
    file_path: Path,
    test_root: Path,
    plugin_modules: frozenset[str] | set[str],
) -> bool:
    """Return True iff *file_path* matches any ``pytest_plugins`` entry.

    ``pytest_plugins`` entries are dotted module paths.  Translate each
    into an on-disk path under the project root and check for a match.
    """
    if not plugin_modules:
        return False
    project_root = test_root.parent
    try:
        rel = file_path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return False
    # ``tests/plugins/shared_fixtures.py`` -> module ``tests.plugins.shared_fixtures``.
    module = ".".join(rel.with_suffix("").parts)
    return any(
        module == entry or module.startswith(entry + ".") for entry in plugin_modules
    )


# ── Public entry point ──────────────────────────────────────────


def _iter_test_files(test_root: Path) -> list[Path]:
    """Return every ``*.py`` file under *test_root* (recursive)."""
    return [p for p in test_root.rglob("*.py") if p.is_file()]


def _iter_conftests(test_root: Path) -> list[Path]:
    """Return every ``conftest.py`` under *test_root* (recursive)."""
    return [p for p in test_root.rglob("conftest.py") if p.is_file()]


def _filter_orphans(
    declarations: list[_Declaration],
    live_names: frozenset[str] | set[str],
    test_root: Path,
    plugin_modules: frozenset[str] | set[str],
) -> list[Orphan]:
    """Project *declarations* onto ``Orphan`` entries, skipping live ones."""
    orphans: list[Orphan] = []
    for decl in declarations:
        if decl.autouse:
            continue
        if decl.name in live_names:
            continue
        if _file_is_under_plugin_module(decl.file, test_root, plugin_modules):
            continue
        if _line_has_suppression(decl.file, decl.decorator_line):
            continue
        orphans.append(
            Orphan(
                file=str(decl.file),
                line=decl.func_line,
                name=decl.name,
            )
        )
    return orphans


def find_orphans(
    test_root: Path,
    skipped: list[tuple[Path, str]] | None = None,
) -> list[Orphan]:
    """Return every orphan fixture under *test_root*.

    Two-pass scan: first collect every fixture declaration under
    ``conftest.py`` files, then walk the whole test tree to gather
    references, then difference.

    Args:
        test_root: Root of the test tree to scan.
        skipped: Optional list that receives ``(path, reason)`` entries
            for every file that could not be read or parsed.  When
            ``None`` (default), unreadable files are silently ignored
            so the existing call sites keep working.  The CLI entry
            point passes a list so the report surfaces these gaps.
    """
    declarations: list[_Declaration] = []
    for conftest in _iter_conftests(test_root):
        declarations.extend(_collect_declarations(conftest, skipped=skipped))
    references = _collect_references(_iter_test_files(test_root), skipped=skipped)

    # Fixture-to-fixture dependencies are themselves references.
    fixture_dep_names: set[str] = set()
    for decl in declarations:
        fixture_dep_names |= decl.fixture_dep_names
    live_names = (
        references.argument_names
        | references.string_references
        | references.imported_names
        | fixture_dep_names
        | _KNOWN_FRAMEWORK_FIXTURES
    )
    return _filter_orphans(
        declarations,
        live_names,
        test_root,
        references.pytest_plugin_modules,
    )


# ── CLI ──────────────────────────────────────────────────────────


def _resolve_project_root() -> Path:
    """Resolve the project root (ancestor directory of this script)."""
    return Path(__file__).resolve().parent.parent


def _detect_test_root(project_root: Path) -> Path:
    """Return the tests directory anchored under *project_root*."""
    return project_root / "tests"


def _report_results(
    orphans: list[Orphan],
    skipped: list[tuple[Path, str]],
    project_root: Path,
) -> int:
    """Print skip warnings + orphan findings; return the CLI exit code."""
    if skipped:
        print(
            f"\nWARNING: {len(skipped)} file(s) could not be fully scanned "
            "-- orphan detection may be incomplete for fixtures defined "
            "in or referenced by these files:",
            file=sys.stderr,
        )
        for path, reason in skipped:
            try:
                rel = path.resolve().relative_to(project_root.resolve())
                display = rel.as_posix()
            except ValueError:
                display = str(path)
            print(f"  {display}: {reason}", file=sys.stderr)

    for orphan in orphans:
        # Emit as ``file:line name`` with project-relative path when
        # possible so editors jump to the right place.
        try:
            rel = Path(orphan.file).resolve().relative_to(project_root.resolve())
            file_display: str = rel.as_posix()
        except ValueError:
            file_display = orphan.file
        print(f"{file_display}:{orphan.line} {orphan.name}")
    if orphans:
        print(
            f"\n{len(orphans)} orphan fixture(s) detected.  "
            "Delete each one, or add "
            "'# lint-allow: orphan-fixture -- <reason>' to its decorator "
            "line if the exception is genuinely sanctioned.",
            file=sys.stderr,
        )

    # Fail closed: an unparseable conftest/test could hide a reference that
    # would otherwise clear an orphan, so an incomplete scan is a hard error.
    if skipped:
        return 2
    return 1 if orphans else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-root",
        type=Path,
        default=None,
        help="Root of the test tree (defaults to <project_root>/tests).",
    )
    args = parser.parse_args(argv)

    project_root = _resolve_project_root()
    test_root = args.test_root or _detect_test_root(project_root)
    if not test_root.is_dir():
        print(f"test root not found: {test_root}", file=sys.stderr)
        return 2

    skipped: list[tuple[Path, str]] = []
    orphans = find_orphans(test_root, skipped=skipped)
    return _report_results(orphans, skipped, project_root)


if __name__ == "__main__":
    sys.exit(main())
