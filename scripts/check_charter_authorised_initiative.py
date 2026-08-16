#!/usr/bin/env python3
"""Pre-push / CI gate: an initiative starts only from an approved charter.

Standing up an initiative commits the organisation to a body of effort and a
budget. That is the operator's decision, taken once, in the charter interview,
and recorded by their approval of what it drafts. It is never inferred from a
message by a classifier, and never taken by an adapter that decided on its own
that a request looked like a project.

``WorkItem.plan_required`` is the flag that forces that outcome: it makes the
spine decompose a brief into a plan whatever the solo-vs-team router says, which
opens a project, a plan and a decomposition run. So the flag IS the decision,
and this gate holds three claims about it, each of which an AST can decide.

Detection
---------
**The invariant is declared.** ``WorkItem`` must still carry a ``charter_id``
field and a model validator that conditions on both ``plan_required`` and
``charter_id`` and reaches a ``raise``. The runtime refusal is what covers the
constructions no static read can open (``**kwargs``, a mapping built by a
call), so a gate that let it be deleted would be guarding a door with no lock
behind it.

**One owner sets the flag.** Any keyword argument named ``plan_required``, and
any ``model_copy(update=...)`` whose mapping names it, must live in the declared
owner. ``model_copy`` is included because it skips validation outright: flipping
the flag on an already-built item is how a second intake path looks once it
stops constructing one.

**The owner pairs it with a charter.** The owner's own call must pass
``charter_id=`` alongside, so keeping the flag while dropping the binding fails
here rather than at request time.

Declared owner
--------------
One module, because one path is the whole rule. It must still set the flag: a
declaration that has outlived its site is an exemption the next intake path
inherits silently.

What it does NOT do
-------------------
It says nothing about a construction whose keywords no static read can open
(``WorkItem(**payload)``). No AST can, and the model validator refuses those at
runtime, which is why the first claim above exists.

Allowlist / opt-out
-------------------
There is deliberately no per-line opt-out and no baseline. A genuine exception
is a second intake path, which is precisely what the rule forbids; changing it
means changing the declared owner, in the open.

Usage::

    uv run python scripts/check_charter_authorised_initiative.py

Exit codes:
    0 -- the invariant is declared and only the owner forces an initiative.
    1 -- something outside the owner forces an initiative.
    2 -- configuration error (bad ``--repo-root``, a missing or weakened
         invariant, a stale owner declaration, or a source file that could not
         be read or parsed -- fail-closed).
"""

import argparse
import ast
import subprocess
import sys
from collections.abc import Iterator
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

#: The flag that forces a brief to become an initiative.
_FORCING_FLAG: Final[str] = "plan_required"
#: The field naming the approval that authorised it.
_AUTHORISING_FIELD: Final[str] = "charter_id"

_COPY_METHOD: Final[str] = "model_copy"

#: Where the invariant lives, and the class that carries it.
_MODEL_REL: Final[str] = "src/synthorg/engine/pipeline/models.py"
_MODEL_CLASS: Final[str] = "WorkItem"

#: The one module that may open an initiative, and why it may. Written with the
#: reason because the reason is the whole exemption.
_OWNER_REL: Final[str] = "src/synthorg/meta/charter/dispatch.py"
_OWNER_REASON: Final[str] = (
    "dispatches the charter the operator approved, which is the decision to "
    "commit the organisation to the work"
)


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _Hit:
    """One place outside the owner that forces an initiative."""

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
            f"passes {_FORCING_FLAG}="
            if self.kind == "keyword"
            else f"rewrites {_FORCING_FLAG} through {_COPY_METHOD}"
        )
        return (
            f"{self.rel}:{self.lineno}:{self.col}: {what}, which opens a "
            f"project, a plan and a decomposition run. Committing the "
            f"organisation to a body of work is the operator's decision, taken "
            f"in the charter interview and recorded by their approval. Route "
            f"the request to the charter interview instead."
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
            f"check_charter_authorised_initiative: git ls-files failed in "
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


def _mapping_keys(node: ast.expr) -> frozenset[str]:
    """Return the statically-known string keys of an update mapping.

    Reads a dict literal and the ``dict(plan_required=...)`` keyword form,
    which are the same mapping spelled two ways.

    Returns:
        The keys, or an empty set when none can be read.
    """
    if isinstance(node, ast.Dict):
        return frozenset(
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dict"
    ):
        return frozenset(kw.arg for kw in node.keywords if kw.arg is not None)
    return frozenset()


def _named_mapping_keys(tree: ast.Module) -> dict[str, frozenset[str]]:
    """Map each module-local name to the mapping keys it is built with.

    Building the mapping a line before the copy is the shape a literal-only
    check is one refactor away from missing.

    Args:
        tree: The parsed module.

    Returns:
        The name-to-keys map, holding only names that carry the forcing flag.
    """
    keys: dict[str, set[str]] = {}

    def _add(name: str, found: frozenset[str]) -> None:
        if _FORCING_FLAG in found:
            keys.setdefault(name, set()).update(found)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add(target.id, _mapping_keys(node.value))
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
                frozenset() if node.value is None else _mapping_keys(node.value),
            )
    return {name: frozenset(found) for name, found in keys.items()}


