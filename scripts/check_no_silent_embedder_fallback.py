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
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Final, NamedTuple

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


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local aliases back to the tracked names they were imported under.

    Both rules read the name written at the call site, so an aliased import
    was invisible to them: ``import HashingTextEmbedder as Lex`` followed by
    ``Lex()`` matched neither the allowlist check nor the handler check, and
    a one-word rename defeated the whole gate.

    Returns:
        Alias to canonical name, for tracked names only.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname and alias.name in _EMBEDDER_BUILDERS:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                tail = alias.name.rsplit(".", 1)[-1]
                if alias.asname and tail in _EMBEDDER_BUILDERS:
                    aliases[alias.asname] = tail
    return aliases


def _resolved_name(node: ast.Call, aliases: Mapping[str, str]) -> str:
    """The call's target name with any local import alias undone.

    Returns:
        The canonical name where the call targets a tracked alias.
    """
    name = _called_name(node)
    return aliases.get(name, name)


def _local_functions(
    tree: ast.Module,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function defined in this module, by name.

    Returns:
        Name to definition. A later definition of the same name wins, which
        matches what the interpreter would call.
    """
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found[node.name] = node
    return found


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


class _HandlerBuild(NamedTuple):
    """One embedder construction a handler can reach."""

    call: ast.Call
    builder: str
    via: str | None


def _reachable_builds(
    node: ast.AST,
    aliases: Mapping[str, str],
    functions: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[str],
    via: str | None = None,
) -> Iterator[_HandlerBuild]:
    """Yield embedder constructions *node* can reach, one call hop deep.

    Following a call into a locally-defined function is what stops the
    obvious way around this rule: moving the construction one line away,
    into a helper the handler calls. Matching only the name written at the
    handler saw ``return _fallback()`` as harmless.

    Nested ``def``s inside a handler are deliberately still walked. Such a
    definition is far more often the fallback itself than a deferred
    callback that merely happens to be declared there, and the per-line
    opt-out is the cheaper side of that trade.

    Yields:
        Each construction, with the helper it was reached through.
    """
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        name = _resolved_name(inner, aliases)
        if name in _EMBEDDER_BUILDERS:
            yield _HandlerBuild(call=inner, builder=name, via=via)
            continue
        target = functions.get(name)
        if target is not None and name not in seen:
            seen.add(name)
            yield from _reachable_builds(target, aliases, functions, seen, via=name)


def _handler_builder_calls(
    tree: ast.Module,
    aliases: Mapping[str, str],
    functions: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> Iterator[_HandlerBuild]:
    """Yield embedder constructions reachable from exception handling.

    Covers both the handler bodies and ``finally``. A ``finally`` block runs
    on the failure path exactly as a handler does, so a construction there
    substitutes one embedder for another just the same; walking only
    ``handlers`` left that shape unpoliced.

    Only reachable statements count: a construction after an unconditional
    ``raise`` in the same block never runs, and reporting it would send a
    developer to fix dead code.

    Yields:
        Each embedder-building call an exception path can reach.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try | ast.TryStar):
            continue
        blocks = [handler.body for handler in node.handlers]
        if node.finalbody:
            blocks.append(node.finalbody)
        for block in blocks:
            for stmt in reachable_statements(block):
                yield from _reachable_builds(stmt, aliases, functions, set())


def _builtin_constructions(
    tree: ast.Module,
    aliases: Mapping[str, str],
) -> Iterator[ast.Call]:
    """Yield every construction of the built-in embedder.

    Yields:
        Each call constructing the built-in, under any local alias.
    """
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _resolved_name(node, aliases) == _BUILTIN_CLASS
        ):
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
    aliases = _import_aliases(tree)
    functions = _local_functions(tree)
    violations: list[str] = []

    if rel not in _CONSTRUCTION_ALLOWLIST:
        violations.extend(
            f"{rel}:{call.lineno}: constructs {_BUILTIN_CLASS} outside the "
            f"explicit-selection path; the built-in embedder is chosen by an "
            f"operator, never reached by default. Add this module to "
            f"_CONSTRUCTION_ALLOWLIST in this gate if the construction really "
            f"is honouring an explicit choice, or opt out per line with "
            f"'# {_SUPPRESSION_MARKER} -- <reason>'"
            for call in _builtin_constructions(tree, aliases)
            if call.lineno not in suppressed
        )

    violations.extend(
        f"{rel}:{build.call.lineno}: builds {build.builder} on an exception "
        f"path{f' (via {build.via})' if build.via else ''}, which substitutes "
        f"one embedder for another that failed. Let the failure propagate so "
        f"memory reports itself off, or opt out per line with "
        f"'# {_SUPPRESSION_MARKER} -- <reason>'"
        for build in _handler_builder_calls(tree, aliases, functions)
        if build.call.lineno not in suppressed
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


def _files_in_scan_root(files: list[Path], repo_root: Path) -> list[Path]:
    """Narrow ``--files`` to the population the tree scan already covers.

    The edit-time runner hands this gate whatever the agent touched, so
    without this a test that legitimately constructs the built-in embedder
    would be reported as a violation the whole-tree run does not see. The
    two modes have to police the same files or the gate's verdict depends
    on how it was invoked.

    Returns:
        The Python files under the scan root, in the caller's order.

    Raises:
        ValueError: If a path lies outside the repository root entirely.
            That is a caller error rather than an out-of-scope file, and
            the gate fails closed on it rather than scanning nothing.
    """
    root = (repo_root / _SCAN_ROOT_REL).resolve()
    scoped: list[Path] = []
    for path in files:
        if path.suffix != ".py" or not path.is_file():
            continue
        resolved = path.resolve()
        resolved.relative_to(repo_root.resolve())
        if resolved.is_relative_to(root):
            scoped.append(path)
    return scoped


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
    try:
        if args.files is not None:
            paths = _files_in_scan_root(args.files, repo_root)
        else:
            root = repo_root / _SCAN_ROOT_REL
            if not root.is_dir():
                print(f"error: {_SCAN_ROOT_REL} is not a directory", file=sys.stderr)
                return 2
            paths = list(root.rglob("*.py"))
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
