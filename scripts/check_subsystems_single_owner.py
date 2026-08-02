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
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_REL = "src/synthorg/api/subsystems/registry.py"
_SOURCE_REL = "src/synthorg"
_ALLOW_MARKER = "lint-allow: subsystem-single-owner"
# Every name _is_wiring_name accepts starts with `wire_` or `_wire_`, so both
# spellings contain this. A file whose text lacks it can neither define nor
# call wiring, which is what lets the scan skip parsing most of the tree.
_WIRING_SUBSTRING = "wire_"


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


def _module_of(repo_root: Path, path: Path) -> str:
    """Return the dotted module name a source file provides.

    Args:
        repo_root: Repository root the layout is relative to.
        path: The source file.

    Returns:
        The dotted name, with a package's ``__init__`` reduced to the package.
    """
    parts = list(path.relative_to(repo_root / "src").with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_symbols(tree: ast.AST, module: str) -> dict[str, OwnedWiring]:
    """Map each locally-bound name to the wiring function it was imported as.

    ``from x import wire_y as _wy`` binds a second name for the same function,
    and a call through it is the same second caller the rule exists to catch.

    Args:
        tree: The parsed module.
        module: Dotted name of the module being parsed, for relative imports.

    Returns:
        ``{local_name: OwnedWiring}`` for every imported wiring function.
    """
    symbols: dict[str, OwnedWiring] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        source = _resolved_import(node, module)
        if source is None:
            continue
        for alias in node.names:
            if _is_wiring_name(alias.name):
                symbols[alias.asname or alias.name] = OwnedWiring(
                    name=alias.name, module=source
                )
    return symbols


def _imported_modules(tree: ast.AST, module: str) -> dict[str, str]:
    """Map each locally-bound name to the module it refers to.

    Args:
        tree: The parsed module.
        module: Dotted name of the module being parsed, for relative imports.

    Returns:
        ``{local_name: dotted_module}`` for imports of whole modules, which is
        what makes ``feature_wiring.wire_docs_engine()`` resolvable.
    """
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            source = _resolved_import(node, module)
            if source is None:
                continue
            for alias in node.names:
                if not _is_wiring_name(alias.name):
                    modules[alias.asname or alias.name] = f"{source}.{alias.name}"
    return modules


def _resolved_import(node: ast.ImportFrom, module: str) -> str | None:
    """Return the absolute module an import statement reads from.

    Args:
        node: The import statement.
        module: Dotted name of the module containing it.

    Returns:
        The absolute dotted module, or ``None`` when it names nothing.
    """
    if not node.level:
        return node.module
    package = module.split(".")[: -node.level] if module else []
    tail = node.module.split(".") if node.module else []
    return ".".join([*package, *tail]) or None


def _call_targets(tree: ast.AST, module: str) -> Iterator[tuple[OwnedWiring, int, int]]:
    """Yield ``(target, start_line, end_line)`` for every resolvable call.

    Covers the shapes a wiring call can take: a name bound by an import,
    an attribute on an imported module (``feature_wiring.wire_docs_engine()``),
    and a bare call to a function the same module defines, which is the
    composite case. Each resolves to the module the function lives in, so a
    call to an unrelated function of the same name is not mistaken for one.
    The line span is the call's, not its callee's, so a per-line opt-out
    marker can sit anywhere in a multi-line call.

    Args:
        tree: The parsed module.
        module: Dotted name of the module being parsed.

    Yields:
        The resolved call target and the line span of the call.
    """
    symbols = _imported_symbols(tree, module)
    modules = _imported_modules(tree, module)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _resolve_callee(node.func, symbols, modules, module)
        if target is not None:
            yield target, node.lineno, node.end_lineno or node.lineno


def _resolve_callee(
    func: ast.expr,
    symbols: dict[str, OwnedWiring],
    modules: dict[str, str],
    module: str,
) -> OwnedWiring | None:
    """Resolve one call's callee to the wiring function it names.

    Args:
        func: The callee expression.
        symbols: Wiring functions imported into this module.
        modules: Modules imported into this module.
        module: Dotted name of the module being parsed.

    Returns:
        The resolved target, or ``None`` when the call names no wiring
        function or names one this module cannot be shown to reach.
    """
    if isinstance(func, ast.Name):
        imported = symbols.get(func.id)
        if imported is not None:
            return imported
        # Not imported, so a matching name is one this module defines: the
        # composite that runs several owned wirers in a fixed order.
        if not _is_wiring_name(func.id):
            return None
        return OwnedWiring(name=func.id, module=module)
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and _is_wiring_name(func.attr)
    ):
        source = modules.get(func.value.id)
        return None if source is None else OwnedWiring(name=func.attr, module=source)
    return None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A source file that mentions wiring, read and parsed once.

    Attributes:
        path: The file on disk.
        module: Its dotted module name.
        source: Its text, kept for the per-line opt-out marker.
        tree: Its parsed AST, shared by the definition and call passes.
    """

    path: Path
    module: str
    source: str
    tree: ast.Module


def _wiring_candidates(repo_root: Path) -> list[_Candidate]:
    """Read and parse every source file that could carry wiring.

    The definition pass and the call pass both need the same trees, so they
    are parsed once here rather than once each. The substring gate runs
    before the parse because the tree under ``src/synthorg`` is large and
    only a small fraction of it mentions wiring at all; parsing all of it
    twice put this scan over the test suite's per-test timeout.

    Args:
        repo_root: Repository root to scan.

    Returns:
        One entry per readable, parseable file whose text mentions wiring.
    """
    candidates: list[_Candidate] = []
    for path in _tracked_sources(repo_root):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _WIRING_SUBSTRING not in source:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        candidates.append(
            _Candidate(
                path=path,
                module=_module_of(repo_root, path),
                source=source,
                tree=tree,
            )
        )
    return candidates


def _definition_modules(candidates: Sequence[_Candidate]) -> dict[str, set[str]]:
    """Map each wiring function name to every module defining one.

    A name defined once anywhere in the tree is unambiguous however it was
    imported, which matters because a package re-export makes the module a
    caller imported from differ from the one the registry names.

    Args:
        candidates: The parsed sources to read definitions from.

    Returns:
        ``{function_name: {defining modules}}``.
    """
    definitions: dict[str, set[str]] = {}
    for candidate in candidates:
        for node in candidate.tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if _is_wiring_name(node.name):
                definitions.setdefault(node.name, set()).add(candidate.module)
    return definitions


def scan_repo(
    repo_root: Path, owned: tuple[OwnedWiring, ...] | None = None
) -> tuple[Violation, ...]:
    """Find every second call site of a registry-owned wiring function.

    Args:
        repo_root: Repository root to scan.
        owned: The registry's owned wiring, when the caller already read it.
            Passed in so a run that also reports the count parses the registry
            once rather than once per question asked of it.

    Returns:
        The violations, in file then line order.

    Raises:
        ValueError: When the registry cannot be read.
    """
    owners = {
        entry.name: entry
        for entry in (owned if owned is not None else owned_wiring(repo_root))
    }
    candidates = _wiring_candidates(repo_root)
    definitions = _definition_modules(candidates)
    # Both sides are joined from the same repo_root and _tracked_sources
    # introduces no symlink or `..`, so they compare equal unresolved.
    registry_path = repo_root / _REGISTRY_REL

    violations: list[Violation] = []
    for candidate in candidates:
        path = candidate.path
        if path == registry_path:
            continue
        lines = candidate.source.splitlines()
        for target, lineno, end_lineno in _call_targets(
            candidate.tree, candidate.module
        ):
            if not _is_owned(target, owners, definitions):
                continue
            span = lines[lineno - 1 : end_lineno]
            if any(_ALLOW_MARKER in line for line in span):
                continue
            violations.append(
                Violation(
                    path=path.relative_to(repo_root).as_posix(),
                    line=lineno,
                    name=target.name,
                )
            )
    return tuple(sorted(violations, key=lambda v: (v.path, v.line)))


def _is_owned(
    target: OwnedWiring,
    owners: dict[str, OwnedWiring],
    definitions: dict[str, set[str]],
) -> bool:
    """Report whether a resolved call reaches a registry-owned function.

    A name only one module defines is that function however it was imported,
    which is what keeps a package re-export from reading as a different
    function. When several modules define the name, the module the call
    resolves to decides, so an unrelated namesake is left alone.

    Args:
        target: The resolved call target.
        owners: Registry-owned wiring, by function name.
        definitions: Every module defining each wiring name.

    Returns:
        ``True`` when this call is a second path to an owned subsystem.
    """
    owner = owners.get(target.name)
    if owner is None:
        return False
    if len(definitions.get(target.name, set())) <= 1:
        return True
    return target.module == owner.module


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
        owned = owned_wiring(repo_root)
        violations = scan_repo(repo_root, owned)
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
    print(f"OK: {len(owned)} declared subsystems, one path each.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