def _rewrites_flag(node: ast.Call, named: dict[str, frozenset[str]]) -> bool:
    """Return whether *node* is a ``model_copy`` that re-points the flag.

    Returns:
        ``True`` when the call's ``update=`` mapping names the forcing flag.
    """
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == _COPY_METHOD):
        return False
    for keyword in node.keywords:
        if keyword.arg != "update":
            continue
        if isinstance(keyword.value, ast.Name):
            return _FORCING_FLAG in named.get(keyword.value.id, frozenset())
        return _FORCING_FLAG in _mapping_keys(keyword.value)
    return False


@dataclass(frozen=True)
class _Site:
    """One place the forcing flag is set, and whether a charter rides along."""

    lineno: int
    col: int
    kind: str
    authorised: bool


def _site_kind(node: ast.Call, named: dict[str, frozenset[str]]) -> str | None:
    """Classify *node* as a forcing site, or ``None`` when it is not one.

    Returns:
        ``"keyword"``, ``"copy"``, or ``None``.
    """
    if _FORCING_FLAG in {kw.arg for kw in node.keywords}:
        return "keyword"
    return "copy" if _rewrites_flag(node, named) else None


def _forcing_sites(tree: ast.Module) -> list[_Site]:
    """Return every call in *tree* that forces an initiative.

    Only the OUTERMOST forcing call in a nest is reported:
    ``model_copy(update=dict(plan_required=True))`` is one decision written
    two levels deep, and counting it twice would report a single violation as
    two while making the owner's own site look like a pair.

    Args:
        tree: The parsed module.

    Returns:
        One entry per site, outermost first.
    """
    named = _named_mapping_keys(tree)
    sites: list[_Site] = []

    def _visit(node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            kind = _site_kind(node, named)
            if kind is not None:
                sites.append(
                    _Site(
                        lineno=node.lineno,
                        col=node.col_offset,
                        kind=kind,
                        authorised=_AUTHORISING_FIELD
                        in {kw.arg for kw in node.keywords},
                    )
                )
                return
        for child in ast.iter_child_nodes(node):
            _visit(child)

    _visit(tree)
    return sites


def _class_def(tree: ast.Module, name: str) -> ast.ClassDef | None:
    """Return the top-level class *name* in *tree*.

    Returns:
        The class definition, or ``None`` when the module does not define it.
    """
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _declares_field(cls: ast.ClassDef, field: str) -> bool:
    """Return whether *cls* declares an annotated *field*.

    Returns:
        ``True`` when the class body annotates that name.
    """
    return any(
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == field
        for node in cls.body
    )


def _guards_the_pair(fn: ast.FunctionDef) -> bool:
    """Return whether *fn* refuses on the flag-without-charter pair.

    Both attribute names must be read inside the function and a ``raise`` must
    be reachable from it: a validator that stopped raising is a validator that
    stopped validating, and it would read as present to a shallower check.

    Returns:
        ``True`` when the function reads both names and can raise.
    """
    read: set[str] = {
        node.attr for node in ast.walk(fn) if isinstance(node, ast.Attribute)
    }
    if not {_FORCING_FLAG, _AUTHORISING_FIELD} <= read:
        return False
    return any(isinstance(node, ast.Raise) for node in ast.walk(fn))


def _invariant_faults(project_root: Path) -> list[str]:
    """Return every way the model has stopped carrying the invariant.

    Returns:
        One message per fault; empty when the invariant stands.

    Raises:
        GateSourceError: If the model module cannot be read or parsed.
    """
    path = project_root / _MODEL_REL
    if not path.is_file():
        return [f"{_MODEL_REL}: missing; the invariant has nowhere to live."]
    _, tree = read_and_parse(path)
    cls = _class_def(tree, _MODEL_CLASS)
    if cls is None:
        return [f"{_MODEL_REL}: {_MODEL_CLASS} is gone; nothing carries the rule."]
    faults: list[str] = []
    if not _declares_field(cls, _AUTHORISING_FIELD):
        faults.append(
            f"{_MODEL_REL}: {_MODEL_CLASS} no longer declares "
            f"{_AUTHORISING_FIELD}, so a brief cannot name what authorised it."
        )
    if not any(
        isinstance(node, ast.FunctionDef) and _guards_the_pair(node)
        for node in cls.body
    ):
        faults.append(
            f"{_MODEL_REL}: {_MODEL_CLASS} no longer refuses "
            f"{_FORCING_FLAG} without {_AUTHORISING_FIELD}. That refusal is "
            f"what covers the constructions no static read can open, so "
            f"without it this gate guards a door with no lock behind it."
        )
    return faults


def _scan_all(project_root: Path) -> tuple[list[_Hit], list[str], int]:
    """Scan ``src/synthorg`` for initiatives forced outside the owner.

    Returns:
        ``(hits, owner_faults, owner_site_count)``.

    Raises:
        GateSourceError: If any source file cannot be read or parsed.
    """
    abs_root = project_root / _SCAN_ROOT_REL
    hits: list[_Hit] = []
    owner_faults: list[str] = []
    owner_sites = 0
    for path, rel in _git_tracked_python_files(abs_root, project_root):
        _, tree = read_and_parse(path)
        sites = _forcing_sites(tree)
        if not sites:
            continue
        if rel != _OWNER_REL:
            hits.extend(
                _Hit(rel=rel, lineno=site.lineno, col=site.col, kind=site.kind)
                for site in sites
            )
            continue
        owner_sites += len(sites)
        owner_faults.extend(
            f"{rel}:{site.lineno}: forces an initiative without passing "
            f"{_AUTHORISING_FIELD}=. The owner is the path that HAS the "
            f"operator's approval; a brief it builds without naming the "
            f"charter fails at request time instead of here."
            for site in sites
            if not site.authorised
        )
    return hits, owner_faults, owner_sites


def _iter_hits(hits: list[_Hit]) -> Iterator[str]:
    """Yield each violation message in a stable order.

    Yields:
        One formatted violation line per hit.
    """
    for hit in sorted(hits, key=lambda h: (h.rel, h.lineno, h.col)):
        yield hit.message()


def _report(messages: list[str]) -> None:
    """Print each configuration fault on stderr."""
    for message in messages:
        print(f"check_charter_authorised_initiative: {message}", file=sys.stderr)


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
        faults = _invariant_faults(project_root)
        hits, owner_faults, owner_sites = _scan_all(project_root)
    except GateSourceError as exc:
        print(f"check_charter_authorised_initiative: {exc}", file=sys.stderr)
        return 2

    faults.extend(owner_faults)
    if not owner_sites:
        faults.append(
            f"{_OWNER_REL}: declared as the one initiative owner ({_OWNER_REASON}) "
            f"but sets no {_FORCING_FLAG}. Point the declaration at the module "
            f"that took the charter dispatch over: an unused exemption is one "
            f"the next intake path inherits silently."
        )
    if faults:
        _report(faults)
        return 2

    if not hits:
        return 0
    for message in _iter_hits(hits):
        print(message)
    print(
        f"\n{len(hits)} site(s) force an initiative outside {_OWNER_REL}. "
        f"There is one intake path for work that stands up a project, and it "
        f"ends at an operator approving a charter.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
