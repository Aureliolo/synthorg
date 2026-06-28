#!/usr/bin/env python3
"""Pre-push / CI gate: every prompt class exposes ``metadata -> ModelPinMetadata``.

A prompt class is one that attributes an LLM call to a registered system prompt
purpose: it passes a non-``None`` ``purpose=`` keyword to a cost / completion
chokepoint (``cost_recording_scope`` / ``complete_text`` /
``complete_structured_text``). Passing a ``PromptPurposeId`` IS the act of
declaring "I am prompt class X", so any class that does it must also expose its
pin via a ``metadata`` property returning
:class:`synthorg.llm.metadata.ModelPinMetadata`. The pin feeds two consumers
(cost attribution by purpose and the pin-validation drift benchmark); a prompt
class that tags its spend but exposes no pin is a silent attribution gap.

A class that passes only ``purpose=None`` is, by that same convention, declaring
it has NO registered system prompt purpose (a per-task / agent-execution call),
so it is out of scope and needs no pin.

Detection
---------
AST-walk every tracked ``*.py`` under ``src/synthorg/``. A ``ClassDef`` is
in-scope when any call in its body (excluding nested class bodies) passes a
``purpose=`` keyword whose value is not the ``None`` literal. An in-scope class
passes when it declares a ``metadata`` property (``@property`` on a method named
``metadata``) whose return annotation is ``ModelPinMetadata`` -- concrete or
``@abstractmethod`` both satisfy it.

Allowlist / opt-out
-------------------
Per-class opt-out: put ``# lint-allow: prompt-class-metadata -- <reason>`` on any
line within the class. The justification after ``--`` is required and non-empty.

Usage::

    uv run python scripts/check_prompt_class_metadata.py
    uv run python scripts/check_prompt_class_metadata.py --scan-all

Exit codes:
    0 -- every in-scope prompt class exposes ``metadata``.
    1 -- an in-scope prompt class is missing the ``metadata`` property.
    2 -- configuration error (bad ``--repo-root``, or a source file that could
         not be read, parsed, or tokenised -- fail-closed).
"""

import argparse
import ast
import io
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_SCAN_ROOT_REL: Final[str] = "src/synthorg"
_PURPOSE_KEYWORD: Final[str] = "purpose"
#: The LLM cost / completion chokepoints whose ``purpose=`` attributes spend to a
#: ``PromptPurposeId``. Only these carry the prompt-class signal; an unrelated
#: ``purpose=`` keyword (e.g. a code-execution purpose) must not pull a class in.
_CHOKEPOINT_CALLS: Final[frozenset[str]] = frozenset(
    {"cost_recording_scope", "complete_text", "complete_structured_text"},
)
_METADATA_PROPERTY: Final[str] = "metadata"
_PIN_TYPE: Final[str] = "ModelPinMetadata"
_PROPERTY_DECORATOR: Final[str] = "property"
_SUPPRESSION_MARKER: Final[str] = "lint-allow: prompt-class-metadata"


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _Hit:
    """One in-scope prompt class missing its ``metadata`` property."""

    rel: str
    lineno: int
    name: str

    def message(self) -> str:
        """Return the human-facing violation message."""
        return (
            f"{self.rel}:{self.lineno}: class {self.name!r} passes a non-None "
            f"'{_PURPOSE_KEYWORD}=' but exposes no '{_METADATA_PROPERTY}' "
            f"property returning {_PIN_TYPE}."
        )


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments.

    Returns:
        The resolved project-root directory.

    Raises:
        ProjectRootError: If *repo_root* cannot be resolved to an existing
            directory, or does not contain the ``src/synthorg`` scan root.
    """
    if repo_root is None:
        resolved = _REPO_ROOT
    else:
        try:
            resolved = repo_root.resolve(strict=True)
        except OSError as exc:
            msg = f"--repo-root not accessible: {repo_root} ({exc})"
            raise ProjectRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise ProjectRootError(msg)
    # Fail closed: an existing-but-wrong root would scan zero files and exit
    # 0, silently disabling the gate. Require the actual scan root to exist.
    scan_root = resolved / _SCAN_ROOT_REL
    if not scan_root.is_dir():
        msg = f"--repo-root is not the synthorg repo root: missing {scan_root}"
        raise ProjectRootError(msg)
    return resolved


def _git_tracked_python_files(
    abs_root: Path,
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` under *abs_root* as ``(abs, rel)``.

    Falls back to :meth:`Path.rglob` when ``git`` is unavailable, emitting a
    stderr warning so the widened scope is visible rather than silent.

    Returns:
        A list of ``(absolute_path, posix_relative_path)`` pairs.
    """
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", rel_root],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        print(
            f"check_prompt_class_metadata: git ls-files failed in "
            f"{project_root} ({type(exc).__name__}: {exc}); falling back "
            f"to rglob (scope widens to include untracked / gitignored files).",
            file=sys.stderr,
        )
        return [
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p and p.endswith(".py")]
    return [((project_root / rel_path), rel_path) for rel_path in paths]


def _is_valid_marker(comment_token: str) -> bool:
    """Return True iff *comment_token* is a justified suppression marker.

    Returns:
        ``True`` for ``# lint-allow: prompt-class-metadata -- <reason>``.
    """
    comment = comment_token.lstrip("#").strip()
    if not comment.startswith(_SUPPRESSION_MARKER):
        return False
    suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
    return suffix.startswith("--") and bool(suffix[2:].strip())


def _marker_lines(text: str, rel: str) -> set[int]:
    """Return the 1-indexed line numbers carrying a valid suppression marker.

    Returns:
        The set of line numbers whose comment is a justified marker.

    Raises:
        GateSourceError: If the (already ast-parsed) source fails to tokenise.
    """
    lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and _is_valid_marker(tok.string):
                lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        msg = f"{rel}: could not tokenise source: {exc}"
        raise GateSourceError(msg) from exc
    return lines


