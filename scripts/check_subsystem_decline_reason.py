#!/usr/bin/env python3
"""Pre-push / CI gate: a subsystem that can decline must say why.

``GET /subsystems`` exists to answer "why is this not up". When an activation
returns without installing its capability and declares nothing, the reconciler
has nothing to report and falls back to "declined on a condition it does not
declare; see the wiring log". Five of seven blocked subsystems answered that in
a live run: the endpoint whose job is to say why told the operator to read a
container log.

The rule is one owner for the reason: the code that decided. A subsystem
satisfies it when ANY of the following holds.

* Its spec declares ``settings=``. The reconciler reads those live and names a
  blank one, which is the shape behind most declines in this tree.
* Its activation chain contains a reachable ``raise SubsystemDeclinedError``.
  The activation names its own condition and the reconciler reports it verbatim.
* Its activation chain cannot decline: no early ``return``, so it either
  installs the capability or raises.

The chain is the registry adapter plus the wiring functions it calls, resolved
through the same imports the adapter uses, so a reason declared one call inward
counts.

There is deliberately no per-line opt-out. An activation that genuinely cannot
name its condition is the defect this gate exists to surface.

Usage::

    python scripts/check_subsystem_decline_reason.py
    python scripts/check_subsystem_decline_reason.py --repo-root /path/to/repo
"""

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_REL = "src/synthorg/api/subsystems/registry.py"
_SRC_REL = "src"
_DECLINED_ERROR = "SubsystemDeclinedError"
_SPEC_CALL = "SubsystemSpec"


@dataclass(frozen=True, slots=True)
class Spec:
    """One declared subsystem, reduced to what the rule reads.

    Attributes:
        name: The subsystem name as the status surface reports it.
        activate: Name of the activation adapter in the registry.
        has_settings: Whether the spec declares a non-empty ``settings=``.
        line: Where the spec is declared, for the failure message.
    """

    name: str
    activate: str | None
    has_settings: bool
    line: int


@dataclass(frozen=True, slots=True)
class Violation:
    """A subsystem that can decline with nothing to report."""

    name: str
    activate: str
    line: int


def _parse(path: Path) -> ast.Module | None:
    """Parse a module, or return ``None`` when it cannot be read.

    Returns:
        The parsed tree, or ``None``.
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except OSError, SyntaxError:
        return None


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Map every module-level function name to its definition.

    Returns:
        ``{name: definition}``.
    """
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _import_sources(tree: ast.Module) -> dict[str, str]:
    """Map each imported symbol to the dotted module it came from.

    Function-local imports count: every registry adapter imports its wiring
    function inside the function body to keep the cold-import graph light.

    Returns:
        ``{local_name: dotted_module}``.
    """
    sources: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                sources[alias.asname or alias.name] = node.module
    return sources


def read_specs(registry: ast.Module) -> tuple[Spec, ...]:
    """Read every ``SubsystemSpec`` declaration out of the registry.

    Returns:
        The declared specs, in declaration order.
    """
    specs: list[Spec] = []
    for node in ast.walk(registry):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == _SPEC_CALL):
            continue
        name = ""
        activate: str | None = None
        has_settings = False
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                name = str(keyword.value.value)
            elif keyword.arg == "activate" and isinstance(keyword.value, ast.Name):
                activate = keyword.value.id
            elif keyword.arg == "settings":
                has_settings = bool(getattr(keyword.value, "elts", None))
        specs.append(
            Spec(
                name=name,
                activate=activate,
                has_settings=has_settings,
                line=node.lineno,
            )
        )
    return tuple(specs)


def _is_bare_return(node: ast.AST) -> bool:
    """Report whether *node* is a valueless ``return``.

    Returns:
        ``True`` for ``return`` and ``return None``.
    """
    return isinstance(node, ast.Return) and (
        node.value is None
        or (isinstance(node.value, ast.Constant) and node.value.value is None)
    )


def _tests_absence(test: ast.expr) -> bool:
    """Report whether a guard backs out because something is MISSING.

    ``if x is not None: return`` is the idempotency guard: the capability is
    already installed, so the reconciler's probe reads it up and there is no
    decline to explain. ``if x is None: return`` and ``if not flag: return``
    are the other shape: the activation is backing out because a collaborator
    or a toggle it needs is absent, which is precisely the condition an
    operator is asking about.

    Args:
        test: The ``if`` test guarding a bare return.

    Returns:
        ``True`` when any branch of the test asserts an absence.
    """
    for node in ast.walk(test):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return True
        if isinstance(node, ast.Compare) and any(
            isinstance(op, ast.Is)
            and isinstance(cmp, ast.Constant)
            and cmp.value is None
            for op, cmp in zip(node.ops, node.comparators, strict=True)
        ):
            return True
    return False


