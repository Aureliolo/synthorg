#!/usr/bin/env python3
"""Pre-push / CI gate: one wiring path per declared subsystem.

A subsystem declared in ``src/synthorg/api/subsystems/registry.py`` is brought
up by the reconciler. If some other module also calls its wiring function, that
second path is a hand-kept list of what someone believed needed rewiring, and
the two drift: that is exactly how ``wire_memory_backend`` came to be missing
from ``_rewire_post_setup_features`` while thirteen of its siblings were in it.

The gate reads the registry's own activation adapters to learn which wiring
functions the reconciler owns, then fails any call to one of them from anywhere
else. A composite in the defining module counts: three of them
(``wire_organization_read_services`` and its peers) existed only to run several
owned wirers in a fixed order, which is the same second list one file inwards.

Per-line opt-out: append ``# lint-allow: subsystem-single-owner -- <reason>``
to the offending call line.

Usage::

    python scripts/check_subsystems_single_owner.py
    python scripts/check_subsystems_single_owner.py --repo-root /path/to/repo
"""

import argparse
import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_REL = "src/synthorg/api/subsystems/registry.py"
_SOURCE_REL = "src/synthorg"
_ALLOW_MARKER = "lint-allow: subsystem-single-owner"


@dataclass(frozen=True, slots=True)
class OwnedWiring:
    """A wiring function the subsystem registry activates.

    Attributes:
        name: The function name, e.g. ``wire_memory_backend``.
        module: Dotted module the registry imports it from.
    """

    name: str
    module: str


@dataclass(frozen=True, slots=True)
class Violation:
    """A second call site for a registry-owned wiring function."""

    path: str
    line: int
    name: str


def owned_wiring(repo_root: Path) -> tuple[OwnedWiring, ...]:
    """Collect every wiring function the registry's adapters import and call.

    Returns:
        The owned wiring functions, sorted by name.

    Raises:
        ValueError: When the registry module cannot be read or parsed.
    """
    path = repo_root / _REGISTRY_REL
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        msg = f"cannot read {_REGISTRY_REL}: {exc}"
        raise ValueError(msg) from exc

    owned: set[OwnedWiring] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if not node.module.startswith("synthorg."):
            continue
        for alias in node.names:
            if _is_wiring_name(alias.name):
                owned.add(OwnedWiring(name=alias.name, module=node.module))
    return tuple(sorted(owned, key=lambda o: o.name))


def _is_wiring_name(name: str) -> bool:
    """Report whether *name* is a wiring entry point the registry owns.

    Matched on the leading token rather than a substring: ``unwire_memory``
    contains "wire" but is the teardown half, which the registry calls from
    its own deactivate adapters and which the single-owner rule does not
    govern. Counting it would report the registry's own teardown as a second
    caller of its wiring.

    Args:
        name: The imported symbol.

    Returns:
        ``True`` for ``wire_*`` and its private ``_wire_*`` form.
    """
    return name.startswith(("wire_", "_wire_"))


def _tracked_sources(repo_root: Path) -> Iterator[Path]:
    """Yield every Python module under ``src/synthorg``."""
    yield from sorted((repo_root / _SOURCE_REL).rglob("*.py"))


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """Map each locally-bound alias to the wiring name it was imported as.

    ``from x import wire_y as _wy`` binds a second name for the same
    function, and a call through it is the same second caller the rule
    exists to catch.

    Args:
        tree: The parsed module.

    Returns:
        ``{local_name: original_name}`` for aliased wiring imports.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.asname is not None and _is_wiring_name(alias.name):
                aliases[alias.asname] = alias.name
    return aliases


def _called_names(tree: ast.AST) -> Iterator[tuple[str, int, int]]:
    """Yield ``(name, start_line, end_line)`` for every call in *tree*.

    Covers the three shapes a wiring call can take: a plain name, a name
    bound by an aliased import, and an attribute on an imported module
    (``feature_wiring.wire_docs_engine()``). The line span is the call's,
    not its callee's, so a per-line opt-out marker can sit anywhere in a
    multi-line call rather than only on its first line.
    """
    aliases = _import_aliases(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = aliases.get(func.id, func.id)
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        yield name, node.lineno, node.end_lineno or node.lineno


def scan_repo(repo_root: Path) -> tuple[Violation, ...]:
    """Find every second call site of a registry-owned wiring function.

    Returns:
        The violations, in file then line order.

    Raises:
        ValueError: When the registry cannot be read.
    """
    owned_names = {entry.name for entry in owned_wiring(repo_root)}
    registry_path = (repo_root / _REGISTRY_REL).resolve()

    violations: list[Violation] = []
    for path in _tracked_sources(repo_root):
        if path.resolve() == registry_path:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except OSError, SyntaxError:
            continue
        lines = source.splitlines()
        for name, lineno, end_lineno in _called_names(tree):
            if name not in owned_names:
                continue
            span = lines[lineno - 1 : end_lineno]
            if any(_ALLOW_MARKER in line for line in span):
                continue
            violations.append(
                Violation(
                    path=path.relative_to(repo_root).as_posix(),
                    line=lineno,
                    name=name,
                )
            )
    return tuple(sorted(violations, key=lambda v: (v.path, v.line)))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` when every declared subsystem has exactly one wiring path.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    repo_root = (
        args.repo_root.resolve()
        if args.repo_root is not None
        else Path(__file__).resolve().parent.parent
    )

    try:
        violations = scan_repo(repo_root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for violation in violations:
        print(
            f"{violation.path}:{violation.line}: {violation.name} is activated by"
            " the subsystem registry; a second caller is a parallel wiring path"
        )
    if violations:
        print(
            f"\n{len(violations)} second wiring path(s). Let the reconciler own"
            " the subsystem, or opt out per-line with"
            f" '# {_ALLOW_MARKER} -- <reason>'.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {len(owned_wiring(repo_root))} declared subsystems, one path each.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