def _is_none_literal(value: ast.expr) -> bool:
    """Return True iff *value* is the ``None`` constant."""
    return isinstance(value, ast.Constant) and value.value is None


def _is_chokepoint_call(node: ast.Call) -> bool:
    """Return True iff *node* calls an LLM cost / completion chokepoint."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _CHOKEPOINT_CALLS
    if isinstance(func, ast.Attribute):
        return func.attr in _CHOKEPOINT_CALLS
    return False


def _passes_non_none_purpose(node: ast.Call) -> bool:
    """Return True iff *node* tags an LLM chokepoint with a non-``None`` purpose.

    Only ``purpose=`` on a cost / completion chokepoint carries the prompt-class
    signal; an unrelated ``purpose=`` keyword (e.g. a code-execution purpose)
    must not bring a class in scope.

    Returns:
        ``True`` when *node* is a chokepoint call with a non-None ``purpose=``.
    """
    if not _is_chokepoint_call(node):
        return False
    return any(
        kw.arg == _PURPOSE_KEYWORD and not _is_none_literal(kw.value)
        for kw in node.keywords
    )


def _body_calls(class_node: ast.ClassDef) -> list[ast.Call]:
    """Return calls in *class_node*'s body, excluding nested class bodies.

    A nested ``ClassDef`` is its own prompt-class unit; its calls must not be
    attributed to the enclosing class, so the walk prunes nested class subtrees.

    Returns:
        Every :class:`ast.Call` reachable from the class body without crossing
        into a nested class definition.
    """
    calls: list[ast.Call] = []
    stack: list[ast.AST] = list(ast.iter_child_nodes(class_node))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.ClassDef):
            continue
        if isinstance(node, ast.Call):
            calls.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return calls


def _annotation_is_pin(annotation: ast.expr | None) -> bool:
    """Return True iff *annotation* names :class:`ModelPinMetadata`."""
    if isinstance(annotation, ast.Name):
        return annotation.id == _PIN_TYPE
    if isinstance(annotation, ast.Attribute):
        return annotation.attr == _PIN_TYPE
    return False


def _is_metadata_property(node: ast.stmt) -> bool:
    """Return True iff *node* is a ``metadata`` property returning the pin type.

    Accepts a concrete or ``@abstractmethod`` property, sync or async, as long
    as a ``property`` decorator is present and the return annotation is
    :class:`ModelPinMetadata`.

    Returns:
        ``True`` when *node* is the required ``metadata`` property.
    """
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    if node.name != _METADATA_PROPERTY:
        return False
    has_property = any(
        isinstance(dec, ast.Name) and dec.id == _PROPERTY_DECORATOR
        for dec in node.decorator_list
    )
    return has_property and _annotation_is_pin(node.returns)


def _declares_metadata(class_node: ast.ClassDef) -> bool:
    """Return True iff *class_node*'s own body declares the ``metadata`` property."""
    return any(_is_metadata_property(stmt) for stmt in class_node.body)


def _scan_file(path: Path, rel: str) -> list[_Hit]:
    """Return in-scope prompt classes in one file that lack ``metadata``.

    Returns:
        A list of :class:`_Hit` for each violating class.

    Raises:
        GateSourceError: If the file cannot be read, parsed, or tokenised.
    """
    text, tree = read_and_parse(path)
    marked = _marker_lines(text, rel)
    hits: list[_Hit] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(_passes_non_none_purpose(call) for call in _body_calls(node)):
            continue
        if _declares_metadata(node):
            continue
        end = node.end_lineno or node.lineno
        if any(node.lineno <= line <= end for line in marked):
            continue
        hits.append(_Hit(rel=rel, lineno=node.lineno, name=node.name))
    return hits


def _scan_all(project_root: Path) -> list[_Hit]:
    """Scan ``src/synthorg`` and return every violating prompt class.

    Returns:
        A list of :class:`_Hit`.

    Raises:
        GateSourceError: If any source file cannot be read or parsed.
    """
    abs_root = project_root / _SCAN_ROOT_REL
    hits: list[_Hit] = []
    for path, rel in _git_tracked_python_files(abs_root, project_root):
        hits.extend(_scan_file(path, rel))
    return hits


def cmd_scan_all(project_root: Path | None = None) -> int:
    """Scan the whole src tree and report violations.

    Returns:
        ``0`` when clean, ``1`` on a violation, ``2`` on a read/parse error.
    """
    root = project_root if project_root is not None else _REPO_ROOT
    try:
        hits = _scan_all(root)
    except GateSourceError as exc:
        print(f"check_prompt_class_metadata: {exc}", file=sys.stderr)
        return 2
    if not hits:
        return 0
    hits.sort(key=lambda h: (h.rel, h.lineno))
    for hit in hits:
        print(hit.message())
    print(
        f"\n{len(hits)} prompt class(es) tag spend with a purpose but expose no "
        f"'{_METADATA_PROPERTY}' property. Add "
        f"'@property def {_METADATA_PROPERTY}(self) -> {_PIN_TYPE}: "
        f"return pin_for(self._PURPOSE_ID)', or opt out with "
        "'# lint-allow: prompt-class-metadata -- <reason>'.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        The gate exit code (0 clean, 1 violation, 2 configuration error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (defaults to this script's repo).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan the full src tree (accepted for symmetry; the default).",
    )
    args = parser.parse_args(argv)
    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return cmd_scan_all(project_root)


if __name__ == "__main__":
    raise SystemExit(main())
