"""Gate: no auto-picked and no shared-default provider.

Every LLM dispatch resolves the connection named by its own bound
``{provider, model_id}`` reference. A provider is a registered *connection*
with its own credentials, endpoint and quota, so there is no "system default"
to borrow and no "whichever provider sorts first" to fall back to: a feature
either has its own pair or it is off. This gate AST-scans ``src/synthorg/``
and fails on a reintroduction of any of:

1. ``<registry>.list_providers()[0]`` -- indexing the sorted provider list.
2. ``<name>[0]`` where ``<name>`` was assigned from a ``.list_providers()``
   call in the same function (the ``names = registry.list_providers()`` /
   ``names[0]`` idiom).
3. Any reference to the removed ``resolve_for_model`` method (the bare-model
   auto-resolver that picked the alphabetically-first serving provider).
4. Any reference to the removed shared default: the ``default_provider`` /
   ``default_provider_name`` / ``default_provider_resolved_name`` /
   ``bind_default_provider`` registry surface, or a ``providers`` settings
   read of ``default_provider``.

Opt a genuine exception out with a trailing
``# lint-allow: provider-auto-pick -- <reason>`` on the offending line (e.g.
a non-dispatch tier hint at empty-company boot). There is deliberately no
baseline: a suppression file would let a dispatch borrow a connection it was
never bound to for as long as nobody drained the list.

Usage:
    uv run python scripts/check_no_provider_auto_pick.py

Exit codes:
    0 -- no violations.
    1 -- a provider auto-pick was found.
    2 -- configuration error (bad ``--repo-root`` or an unreadable source file).
"""

import argparse
import ast
import re
import sys
from collections.abc import Iterator
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

_SRC_REL: Final[str] = "src/synthorg"
_MARKER: Final[str] = "lint-allow: provider-auto-pick"
# The marker suppresses only as a trailing COMMENT carrying a non-empty
# reason (``# lint-allow: provider-auto-pick -- <reason>``); the same text
# inside a string literal must never silence a real finding.
_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"#.*" + re.escape(_MARKER) + r"\s*--\s*\S"
)
_LIST_PROVIDERS: Final[str] = "list_providers"
_REMOVED_RESOLVER: Final[str] = "resolve_for_model"
#: The removed shared-default surface. Each name resolved a provider for a
#: caller that had not named one, which is exactly the ambiguity a
#: ``(provider, model)`` pair exists to remove.
_REMOVED_DEFAULT_SURFACE: Final[frozenset[str]] = frozenset(
    {
        "default_provider",
        "default_provider_name",
        "default_provider_resolved_name",
        "bind_default_provider",
    }
)


def _is_list_providers_call(node: ast.expr) -> bool:
    """Whether *node* is a ``<expr>.list_providers()`` call.

    Unwraps a leading ``await`` first: ``list_providers`` is ``async def`` on the
    provider-management service, so both ``(await x.list_providers())[0]`` and the
    ``names = list(await x.list_providers())`` idiom must be seen through.
    """
    if isinstance(node, ast.Await):
        node = node.value
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == _LIST_PROVIDERS
    )


def _is_zero_index(node: ast.Subscript) -> bool:
    """Whether *node* subscripts with the literal ``0``."""
    return isinstance(node.slice, ast.Constant) and node.slice.value == 0


def _iter_own_scope(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterator[ast.AST]:
    """Yield nodes lexically inside *func*'s own scope.

    Does not descend into a nested function or lambda body -- each opens its
    own scope and is scanned separately, so an outer binding never leaks into
    an inner one (or back).
    """
    stack: list[ast.AST] = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _provider_list_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names bound to a ``.list_providers()`` result within *func*'s own scope.

    Covers ``names = registry.list_providers()`` and its ``list(...)`` /
    ``tuple(...)`` / ``sorted(...)`` wrappers, so the ``names[0]`` idiom is
    caught regardless of the wrapper. Nested function / lambda bodies are
    excluded (they carry their own scope).
    """
    bound: set[str] = set()
    for node in _iter_own_scope(func):
        # Both a plain ``names = ...`` and a typed ``names: list[str] = ...``
        # (annotated) binding must be seen through.
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target = node.target
            value = node.value
        if not isinstance(target, ast.Name) or value is None:
            continue
        # Unwrap a single-arg list()/tuple()/sorted() wrapper.
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in {"list", "tuple", "sorted"}
            and value.args
        ):
            value = value.args[0]
        if _is_list_providers_call(value):
            bound.add(target.id)
    return bound


def _scan_module(tree: ast.Module, lines: list[str], relpath: str) -> list[str]:
    """Return every provider-auto-pick finding in one module."""
    findings: list[str] = []

    def _allowed(lineno: int) -> bool:
        return 1 <= lineno <= len(lines) and bool(_ALLOW_RE.search(lines[lineno - 1]))

    # 1 + 3: direct list_providers()[0] and any resolve_for_model reference.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and _is_zero_index(node)
            and _is_list_providers_call(node.value)
            and not _allowed(node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:list_providers()[0]")
        if (
            isinstance(node, ast.Attribute)
            and node.attr == _REMOVED_RESOLVER
            and not _allowed(node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:{_REMOVED_RESOLVER}")
        # 4: the removed shared-default surface, as an attribute access
        # (``registry.default_provider()``) or as a settings key literal
        # (``get_str("providers", "default_provider")``).
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _REMOVED_DEFAULT_SURFACE
            and not _allowed(node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:{node.attr}")
        if (
            isinstance(node, ast.Constant)
            and node.value == "default_provider"
            and not _allowed(node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:setting:default_provider")

    # 2: name[0] where name came from a list_providers() result in the same func.
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        provider_names = _provider_list_names(node)
        if not provider_names:
            continue
        for sub in _iter_own_scope(node):
            if (
                isinstance(sub, ast.Subscript)
                and _is_zero_index(sub)
                and isinstance(sub.value, ast.Name)
                and sub.value.id in provider_names
                and not _allowed(sub.lineno)
            ):
                findings.append(  # noqa: PERF401 -- guarded, narrowed append
                    f"{relpath}:{sub.lineno}:{sub.value.id}[0]"
                )
    return findings


def _scan(root: Path) -> list[str]:
    """Return every current violation identifier under *root*.

    Raises:
        GateSourceError: When the source tree is missing, so a misconfigured
            ``--repo-root`` fails closed rather than scanning zero files.
    """
    src_dir = root / _SRC_REL
    if not src_dir.is_dir():
        msg = f"expected source tree not found: {src_dir}"
        raise GateSourceError(msg)
    findings: list[str] = []
    for path in sorted(src_dir.rglob("*.py")):
        relpath = path.relative_to(root).as_posix()
        text, tree = read_and_parse(path)
        findings.extend(_scan_module(tree, text.splitlines(), relpath))
    return findings


def main(argv: list[str] | None = None) -> int:
    """Scan for provider auto-picks and return the gate exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"error: --repo-root is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        findings = sorted(set(_scan(root)))
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if findings:
        print(
            "error: provider auto-pick(s) found (name the connection through "
            "the consumer's own MODEL_REF pair; there is no shared default "
            "and never the first registered):",
            file=sys.stderr,
        )
        for ident in findings:
            print(f"  {ident}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
