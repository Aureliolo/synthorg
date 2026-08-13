"""Gate: a failover target is declared by an operator, never derived.

Operator-declared failover is a deliberate carve-out from Explicit Provider
Binding: for the twenty-odd ``MODEL_REF`` settings that bind a *system
feature* to a ``(provider, model)`` pair, an operator may name a second pair
to serve when the first cannot. That carve-out is only defensible while it
stays exactly what it says, so this gate keeps three things true.

1. **The alternate is looked up, never picked.** Inside the failover modules
   the only admissible resolution is an exact-key lookup in the operator's
   map. Indexing a computed sequence, ``next(iter(...))``, a ``.values()``
   scan or anything reading the provider list is how an auto-pick comes back
   wearing a different name, so each is rejected there outright.
2. **It never reaches an embedder or the memory backend.** Memory's embedding
   model is the operator's explicit choice and a silent substitute is banned
   (``check_no_silent_embedder_fallback.py``); a failover resolver imported
   into that wiring would be one. The exclusion is structural today, and this
   is what keeps it structural.
3. **It never reaches an agent's pair or the gateway.** An agent whose pair
   cannot serve becomes unavailable and its work is reassigned, which is a
   legible org state; the gateway's pair is minted per run from verified
   claims. Both are out of scope by ruling, so the wrapper may be constructed
   in exactly one module (the ``MODEL_REF`` resolution path), the failover
   modules may not reach for an agent identity, and the gateway package may
   not import them at all.

Opt a genuine exception out with a trailing
``# lint-allow: declared-failover -- <reason>`` on the offending line. The
reason is mandatory: every legitimate exception here is a claim about scope,
and the claim is the only thing that makes it reviewable. There is
deliberately no baseline; a suppression file would let the carve-out widen
for as long as nobody drained it.

Usage:
    uv run python scripts/check_declared_failover_pairs.py

Exit codes:
    0 -- no violations.
    1 -- a derived, out-of-scope or unowned failover was found.
    2 -- configuration error (bad ``--repo-root`` or an unreadable source file).
"""

import argparse
import ast
import re
import sys
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
_MARKER: Final[str] = "lint-allow: declared-failover"
_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"#.*" + re.escape(_MARKER) + r"\s*--\s*\S"
)

#: The modules that decide where a failover goes. Rule 1 applies inside
#: these and nowhere else: an index or a ``.values()`` scan is ordinary code
#: in the rest of the tree.
_FAILOVER_MODULES: Final[frozenset[str]] = frozenset(
    {
        "src/synthorg/providers/failover.py",
        "src/synthorg/providers/failover_dispatch.py",
    }
)

#: The one module that may put the wrapper behind a client. It is the single
#: path from "an operator chose a pair for a system feature" to "a call can
#: be made", so owning construction here is what makes the scope ruling
#: structural rather than a convention somebody has to remember.
_WRAPPER_OWNER: Final[str] = "src/synthorg/providers/model_binding.py"
_WRAPPER_CLASS: Final[str] = "FailoverCompletionProvider"

#: Import paths that carry the failover mechanism.
_FAILOVER_IMPORTS: Final[frozenset[str]] = frozenset(
    {
        "synthorg.providers.failover",
        "synthorg.providers.failover_dispatch",
    }
)

#: Where the mechanism must not be reachable from. ``memory`` and any
#: embedder module because an embedder substitution is separately banned;
#: the gateway package because its pair comes from verified per-run claims.
_FORBIDDEN_PREFIXES: Final[tuple[str, ...]] = (
    "src/synthorg/memory/",
    "src/synthorg/api/gateway/",
)
_FORBIDDEN_FRAGMENT: Final[str] = "embedder"

#: Names that would make the failover modules aware of an agent. An agent's
#: pair is exclusive by ruling: it does not fail over, it goes unavailable.
_AGENT_NAMES: Final[frozenset[str]] = frozenset({"AgentIdentity", "AgentRegistry"})

#: Sequence-picking helpers. ``next(iter(...))`` takes whatever the iteration
#: order happens to be, which is the definition of a pick nobody declared.
_ITER_PICKERS: Final[frozenset[str]] = frozenset({"next"})
_SCAN_ATTRS: Final[frozenset[str]] = frozenset({"values", "list_providers"})


def _allowed(lines: list[str], lineno: int) -> bool:
    """Whether the source line carries a reasoned opt-out marker."""
    return 1 <= lineno <= len(lines) and bool(_ALLOW_RE.search(lines[lineno - 1]))


def _is_integer_index(node: ast.Subscript) -> bool:
    """Whether *node* subscripts with an integer literal."""
    return isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int)


def _scan_resolution(tree: ast.Module, lines: list[str], relpath: str) -> list[str]:
    """Return every derived-target finding inside one failover module."""
    findings: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and _is_integer_index(node)
            and isinstance(node.value, ast.Call | ast.Await)
            and not _allowed(lines, node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:indexed-a-computed-sequence")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _ITER_PICKERS
            and not _allowed(lines, node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:{node.func.id}(...)")
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _SCAN_ATTRS
            and not _allowed(lines, node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:.{node.attr}()")
        if (
            isinstance(node, ast.Name)
            and node.id in _AGENT_NAMES
            and not _allowed(lines, node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:agent-aware:{node.id}")
    return findings


def _imports_failover(node: ast.AST) -> str | None:
    """Return the failover module *node* imports, if it imports one.

    Returns:
        The imported module path, or ``None``.
    """
    if isinstance(node, ast.ImportFrom) and node.module in _FAILOVER_IMPORTS:
        return node.module
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name in _FAILOVER_IMPORTS:
                return alias.name
    return None


def _is_forbidden_reach(relpath: str) -> bool:
    """Whether *relpath* is somewhere the mechanism must not be reachable."""
    return relpath.startswith(_FORBIDDEN_PREFIXES) or _FORBIDDEN_FRAGMENT in relpath


def _scan_module(tree: ast.Module, lines: list[str], relpath: str) -> list[str]:
    """Return every declared-failover finding in one module."""
    findings: list[str] = []
    if relpath in _FAILOVER_MODULES:
        findings.extend(_scan_resolution(tree, lines, relpath))
    forbidden = _is_forbidden_reach(relpath)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            imported = _imports_failover(node)
            if imported is not None and forbidden and not _allowed(lines, node.lineno):
                findings.append(
                    f"{relpath}:{node.lineno}:out-of-scope-import:{imported}"
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == _WRAPPER_CLASS
            and relpath != _WRAPPER_OWNER
            and not _allowed(lines, node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:unowned-wrapper-construction")
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
    """Scan for derived or out-of-scope failover and return the exit code."""
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
            "error: failover must resolve by exact-key lookup in the operator's "
            "declared map, must not reach an embedder, the memory backend, an "
            "agent's pair or the gateway, and may be wrapped only by "
            f"{_WRAPPER_OWNER}:",
            file=sys.stderr,
        )
        for ident in findings:
            print(f"  {ident}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