def _has_early_return(fn: ast.AST) -> bool:
    """Report whether a function can back out because something is missing.

    A guarded bare ``return`` is how a wiring function declines: the
    capability is not installed and the reconciler reads a decline it cannot
    explain. An idempotency guard is excluded (see :func:`_tests_absence`),
    and so is an unguarded trailing ``return``, which cannot skip the wiring
    above it.

    Returns:
        ``True`` when the function has a decline path.
    """
    return any(
        isinstance(node, ast.If)
        and any(_is_bare_return(stmt) for stmt in node.body)
        and _tests_absence(node.test)
        for node in ast.walk(fn)
    )


def _raises_declined(fn: ast.AST) -> bool:
    """Report whether a function raises ``SubsystemDeclinedError``.

    Returns:
        ``True`` when a reachable raise names the error.
    """
    return any(
        isinstance(node, ast.Raise)
        and node.exc is not None
        and _DECLINED_ERROR in ast.unparse(node.exc)
        for node in ast.walk(fn)
    )


def _called_names(fn: ast.AST) -> set[str]:
    """Collect every plain-name call the function makes.

    Returns:
        The callee names.
    """
    return {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


class _ChainReader:
    """Resolves an activation chain and answers the two questions about it.

    Args:
        repo_root: Repository root the ``src`` layout is relative to.
        registry: The parsed registry module.
    """

    def __init__(self, repo_root: Path, registry: ast.Module) -> None:
        self._repo_root = repo_root
        self._registry_functions = _functions(registry)
        self._registry_imports = _import_sources(registry)
        self._cache: dict[str, ast.Module | None] = {}

    def inspect(self, activate: str) -> tuple[bool, bool]:
        """Walk the chain from *activate*, one call inward.

        Args:
            activate: Name of the registry activation adapter.

        Returns:
            ``(can_decline, declares_reason)``.
        """
        adapter = self._registry_functions.get(activate)
        if adapter is None:
            return True, False
        can_decline = _has_early_return(adapter)
        declares = _raises_declined(adapter)
        for callee in _called_names(adapter):
            module = self._registry_imports.get(callee)
            if module is None:
                continue
            tree = self._module(module)
            if tree is None:
                continue
            target = _functions(tree).get(callee)
            if target is None:
                continue
            can_decline = can_decline or _has_early_return(target)
            declares = declares or _raises_declined(target)
        return can_decline, declares

    def _module(self, dotted: str) -> ast.Module | None:
        """Parse a dotted module under ``src``, memoised.

        Returns:
            The parsed tree, or ``None`` when it is not a source file here.
        """
        if dotted not in self._cache:
            path = self._repo_root / _SRC_REL / (dotted.replace(".", "/") + ".py")
            self._cache[dotted] = _parse(path) if path.is_file() else None
        return self._cache[dotted]


def scan_repo(repo_root: Path) -> tuple[Violation, ...]:
    """Find every subsystem that can decline without declaring why.

    Args:
        repo_root: Repository root to scan.

    Returns:
        The violations, in declaration order.

    Raises:
        ValueError: When the registry cannot be read.
    """
    registry = _parse(repo_root / _REGISTRY_REL)
    if registry is None:
        msg = f"cannot read {_REGISTRY_REL}"
        raise ValueError(msg)
    reader = _ChainReader(repo_root, registry)
    violations: list[Violation] = []
    for spec in read_specs(registry):
        if spec.has_settings or spec.activate is None:
            continue
        can_decline, declares = reader.inspect(spec.activate)
        if can_decline and not declares:
            violations.append(
                Violation(name=spec.name, activate=spec.activate, line=spec.line)
            )
    return tuple(violations)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` when every subsystem can explain a decline.
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
            f"{_REGISTRY_REL}:{violation.line}: subsystem {violation.name!r} can"
            f" return from {violation.activate} without installing its"
            " capability, and declares no reason"
        )
    if violations:
        print(
            f"\n{len(violations)} subsystem(s) can decline with nothing to report."
            f" Raise {_DECLINED_ERROR} on the branch that backs out, or declare"
            " the settings the activation reads.",
            file=sys.stderr,
        )
        return 1
    print("OK: every declared subsystem can explain a decline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
