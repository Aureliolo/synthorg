#!/usr/bin/env python3
"""Pre-push / CI gate: nothing rewrites an agent's bound ``(provider, model)``.

A provider is a registered *connection*, with its own credentials, endpoint and
quota, so the same model id reached through two of them is two different calls,
billed and rate-limited separately. The pair an operator binds to an agent is
therefore a choice about where work runs and what it costs, and an agent is a
fixed ``(role, personality, model)`` unit: work that needs more capability goes
to a DIFFERENT AGENT, never to the same agent quietly running something else.

Three mechanisms used to disagree. Budget auto-downgrade handed the run a
rewritten identity at the task boundary; quota degradation swapped the provider
mid-dispatch; and the stakes router raised the requirement after selection had
already approved the pair. Each read perfectly reasonable at its own call site,
each produced a run whose recorded capability rung meant nothing, and together
they made "which model ran this" a question with three answers.

Detection
---------
AST-walk every tracked ``*.py`` under ``src/synthorg/`` and flag two shapes:

**A rewrite.** A ``model_copy(update=...)`` whose update mapping names
``model``, ``provider``, ``model_id`` or ``capability``. The update mapping is
read from a dict literal, from ``dict(model=...)`` keyword form, and from a
module-local name that is assigned or subscript-assigned one of those keys
somewhere in the same module, because building the dict a line earlier is the
shape the check would otherwise be one refactor away from missing.

**A construction.** ``ModelConfig(...)`` under whatever local name it was
imported as, plus Pydantic's class-level constructors (``model_validate``,
``model_validate_json``, ``model_construct``). Minting a fresh binding is how a
rewrite looks once it stops calling itself one.

Both are matched on the dotted spelling, so reaching the class through its
module counts exactly as reaching it through an imported name: matching one
spelling would be a gate you get past by renaming an import.

Declared owners
---------------
Four modules legitimately produce a binding, each because a human chose it, and
each must still contain a matching construct: a declaration that has outlived
its site is an exemption the next rewrite inherits silently.

What it does NOT do
-------------------
It says nothing about an update mapping it cannot read statically (one built by
a comprehension, returned by a call, or unpacked from ``**``). No AST can, and
guessing would trade a real guarantee for a noisy one. The construction half
has no such gap, and it is the half that mints a binding out of nothing.

Allowlist / opt-out
-------------------
Per-line opt-out: append ``# lint-allow: bound-pair-rewrite -- <reason>`` to the
offending line. The justification after ``--`` is required.

There is deliberately no baseline file. The two violations this convention was
written for are deleted, so a baseline would exist only to let the rule grow
back.

Usage::

    uv run python scripts/check_no_bound_pair_rewrite.py

Exit codes:
    0 -- nothing rewrites or mints a binding outside the declared owners.
    1 -- a binding is rewritten or minted elsewhere.
    2 -- configuration error (bad ``--repo-root``, a declared owner that no
         longer produces a binding, or a source file that could not be read,
         parsed, or tokenised -- fail-closed).
"""

import argparse
import ast
import io
import subprocess
import sys
import tokenize
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
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
_SUPPRESSION_MARKER: Final[str] = "lint-allow: bound-pair-rewrite"
_TARGET_CLASS: Final[str] = "ModelConfig"
#: The module that defines the class. Reaching it through its module
#: (``agent.ModelConfig(...)``) mints exactly the same binding as importing the
#: name, so a gate blind to it is one you get past by changing an import line.
_TARGET_MODULE: Final[str] = "synthorg.core.agent"

#: Pydantic's class-level constructors. Each takes untyped input and hands back
#: a whole binding, so each is the same mint as calling the class;
#: ``model_construct`` is the sharpest of them, skipping validation outright.
_CLASS_CONSTRUCTORS: Final[frozenset[str]] = frozenset(
    {"model_validate", "model_validate_json", "model_construct"}
)

#: Update keys that re-point what an agent runs. ``model`` swaps the whole
#: binding; the other three edit a half of it, which is the same swap written
#: one level down.
_BINDING_KEYS: Final[frozenset[str]] = frozenset(
    {"model", "provider", "model_id", "capability"}
)

_COPY_METHOD: Final[str] = "model_copy"

