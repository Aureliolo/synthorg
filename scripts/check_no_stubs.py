#!/usr/bin/env python3
"""Pre-push / CI gate: no stubs in application code.

Project convention (CLAUDE.md, MANDATORY "No Stubs"): a stub is a
placeholder that ships and then masquerades as working code. They are
banned outright under ``src/synthorg/``. Implement the behaviour fully,
or fail loud with a typed :class:`DomainError` (e.g.
``FeatureNotImplementedError``, code 8009), never a silent placeholder.

AST-based (string/comment mentions never false-positive). Flags, under
``src/synthorg/``:

* ``raise NotImplementedError`` in ANY body, with no exemption. An
  ``@abstractmethod`` / ``@overload`` / ``Protocol`` seam declares an
  interface-only body as ``...`` (Python already blocks instantiating a
  class with an unimplemented abstract method); a concrete method that
  cannot do its job fails loud with a typed :class:`DomainError`
  (e.g. ``FeatureNotImplementedError``). Either way the builtin
  ``NotImplementedError`` is never the right tool, so it is banned
  outright rather than merely in concrete bodies.
* A function/method whose body is exactly ``pass`` or ``...`` (after an
  optional docstring), unless the enclosing class is a ``Protocol``, the
  method is ``@abstractmethod`` / ``@overload``, or the definition sits
  inside an ``if TYPE_CHECKING:`` block (all three are legitimate
  interface-only declarations, not stubs).
* A source identifier that self-declares as a stub: a class named
  ``*Stub*``, or a string constant whose value is a ``stub:``-prefixed
  source identifier (e.g. the fabricated ``"stub:calibrated-v1"``
  benchmark source).
* A module file named ``*_stub.py`` / ``*_stubs.py``.

Per-line opt-out (mandatory non-empty reason)::

    raise NotImplementedError  # lint-allow: no-stub -- <reason>

Fail-closed on a syntax error. No baseline: the tree is expected clean;
a genuine stub is implemented or fail-loud'd, never frozen.

Usage::

    python scripts/check_no_stubs.py
    python scripts/check_no_stubs.py --repo-root /path/to/repo
"""

import argparse
import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, override

if TYPE_CHECKING:
    from collections.abc import Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_scope import select_scoped_files  # type: ignore[import-not-found]
else:
    from scripts._gate_scope import select_scoped_files

SCAN_ROOT: Final = Path("src/synthorg")
_SUPPRESSION_MARKER: Final = "lint-allow: no-stub"
_ABSTRACT_DECORATORS: Final = frozenset({"abstractmethod", "overload"})
_STUB_STRING_PREFIX: Final = "stub:"


@dataclass(frozen=True)
class Violation:
    """One stub in application code."""

    file: str
    lineno: int
    detail: str


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the ``no-stub`` suppression marker.

    The marker must be followed by ``--`` and non-empty justification
    text, mirroring the other ``scripts/check_*.py`` gates.

    Returns:
        True when the line ends in a valid opt-out comment.
    """
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
        if suffix.startswith("--") and suffix[2:].strip():
            return True
    return False


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return the trailing attribute name of each decorator on *node*.

    ``@abstractmethod`` and ``@abc.abstractmethod`` both yield
    ``"abstractmethod"`` so the caller can match on the bare name.

    Returns:
        The set of decorator leaf names.
    """
    names: set[str] = set()
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _class_is_protocol(node: ast.ClassDef) -> bool:
    """Return True iff *node* declares ``Protocol`` (or ``Protocol[...]``).

    Returns:
        True when any base resolves to ``Protocol``.
    """
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        if isinstance(target, ast.Name) and target.id == "Protocol":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "Protocol":
            return True
    return False


def _is_empty_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.stmt | None:
    """Return the placeholder statement iff *node*'s body is empty.

    A body is "empty" when, after an optional leading docstring, the only
    remaining statement is ``pass`` or a bare ``...`` expression.

    Returns:
        The offending ``pass`` / ``...`` statement, or ``None``.
    """
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1:
        return None
    stmt = body[0]
    if isinstance(stmt, ast.Pass):
        return stmt
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    ):
        return stmt
    return None


def _raises_not_implemented(node: ast.Raise) -> bool:
    """Return True iff *node* raises ``NotImplementedError``.

    Returns:
        True for ``raise NotImplementedError`` and
        ``raise NotImplementedError(...)``.
    """
    exc = node.exc
    if exc is None:
        return False
    target = exc.func if isinstance(exc, ast.Call) else exc
    if isinstance(target, ast.Name):
        return target.id == "NotImplementedError"
    if isinstance(target, ast.Attribute):
        return target.attr == "NotImplementedError"
    return False


