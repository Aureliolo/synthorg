"""Gate: the built-in embedder is chosen, never fallen back to.

Agent memory running on the built-in feature-hashing embedder matches shared
vocabulary rather than meaning. It answers every query, so it reads as working
memory while returning materially worse results, and an operator who did not
choose it has no reason to suspect it. That is the exact failure the memory
health surface exists to expose, and a substitution one layer down would
reintroduce it underneath that surface.

So the built-in is reachable only where an operator's explicit choice is being
honoured, and no embedder may be constructed inside an exception handler, which
is the shape a fallback takes: build the chosen one, and on failure quietly
build a different one.

Both rules are decidable from the AST. The first is an allowlist of construction
sites, deliberately narrow: a new construction site is a decision about when
memory may silently change character, and it should cost a line in this file.

Allowlist / opt-out
-------------------
Per-line opt-out: append ``# lint-allow: no-silent-embedder-fallback --
<reason>`` to the constructing line. The justification after ``--`` is required
and must be non-empty.

Usage:
    uv run python scripts/check_no_silent_embedder_fallback.py
    uv run python scripts/check_no_silent_embedder_fallback.py --files a.py b.py

Exit codes:
    0 -- no unsanctioned construction and no embedder built in a handler.
    1 -- a violation was found.
    2 -- configuration error, or a file that could not be read or parsed.
"""

import argparse
import ast
import io
import sys
import tokenize
from collections.abc import Iterator
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        reachable_statements,
        read_and_parse,
    )
else:
    from scripts._gate_source import (
        GateSourceError,
        reachable_statements,
        read_and_parse,
    )

_SCAN_ROOT_REL: Final[str] = "src/synthorg"
_SUPPRESSION_MARKER: Final[str] = "lint-allow: no-silent-embedder-fallback"

#: The built-in embedder class. Constructing it anywhere else makes lexical
#: recall reachable without the operator having asked for it.
_BUILTIN_CLASS: Final[str] = "HashingTextEmbedder"

#: Modules permitted to construct the built-in, each because it is acting on an
#: explicit operator choice (or is the class's own module).
_CONSTRUCTION_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        # Defines it.
        "src/synthorg/memory/embedding/hashing.py",
        # Builds it only after resolution returned the built-in binding, which
        # only happens when the operator's setting names it.
        "src/synthorg/api/lifecycle_helpers/memory_backend_wiring.py",
        # The meeting detector's ``hashing`` strategy, selected by an operator
        # setting. Not a fallback: the sibling strategy raises rather than
        # handing over to this one.
        "src/synthorg/communication/meeting/embedder.py",
    },
)

#: Every embedder constructor. Building ANY of them inside an exception handler
#: is a fallback, whichever one failed and whichever one replaces it.
_EMBEDDER_BUILDERS: Final[frozenset[str]] = frozenset(
    {
        _BUILTIN_CLASS,
        "ProviderTextEmbedder",
        "SentenceTransformerEmbedder",
        "build_text_embedder",
    },
)


def _called_name(node: ast.Call) -> str:
    """The bare name a call targets.

    Returns:
        The function or attribute name, or an empty string for a call whose
        target is neither (a subscript or a call-of-a-call).
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _marker_lines(text: str, rel: str) -> set[int]:
    """The 1-indexed lines carrying a valid suppression marker.

    Tokenises the whole source so a ``#`` inside a string literal is never
    mistaken for a comment.

    Returns:
        The line numbers whose comment opts out.

    Raises:
        GateSourceError: If the source cannot be tokenised, so the gate fails
            closed rather than treating every line as unsuppressed.
    """
    lines: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT and _is_valid_marker(token.string):
                lines.add(token.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        msg = f"{rel}: could not tokenise source: {exc}"
        raise GateSourceError(msg) from exc
    return lines


def _is_valid_marker(comment: str) -> bool:
    """Whether a comment is a marker with a non-empty justification.

    Returns:
        ``True`` for ``# lint-allow: no-silent-embedder-fallback -- <reason>``.
    """
    body = comment.lstrip("#").strip()
    if not body.startswith(_SUPPRESSION_MARKER):
        return False
    suffix = body[len(_SUPPRESSION_MARKER) :].strip()
    return suffix.startswith("--") and bool(suffix[2:].strip())


def _handler_builder_calls(tree: ast.Module) -> Iterator[ast.Call]:
    """Yield embedder constructions reachable inside an exception handler.

    Only reachable statements count: a construction after an unconditional
    ``raise`` in the same handler never runs, and reporting it would send a
    developer to fix dead code.

    Yields:
        Each embedder-building call inside a handler body.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try | ast.TryStar):
            continue
        for handler in node.handlers:
            for stmt in reachable_statements(handler.body):
                for inner in ast.walk(stmt):
                    if (
                        isinstance(inner, ast.Call)
                        and _called_name(inner) in _EMBEDDER_BUILDERS
                    ):
                        yield inner


def _builtin_constructions(tree: ast.Module) -> Iterator[ast.Call]:
    """Yield every construction of the built-in embedder.

    Yields:
        Each call constructing the built-in.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node) == _BUILTIN_CLASS:
            yield node


def _check_file(path: Path, rel: str) -> list[str]:
    """Scan one module.

    Returns:
        The violation messages for this file.

    Raises:
        GateSourceError: If the file cannot be read, parsed, or tokenised.
    """
    text, tree = read_and_parse(path)
    suppressed = _marker_lines(text, rel)
    violations: list[str] = []

    if rel not in _CONSTRUCTION_ALLOWLIST:
        violations.extend(
            f"{rel}:{call.lineno}: constructs {_BUILTIN_CLASS} outside the "
            f"explicit-selection path; the built-in embedder is chosen by an "
            f"operator, never reached by default. Add this module to "
            f"_CONSTRUCTION_ALLOWLIST in this gate if the construction really "
            f"is honouring an explicit choice, or opt out per line with "
            f"'# {_SUPPRESSION_MARKER} -- <reason>'"
            for call in _builtin_constructions(tree)
            if call.lineno not in suppressed
        )

    violations.extend(
        f"{rel}:{call.lineno}: builds {_called_name(call)} inside an exception "
        f"handler, which substitutes one embedder for another that failed. "
        f"Let the failure propagate so memory reports itself off, or opt out "
        f"per line with '# {_SUPPRESSION_MARKER} -- <reason>'"
        for call in _handler_builder_calls(tree)
        if call.lineno not in suppressed
    )
    return violations


def _scan(paths: list[Path], repo_root: Path) -> list[str]:
    """Scan every given module.

    Returns:
        All violation messages, in file order.

    Raises:
        GateSourceError: If any file cannot be read, parsed, or tokenised.
    """
    violations: list[str] = []
    for path in sorted(paths):
        rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
        violations.extend(_check_file(path, rel))
    return violations


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        The gate exit code (0 clean, 1 violation, 2 configuration error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--files", nargs="*", type=Path, default=None)
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root
    if args.files is not None:
        paths = [p for p in args.files if p.suffix == ".py" and p.is_file()]
    else:
        root = repo_root / _SCAN_ROOT_REL
        if not root.is_dir():
            print(f"error: {_SCAN_ROOT_REL} is not a directory", file=sys.stderr)
            return 2
        paths = list(root.rglob("*.py"))

    try:
        violations = _scan(paths, repo_root)
    except GateSourceError as exc:
        print(f"FAIL (scan could not read a file): {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: file outside the repository root: {exc}", file=sys.stderr)
        return 2

    if violations:
        print("Silent-embedder-fallback check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
