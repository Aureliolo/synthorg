#!/usr/bin/env python3
"""Pre-push / CI gate: every ``ModelCapabilities`` field has a real reader.

Six fields on the routing-layer ``ModelCapabilities`` record were found
(``max_output_tokens``, ``max_context_tokens``, ``cost_per_1k_input``,
``cost_per_1k_output``, ``supports_system_messages``,
``supports_streaming_tool_calls``) with no reader outside the class's own
validator: `grep -rn ModelCapabilities src/synthorg/providers/routing/`
returned zero, and the cost pair read as "live" tree-wide only because
identically-named fields on ``ProviderModelConfig`` and ``ResolvedModel``
absorbed every hit. This is the rule that stops the next unread field
hiding behind the same false positive.

Detection
---------
Population is derived by AST from the ``ModelCapabilities`` class body in
``providers/capabilities.py``: every field name bound by a direct
``AnnAssign`` in the class's own body. Losing the class (a rename, a move)
is a configuration error, not a clean scan, so it exits 2.

A field has a reader when some OTHER module that references
``ModelCapabilities`` (the bare name appears anywhere as an ``ast.Name`` in
that module) contains an ``ast.Attribute`` access naming the field, in Load
context. The declaring module (``providers/capabilities.py`` itself) is
excluded from both the reference scan and the reader scan: a validator
reading its own field is not a consumer, which is exactly the shape all six
retired fields had and nothing else.

This is deliberately duck-typed and therefore fail-CLOSED on the direction
that matters: scoping to modules that reference the type at all (rather than
every ``.field_name`` access tree-wide) is what stops ``cost_per_1k_input``
absorbing hits meant for ``ProviderModelConfig``; the residual risk is a
module that references ``ModelCapabilities`` for an unrelated reason and
happens to access an identically-named attribute of something else. That
direction is accepted -- a rare false negative, never a false positive
against a value this class does not carry.

Allowlist / opt-out
--------------------
Per-line opt-out: append ``# lint-allow: capability-field-unread -- <reason>``
to the field's declaring line. The reason is mandatory. The only legitimate
case is a field a genuinely external consumer reads by value rather than by
Python attribute access (there is none today).

No baseline: every field this gate would have flagged is deleted in the
same PR that introduces it.

Usage::

    uv run python scripts/check_capability_field_has_reader.py

Exit codes:
    0 -- every declared field has a reader outside its own module.
    1 -- at least one field is read nowhere but its own validator.
    2 -- configuration error (bad ``--repo-root``, the class could not be
         found, an empty field population, or a source file that could not
         be read, parsed, or tokenised -- fail-closed).
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
_CAPABILITIES_MODULE_REL: Final[str] = "src/synthorg/providers/capabilities.py"
_CLASS_NAME: Final[str] = "ModelCapabilities"
_SRC_ROOT_REL: Final[str] = "src/synthorg"
_SUPPRESSION_MARKER: Final[str] = "lint-allow: capability-field-unread"


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


class ClassNotFoundError(Exception):
    """Raised when ``ModelCapabilities`` cannot be found in its module."""


@dataclass(frozen=True)
class _Hit:
    """One declared capability field with no reader outside its own module."""

    name: str
    lineno: int

    def message(self) -> str:
        """Return the human-facing violation message."""
        return (
            f"{_CAPABILITIES_MODULE_REL}:{self.lineno}: {_CLASS_NAME}.{self.name} "
            f"is declared and read nowhere outside its own module. Wire a "
            f"reader, delete the field, or justify it with "
            f"'# lint-allow: {_SUPPRESSION_MARKER} -- <reason>'."
        )


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments.

    Returns:
        The resolved project-root directory.

    Raises:
        ProjectRootError: If *repo_root* cannot be resolved to an existing
            directory.
    """
    if repo_root is None:
        return _REPO_ROOT
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError as exc:
        msg = f"--repo-root not accessible: {repo_root} ({exc})"
        raise ProjectRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise ProjectRootError(msg)
    return resolved