class _StubVisitor(ast.NodeVisitor):
    """Collect stub violations while tracking class / decorator / guard context."""

    def __init__(self, rel: str, source_lines: list[str]) -> None:
        self._rel = rel
        self._lines = source_lines
        self.violations: list[Violation] = []
        self._protocol_stack: list[bool] = []
        self._type_checking_depth = 0

    def _marked(self, lineno: int) -> bool:
        idx = lineno - 1
        return 0 <= idx < len(self._lines) and _line_has_trailing_marker(
            self._lines[idx]
        )

    def _record(self, lineno: int, detail: str) -> None:
        if not self._marked(lineno):
            self.violations.append(Violation(self._rel, lineno, detail))

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if "Stub" in node.name:
            self._record(node.lineno, f"class {node.name} self-declares as a stub")
        self._protocol_stack.append(_class_is_protocol(node))
        self.generic_visit(node)
        self._protocol_stack.pop()

    def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        decorators = _decorator_names(node)
        exempt = bool(decorators & _ABSTRACT_DECORATORS)
        in_protocol = bool(self._protocol_stack and self._protocol_stack[-1])
        in_type_checking = self._type_checking_depth > 0
        placeholder = _is_empty_body(node)
        if (
            placeholder is not None
            and not exempt
            and not in_protocol
            and not in_type_checking
        ):
            kind = "pass" if isinstance(placeholder, ast.Pass) else "..."
            self._record(
                placeholder.lineno,
                f"function {node.name!r} has an empty ({kind}) body",
            )
        self.generic_visit(node)

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node)

    @override
    def visit_Raise(self, node: ast.Raise) -> None:
        if _raises_not_implemented(node):
            self._record(
                node.lineno,
                "raise NotImplementedError is banned everywhere: use '...' in an "
                "@abstractmethod / @overload / Protocol seam, or fail loud with a "
                "typed DomainError (e.g. FeatureNotImplementedError) in a concrete "
                "body",
            )
        self.generic_visit(node)

    @override
    def visit_If(self, node: ast.If) -> None:
        guard = node.test
        is_type_checking = (
            isinstance(guard, ast.Name) and guard.id == "TYPE_CHECKING"
        ) or (isinstance(guard, ast.Attribute) and guard.attr == "TYPE_CHECKING")
        if is_type_checking:
            self._type_checking_depth += 1
            for stmt in node.body:
                self.visit(stmt)
            self._type_checking_depth -= 1
            for stmt in node.orelse:
                self.visit(stmt)
        else:
            self.generic_visit(node)

    @override
    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value.startswith(_STUB_STRING_PREFIX):
            self._record(
                node.lineno,
                f"stub source identifier {node.value!r}",
            )


def _scan_file(path: Path, repo_root: Path) -> list[Violation]:
    """Return every stub violation in *path*.

    Returns:
        The violations found, empty when clean.

    Raises:
        SyntaxError: When the file cannot be parsed (fail-closed).
    """
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(repo_root).as_posix()
    out: list[Violation] = []
    stem = path.stem
    if stem.endswith(("_stub", "_stubs")):
        out.append(Violation(rel, 1, f"module {path.name} self-declares as a stub"))
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        msg = f"{path}: {exc}"
        raise SyntaxError(msg) from exc
    visitor = _StubVisitor(rel, text.splitlines())
    visitor.visit(tree)
    out.extend(visitor.violations)
    return out


def _iter_py_files(repo_root: Path) -> Iterable[Path]:
    base = repo_root / SCAN_ROOT
    if not base.is_dir():
        return
    yield from sorted(base.rglob("*.py"))


def _select_scoped_files(repo_root: Path, files: list[str]) -> list[Path]:
    """Return the in-scope subset of *files*, as absolute paths.

    Reports what it dropped so a routing table that has drifted out of step
    with ``SCAN_ROOT`` shows up as a diagnostic rather than as a clean scan.

    Args:
        repo_root: Resolved repository root.
        files: Caller-supplied paths, absolute or repo-relative.

    Returns:
        Absolute paths to scan, ordered deterministically.
    """
    result = select_scoped_files(
        files,
        project_root=repo_root,
        roots=[SCAN_ROOT],
        suffixes=frozenset({".py"}),
    )
    if not result.selected:
        print(
            f"check_no_stubs: {result.skipped} path(s) supplied, none under "
            f"{SCAN_ROOT.as_posix()}/; nothing scanned.",
            file=sys.stderr,
        )
    return [scoped.path for scoped in result.selected]


def _run(repo_root: Path, files: list[str] | None = None) -> int:
    """Scan the tree (or *files*) and report every stub found.

    Args:
        repo_root: Resolved repository root.
        files: Restrict the scan to these paths; ``None`` walks ``SCAN_ROOT``.

    Returns:
        ``0`` clean, ``1`` on violations.
    """
    violations: list[Violation] = []
    targets = (
        _select_scoped_files(repo_root, files)
        if files is not None
        else _iter_py_files(repo_root)
    )
    for py in targets:
        violations.extend(_scan_file(py, repo_root))
    if not violations:
        return 0
    print("Stubs found in application code:")
    for v in sorted(violations, key=lambda x: (x.file, x.lineno)):
        print(f"  {v.file}:{v.lineno} ({v.detail})")
    print(
        "\nFix: implement the behaviour fully, or fail loud with a typed "
        "DomainError (e.g. FeatureNotImplementedError). An interface-only seam "
        "belongs on an abc.ABC (@abstractmethod) or a @runtime_checkable "
        "Protocol. Genuine exceptions carry "
        "`# lint-allow: no-stub -- <reason>`.",
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; ``None`` reads ``sys.argv``.

    Returns:
        ``0`` clean, ``1`` on violations, ``2`` on a scan/parse error.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--files",
        nargs="+",
        default=None,
        help=(
            "Scan only these files instead of the whole scan root. Paths "
            "outside src/synthorg/ are skipped. Used by the agent-time "
            "PostToolUse dispatcher; the pre-push run always scans in full."
        ),
    )
    args = parser.parse_args(argv)
    try:
        return _run(args.repo_root.resolve(), args.files)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        print(f"check_no_stubs: scan error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