# The four modules that produce a binding, each with the reason it may. Written
# here rather than as bare paths because the reason is the whole exemption.
_BINDING_OWNER_PATHS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "src/synthorg/api/bootstrap.py": (
            "rehydrates the operator's persisted choice into the roster at boot"
        ),
        "src/synthorg/hr/hiring_instantiation.py": (
            "mints the binding an approved hire was created with"
        ),
        "src/synthorg/meta/chief_of_staff/console_identity.py": (
            "the operator's own console, whose pair the operator sets directly"
        ),
        "src/synthorg/api/services/_org_agent_mutations.py": (
            "the operator's own PATCH, which is how a binding is meant to change"
        ),
    }
)


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _Hit:
    """One binding rewritten or minted outside the declared owners."""

    rel: str
    lineno: int
    col: int
    kind: str

    def message(self) -> str:
        """Return the human-facing violation message.

        Returns:
            The formatted violation line.
        """
        what = (
            "rewrites an agent's bound (provider, model) pair"
            if self.kind == "rewrite"
            else f"mints a {_TARGET_CLASS}"
        )
        return (
            f"{self.rel}:{self.lineno}:{self.col}: {what}. The pair is the "
            f"operator's choice about where work runs and what it costs; work "
            f"needing more capability goes to a different agent, not to the "
            f"same agent running something else."
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
            f"check_no_bound_pair_rewrite: git ls-files failed in "
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
        ``True`` for ``# lint-allow: bound-pair-rewrite -- <reason>``.
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
        GateSourceError: If the source fails to tokenise, so a dropped marker
            fails the gate loud rather than silently.
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


def _dotted(node: ast.expr) -> str | None:
    """Render a pure name/attribute chain as its dotted source spelling.

    Returns:
        ``"agent.ModelConfig"`` for that expression, ``None`` for anything
        rooted in a call, subscript or literal, where no static spelling
        exists to compare against.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _local_names(tree: ast.Module) -> set[str]:
    """Return every local spelling that reaches :data:`_TARGET_CLASS`.

    Covers ``from ... import ModelConfig as X``, a module-level
    ``X = ModelConfig``, and every way of reaching the class through its
    module: ``import synthorg.core.agent [as a]`` and
    ``from synthorg.core import agent [as a]``, each yielding the dotted
    spelling the call site actually writes.

    Args:
        tree: The parsed module.

    Returns:
        The dotted spellings that refer to the class.
    """
    names = {_TARGET_CLASS}
    package, _, leaf = _TARGET_MODULE.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(
                f"{alias.asname or alias.name}.{_TARGET_CLASS}"
                for alias in node.names
                if alias.name == _TARGET_MODULE
            )
        elif isinstance(node, ast.ImportFrom):
            names.update(
                alias.asname
                for alias in node.names
                if alias.name == _TARGET_CLASS and alias.asname
            )
            if node.module == package:
                names.update(
                    f"{alias.asname or alias.name}.{_TARGET_CLASS}"
                    for alias in node.names
                    if alias.name == leaf
                )
        elif isinstance(node, ast.Assign):
            names.update(
                target.id
                for target in node.targets
                if isinstance(target, ast.Name) and _dotted(node.value) in names
            )
    return names


def _is_construction(node: ast.Call, names: set[str]) -> bool:
    """Return whether *node* mints a binding out of nothing.

    Returns:
        ``True`` when the call is the class, or one of Pydantic's class-level
        constructors on it.
    """
    func = node.func
    if _dotted(func) in names:
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _CLASS_CONSTRUCTORS
        and _dotted(func.value) in names
    )


def _literal_keys(node: ast.expr) -> frozenset[str]:
    """Return the statically-known string keys of an update mapping.

    Reads a dict literal and the ``dict(model=...)`` keyword form, which are
    the same mapping spelled two ways. A ``**`` unpacking contributes nothing
    it does not also spell out, and anything else is undecidable.

    Returns:
        The keys, or an empty set when none can be read.
    """
    if isinstance(node, ast.Dict):
        return frozenset(
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        )
    if isinstance(node, ast.Call) and _dotted(node.func) == "dict":
        return frozenset(kw.arg for kw in node.keywords if kw.arg is not None)
    return frozenset()


def _named_update_keys(tree: ast.Module) -> dict[str, frozenset[str]]:
    """Map each module-local name to the update keys it is built with.

    Building the mapping a line before the copy is the shape a literal-only
    check is one refactor away from missing, and the operator's own PATCH is
    written exactly that way. Names are collected module-wide rather than per
    function: a name assigned a binding key in one function and passed as
    ``update=`` in another is one dict, and the alternative (over-scoping by
    name) errs toward asking for a justification rather than toward silence.

    Args:
        tree: The parsed module.

    Returns:
        The name-to-keys map, holding only names that carry a binding key.
    """
    keys: dict[str, set[str]] = {}

    def _add(name: str, found: frozenset[str]) -> None:
        if found & _BINDING_KEYS:
            keys.setdefault(name, set()).update(found)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add(target.id, _literal_keys(node.value))
                elif (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    _add(target.value.id, frozenset({target.slice.value}))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add(
                node.target.id,
                frozenset() if node.value is None else _literal_keys(node.value),
            )
    return {name: frozenset(found) for name, found in keys.items()}


def _update_keys(node: ast.Call, named: Mapping[str, frozenset[str]]) -> frozenset[str]:
    """Return the keys of *node*'s ``update=`` mapping.

    Returns:
        The statically-known keys, empty when the call passes no ``update=``
        or passes a mapping no static read can open.
    """
    for keyword in node.keywords:
        if keyword.arg != "update":
            continue
        if isinstance(keyword.value, ast.Name):
            return named.get(keyword.value.id, frozenset())
        return _literal_keys(keyword.value)
    return frozenset()


@dataclass(frozen=True)
class _Site:
    """One place a binding is produced, and the lines it spans.

    The span is what the suppression marker is matched against: a trailing
    comment on a call broken across lines sits on its LAST line, so anchoring
    to the first would refuse every justification written the natural way.
    """

    lineno: int
    col: int
    kind: str
    end_lineno: int


def _violation_sites(tree: ast.Module) -> list[_Site]:
    """Return every rewrite and construction in *tree*.

    Returns:
        One entry per site, in walk order.
    """
    names = _local_names(tree)
    named = _named_update_keys(tree)
    sites: list[_Site] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_construction(node, names):
            kind = "construct"
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == _COPY_METHOD
            and _update_keys(node, named) & _BINDING_KEYS
        ):
            kind = "rewrite"
        else:
            continue
        sites.append(
            _Site(
                lineno=node.lineno,
                col=node.col_offset,
                kind=kind,
                end_lineno=node.end_lineno or node.lineno,
            )
        )
    return sites


def _scan_file(path: Path, rel: str) -> tuple[list[_Hit], int]:
    """Return the violations in one file and how many binding sites it holds.

    Returns:
        ``(hits, site_count)``. A declared owner yields no hits, but its count
        still matters: it is what proves the declaration is load-bearing.

    Raises:
        GateSourceError: If the file cannot be read or parsed (fail-closed).
    """
    text, tree = read_and_parse(path)
    sites = _violation_sites(tree)
    if not sites:
        return [], 0
    if rel in _BINDING_OWNER_PATHS:
        return [], len(sites)
    marked = _marker_lines(text, rel)
    hits = [
        _Hit(rel=rel, lineno=site.lineno, col=site.col, kind=site.kind)
        for site in sites
        if marked.isdisjoint(range(site.lineno, site.end_lineno + 1))
    ]
    return hits, len(sites)


def _scan_all(project_root: Path) -> tuple[list[_Hit], set[str]]:
    """Scan ``src/synthorg`` for rewrites outside the declared owners.

    Returns:
        ``(hits, declared_owners_that_still_produce_a_binding)``.

    Raises:
        GateSourceError: If any source file cannot be read or parsed.
    """
    abs_root = project_root / _SCAN_ROOT_REL
    hits: list[_Hit] = []
    live_owners: set[str] = set()
    for path, rel in _git_tracked_python_files(abs_root, project_root):
        file_hits, count = _scan_file(path, rel)
        hits.extend(file_hits)
        if count and rel in _BINDING_OWNER_PATHS:
            live_owners.add(rel)
    return hits, live_owners


def _report_stale_owners(live_owners: set[str]) -> int:
    """Print every declared owner that no longer produces a binding.

    Returns:
        The number of stale declarations.
    """
    stale = sorted(set(_BINDING_OWNER_PATHS) - live_owners)
    for rel in stale:
        print(
            f"{rel}: declared as a binding owner but produces no "
            f"{_TARGET_CLASS}. Remove the declaration, or point it at the "
            f"module that took the binding over: an unused exemption is one "
            f"the next rewrite inherits silently.",
            file=sys.stderr,
        )
    return len(stale)


def _iter_hits(hits: list[_Hit]) -> Iterator[str]:
    """Yield each violation message in a stable order.

    Yields:
        One formatted violation line per hit.
    """
    for hit in sorted(hits, key=lambda h: (h.rel, h.lineno, h.col)):
        yield hit.message()


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
        hits, live_owners = _scan_all(project_root)
    except GateSourceError as exc:
        print(f"check_no_bound_pair_rewrite: {exc}", file=sys.stderr)
        return 2

    if _report_stale_owners(live_owners):
        return 2

    if not hits:
        return 0
    for message in _iter_hits(hits):
        print(message)
    print(
        f"\n{len(hits)} site(s) rewrite or mint an agent's bound pair outside "
        "the declared owners. Route the work to an agent already bound to what "
        "it needs, or add '# lint-allow: bound-pair-rewrite -- <reason>' on "
        "the offending line.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