def _git_tracked_python_files(
    abs_root: Path,
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` under *abs_root* as ``(abs, rel)``.

    Falls back to :meth:`Path.rglob` when ``git`` is unavailable, warning on
    stderr because the fallback widens scope to untracked files.

    Returns:
        A list of ``(absolute_path, posix_relative_path)`` pairs.
    """
    if not abs_root.is_dir():
        return []
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
            f"check_capability_field_has_reader: git ls-files failed in "
            f"{project_root} ({type(exc).__name__}: {exc}); falling back to "
            f"rglob (scope widens to include untracked / gitignored files).",
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
        ``True`` for ``# lint-allow: capability-field-unread -- <reason>``.
    """
    comment = comment_token.lstrip("#").strip()
    if not comment.startswith(_SUPPRESSION_MARKER):
        return False
    suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
    return suffix.startswith("--") and bool(suffix[2:].strip())


def _marker_lines(text: str) -> set[int]:
    """Return the 1-indexed line numbers carrying a valid suppression marker.

    Returns:
        The set of line numbers whose comment is a justified marker.

    Raises:
        GateSourceError: If the source fails to tokenise, so a dropped marker
            fails the gate loud rather than silently.
    """
    lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and _is_valid_marker(tok.string):
                lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        msg = f"{_CAPABILITIES_MODULE_REL}: could not tokenise source: {exc}"
        raise GateSourceError(msg) from exc
    return lines


def _declared_fields(module_tree: ast.Module) -> dict[str, int]:
    """Return every ``ModelCapabilities`` field as ``name -> lineno``.

    Only a direct ``AnnAssign`` in the class's own body counts, so a method,
    a ``@computed_field`` property, or a ``@model_validator`` is never
    mistaken for a field.

    Returns:
        A mapping from field name to its declaring line number.

    Raises:
        ClassNotFoundError: If no ``class ModelCapabilities`` is found.
    """
    for node in module_tree.body:
        if isinstance(node, ast.ClassDef) and node.name == _CLASS_NAME:
            return {
                stmt.target.id: stmt.lineno
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    msg = f"{_CAPABILITIES_MODULE_REL}: class {_CLASS_NAME} not found"
    raise ClassNotFoundError(msg)


def _references_class(tree: ast.Module) -> bool:
    """Whether *tree* names ``ModelCapabilities`` anywhere.

    Returns:
        ``True`` if the bare class name appears as an ``ast.Name`` node,
        which covers an import, a type annotation, and an ``isinstance``
        check alike.
    """
    return any(
        isinstance(node, ast.Name) and node.id == _CLASS_NAME for node in ast.walk(tree)
    )


def _read_field_names(tree: ast.Module) -> set[str]:
    """Return every attribute name accessed (Load context) in *tree*.

    Returns:
        The set of ``.attr`` names read anywhere in the module.
    """
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
    }


def _scan(project_root: Path) -> list[_Hit]:
    """Return every declared capability field with no reader outside its module.

    Returns:
        A list of :class:`_Hit`, sorted by line then name.

    Raises:
        ClassNotFoundError: If ``ModelCapabilities`` cannot be located or
            declares no fields.
        GateSourceError: If any source file cannot be read, parsed, or
            tokenised.
    """
    capabilities_path = project_root / _CAPABILITIES_MODULE_REL
    own_text, own_tree = read_and_parse(capabilities_path)
    declared = _declared_fields(own_tree)
    if not declared:
        msg = (
            f"{_CAPABILITIES_MODULE_REL}: class {_CLASS_NAME} declares no "
            f"fields; the scan is misconfigured"
        )
        raise ClassNotFoundError(msg)

    read_names: set[str] = set()
    src_root = project_root / _SRC_ROOT_REL
    for path, rel in _git_tracked_python_files(src_root, project_root):
        if rel == _CAPABILITIES_MODULE_REL:
            continue
        _, tree = read_and_parse(path)
        if not _references_class(tree):
            continue
        read_names.update(_read_field_names(tree))

    marked = _marker_lines(own_text)
    hits = [
        _Hit(name=name, lineno=lineno)
        for name, lineno in declared.items()
        if name not in read_names and lineno not in marked
    ]
    hits.sort(key=lambda h: (h.lineno, h.name))
    return hits


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
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        hits = _scan(project_root)
    except (ClassNotFoundError, GateSourceError) as exc:
        print(f"check_capability_field_has_reader: {exc}", file=sys.stderr)
        return 2

    if not hits:
        return 0
    for hit in hits:
        print(hit.message())
    print(
        f"\n{len(hits)} unread capability field(s). Wire a reader, delete "
        f"the field, or add "
        f"'# lint-allow: {_SUPPRESSION_MARKER} -- <reason>'.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
