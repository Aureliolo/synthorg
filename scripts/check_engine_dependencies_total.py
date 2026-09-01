#!/usr/bin/env python3
"""Pre-push / CI gate: an engine's wiring is declared in full, never omitted.

``AgentEngine.__init__`` took 64 keyword arguments, 63 of them defaulting to
``None``. A deployment passed 51 and the harness recording the loop passed 8,
for eight recordings, and nothing at any layer could tell: omitting
``compaction_callback`` looks exactly like deciding against it. The corpus that
produced a root-cause analysis of the loop had therefore measured an engine the
product does not ship.

The fix is that a partially wired engine is not CONSTRUCTABLE.
``EngineDependencies`` and its bundles carry no defaults, so mypy refuses a
partial literal by name. Absence stays allowed and stays common; absence by
OMISSION does not, because ``compaction_callback=None`` is a decision a reader
can see and a missing keyword is a decision nobody made.

This gate covers the three ways that contract is lost that a type-checker
cannot see.

Detection
---------
**A default reappears.** Any field of any gated dataclass carrying a value
fails. One default is all it takes: the field stops being a decision the caller
makes and goes back to being one the reader cannot see.

**A construction skips the field list.** ``EngineDependencies(**mapping)`` type
-checks against nothing, so the whole contract is bypassed by four characters.
Same for every bundle, for ``CheckpointWiring`` and for ``EngineAssemblyInputs``.
``AgentEngine(...)`` is held to exactly one positional argument for the same
reason: the engine takes its wiring as one declared object or it does not take
it at all.

**A defaults-supplying builder appears.** A helper that fills in what a caller
did not name re-creates the defect one layer up, and a type-checker is happy
throughout. Exactly one is sanctioned, ``tests/_shared/engine_deps.py``, because
a unit test about budget refusal is making no claim about the review pipeline
and should not restate sixty absences to say so. The absences are spelled once,
there, where a reviewer can read them.

**The instrument borrows the test helper.** ``evals/`` measures the product, so
a harness that fills in what it forgot is precisely the defect under
measurement. Importing the tests helper from ``evals/`` fails.

Derivation
----------
The gated types are DERIVED, never listed: every dataclass declared in
``src/synthorg/engine/dependencies/``, plus ``CheckpointWiring`` and
``EngineAssemblyInputs`` at their declared paths. A twelfth bundle is covered
the day it lands, and a list is one bundle away from disagreeing with the
package it claims to enforce.

Allowlist / opt-out
-------------------
There is deliberately no per-line opt-out and no baseline. A default on one of
these fields is the defect itself; a second defaults-supplying builder is the
defect one layer up. An exception means changing the declared sanctioned module,
in the open.

Usage::

    uv run python scripts/check_engine_dependencies_total.py

Exit codes:
    0 -- every declaration is total and every construction names its fields.
    1 -- a default, a splat, a builder, or a harness borrowing the test helper.
    2 -- configuration error (bad ``--repo-root``, a missing or renamed anchor,
         a sanctioned module that no longer builds one, or a source file that
         could not be read or parsed -- fail-closed).
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

#: Where the bundles live. Every dataclass declared under here is gated.
_PACKAGE_REL: Final[str] = "src/synthorg/engine/dependencies"
#: The root type, which must be one of the dataclasses that package declares.
_ROOT_TYPE: Final[str] = "EngineDependencies"

#: Gated types declared outside the package, each at its own path. Both are
#: the same contract reached by a different route: one makes checkpointing
#: both-or-neither, the other is what a caller of the boot assembly owns.
_SATELLITE_TYPES: Final[dict[str, str]] = {
    "CheckpointWiring": "src/synthorg/engine/checkpoint/wiring.py",
    "EngineAssemblyInputs": "src/synthorg/workers/engine_assembly.py",
}

#: The engine, and the module declaring it.
_ENGINE_REL: Final[str] = "src/synthorg/engine/agent_engine.py"
_ENGINE_CLASS: Final[str] = "AgentEngine"
#: ``self`` plus the one dependencies object, and nothing else.
_ENGINE_INIT_ARITY: Final[int] = 2

#: The one module that may supply defaults, and why it may.
_SANCTIONED_REL: Final[str] = "tests/_shared/engine_deps.py"
_SANCTIONED_REASON: Final[str] = (
    "spells every absence once, so a unit test overrides only the bundle it "
    "is actually about"
)

#: What may not reach the instrument measuring the product.
_TESTS_PACKAGE: Final[str] = "tests"

_SCAN_ROOTS: Final[tuple[str, ...]] = ("src/synthorg", "evals", "scripts", "tests")
#: Where a borrowed test helper is a defect rather than ordinary test wiring.
_INSTRUMENT_ROOTS: Final[tuple[str, ...]] = ("src/synthorg", "evals")

_DATACLASS_DECORATORS: Final[frozenset[str]] = frozenset({"dataclass", "dataclasses"})


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True, slots=True)
class _Hit:
    """One place the wiring contract is lost.

    Attributes:
        rel: Repo-relative POSIX path of the offending file.
        lineno: 1-indexed line the violation anchors to.
        kind: Which of the four detections fired.
        detail: What was found, in the gate's own words.
    """

    rel: str
    lineno: int
    kind: str
    detail: str

    def message(self) -> str:
        """Return the human-facing violation line.

        Returns:
            The formatted message.
        """
        return f"{self.rel}:{self.lineno}: [{self.kind}] {self.detail}"


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments.

    Returns:
        The resolved project-root directory.

    Raises:
        ProjectRootError: If *repo_root* cannot be resolved to a directory.
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
    abs_root: Path, project_root: Path
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
            f"check_engine_dependencies_total: git ls-files failed in "
            f"{project_root} ({type(exc).__name__}: {exc}); falling back to "
            f"rglob (scope widens to untracked / gitignored files).",
            file=sys.stderr,
        )
        return [
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p and p.endswith(".py")]
    return [((project_root / rel_path), rel_path) for rel_path in paths]


def _is_dataclass_decorated(node: ast.ClassDef) -> bool:
    """Whether *node* carries a ``@dataclass`` decorator in any spelling.

    Returns:
        ``True`` for the bare, called, and ``dataclasses.``-qualified forms.
    """
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id in _DATACLASS_DECORATORS:
            return True
        if isinstance(target, ast.Attribute) and target.attr == "dataclass":
            return True
    return False


def _defaulted_fields(node: ast.ClassDef) -> Iterator[tuple[str, int]]:
    """Yield ``(field_name, lineno)`` for every annotated field with a value.

    A dataclass field is an ``AnnAssign`` in the class body; a value on it is
    a default however it is spelled, ``field(default_factory=...)`` included.

    Yields:
        Each defaulted field of *node*.
    """
    for stmt in node.body:
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.value is not None
        ):
            yield stmt.target.id, stmt.lineno


def _class_defs(tree: ast.Module) -> Iterator[ast.ClassDef]:
    """Yield every class defined anywhere in *tree*.

    Yields:
        Each ``ClassDef`` node.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield node


@dataclass(frozen=True, slots=True)
class _Anchors:
    """What the scan found before it started, and needs to have found.

    Attributes:
        gated: Every type name whose construction and fields are enforced.
        faults: Configuration faults, each of which is exit 2.
        default_hits: Defaulted fields discovered while reading declarations.
    """

    gated: frozenset[str]
    faults: tuple[str, ...]
    default_hits: tuple[_Hit, ...]


def _collect_declarations(project_root: Path) -> _Anchors:
    """Read the declaring modules and derive the gated type set.

    Returns:
        The anchors, with a fault for every anchor that could not be found.

    Raises:
        GateSourceError: If a declaring module cannot be read or parsed.
    """
    faults: list[str] = []
    hits: list[_Hit] = []
    gated: set[str] = set()

    package = project_root / _PACKAGE_REL
    if not package.is_dir():
        faults.append(
            f"{_PACKAGE_REL}: the dependencies package is missing. A scan that "
            f"cannot find its anchor reads exactly like one finding nothing "
            f"wrong; point the gate at the package that replaced it."
        )
        return _Anchors(frozenset(), tuple(faults), ())

    declaring: list[tuple[Path, str]] = sorted(
        (path, path.relative_to(project_root).as_posix())
        for path in package.rglob("*.py")
    )
    for name, rel in _SATELLITE_TYPES.items():
        satellite = project_root / rel
        if not satellite.is_file():
            faults.append(
                f"{rel}: declared as where {name} lives, and it is not there. "
                f"Repoint the declaration at the module that took it over."
            )
            continue
        declaring.append((satellite, rel))

    wanted_satellites = set(_SATELLITE_TYPES)
    for path, rel in declaring:
        _, tree = read_and_parse(path)
        in_package = path.is_relative_to(package)
        for node in _class_defs(tree):
            if not _is_dataclass_decorated(node):
                continue
            if not in_package and node.name not in wanted_satellites:
                continue
            gated.add(node.name)
            hits.extend(
                _Hit(
                    rel=rel,
                    lineno=lineno,
                    kind="default",
                    detail=(
                        f"{node.name}.{field} carries a default. A field with "
                        f"a default is a decision the reader cannot see; write "
                        f"the absence at every call site instead."
                    ),
                )
                for field, lineno in _defaulted_fields(node)
            )

    if _ROOT_TYPE not in gated:
        faults.append(
            f"{_PACKAGE_REL}: no dataclass named {_ROOT_TYPE} is declared "
            f"there. The root type is what every construction is checked "
            f"against, so the gate cannot report honestly without it."
        )
    faults.extend(
        f"{_SATELLITE_TYPES[name]}: declared as declaring {name}, and no "
        f"such dataclass is there."
        for name in sorted(wanted_satellites - gated)
    )
    return _Anchors(frozenset(gated), tuple(faults), tuple(hits))


def _engine_faults(project_root: Path) -> list[str]:
    """Return a fault for each way ``AgentEngine`` stopped taking one object.

    Returns:
        The faults, empty when the engine takes exactly one declared argument.

    Raises:
        GateSourceError: If the engine module cannot be read or parsed.
    """
    path = project_root / _ENGINE_REL
    if not path.is_file():
        return [f"{_ENGINE_REL}: the engine module is missing."]
    _, tree = read_and_parse(path)
    engine = next(
        (node for node in _class_defs(tree) if node.name == _ENGINE_CLASS), None
    )
    if engine is None:
        return [f"{_ENGINE_REL}: no class named {_ENGINE_CLASS} is declared there."]
    init = next(
        (
            node
            for node in engine.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        ),
        None,
    )
    if init is None:
        return [
            (
                f"{_ENGINE_REL}: {_ENGINE_CLASS} declares no __init__. The "
                f"whole contract is that its wiring arrives as one declared "
                f"object."
            )
        ]
    args = init.args
    positional = [*args.posonlyargs, *args.args]
    if (
        len(positional) != _ENGINE_INIT_ARITY
        or args.kwonlyargs
        or args.vararg
        or args.kwarg
    ):
        return [
            (
                f"{_ENGINE_REL}:{init.lineno}: {_ENGINE_CLASS}.__init__ takes "
                f"more than self and one dependencies object. Sixty-four "
                f"keyword arguments is the shape this gate exists to keep gone."
            )
        ]
    annotation = positional[1].annotation
    named = annotation.id if isinstance(annotation, ast.Name) else None
    if named != _ROOT_TYPE:
        return [
            (
                f"{_ENGINE_REL}:{init.lineno}: {_ENGINE_CLASS}.__init__ takes "
                f"one argument annotated {named!r} rather than {_ROOT_TYPE}."
            )
        ]
    return []


def _call_name(node: ast.Call) -> str | None:
    """Return the constructed name of *node*, bare or attribute-qualified.

    Returns:
        The rightmost identifier of the callee, or ``None``.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _imports_tests_package(tree: ast.Module) -> Iterator[int]:
    """Yield the line of every import reaching the ``tests`` package.

    Yields:
        Each offending import's line number.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == _TESTS_PACKAGE
                or alias.name.startswith(f"{_TESTS_PACKAGE}.")
                for alias in node.names
            ):
                yield node.lineno
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and (
                module == _TESTS_PACKAGE or module.startswith(f"{_TESTS_PACKAGE}.")
            ):
                yield node.lineno


def _supplies_defaults(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return why *node* is a defaults-supplying builder, or ``None``.

    Returns:
        A short description of what makes it one.
    """
    args = node.args
    if args.defaults or any(default is not None for default in args.kw_defaults):
        return "carries a parameter default"
    if args.kwarg is not None:
        return "takes **kwargs, so a caller may name nothing at all"
    return None


def _scan_file(
    rel: str, tree: ast.Module, *, gated: frozenset[str], instrument: bool
) -> Iterator[_Hit]:
    """Yield every contract loss in one parsed module.

    Yields:
        Each violation found in *tree*.
    """
    sanctioned = rel == _SANCTIONED_REL
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield from _call_hits(rel, node, gated=gated)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if sanctioned:
                continue
            returns = node.returns
            if not isinstance(returns, ast.Name) or returns.id not in gated:
                continue
            reason = _supplies_defaults(node)
            if reason is not None:
                yield _Hit(
                    rel=rel,
                    lineno=node.lineno,
                    kind="builder",
                    detail=(
                        f"{node.name}() returns {returns.id} and {reason}. A "
                        f"helper that fills in what a caller did not name "
                        f"rebuilds the defect one layer up; "
                        f"{_SANCTIONED_REL} is the one sanctioned place "
                        f"({_SANCTIONED_REASON})."
                    ),
                )
    if instrument:
        for lineno in _imports_tests_package(tree):
            yield _Hit(
                rel=rel,
                lineno=lineno,
                kind="borrowed-helper",
                detail=(
                    f"imports the {_TESTS_PACKAGE} package. The harness "
                    f"measures the product, so a helper that supplies what it "
                    f"forgot is the defect under measurement."
                ),
            )


def _call_hits(rel: str, node: ast.Call, *, gated: frozenset[str]) -> Iterator[_Hit]:
    """Yield every violation one call site carries.

    Yields:
        Each violation attributable to *node*.
    """
    name = _call_name(node)
    if name is None:
        return
    if name in gated and any(keyword.arg is None for keyword in node.keywords):
        yield _Hit(
            rel=rel,
            lineno=node.lineno,
            kind="splat",
            detail=(
                f"{name}(**mapping) type-checks against nothing, so the whole "
                f"field list is bypassed. Name the fields."
            ),
        )
    if name == _ENGINE_CLASS and (
        len(node.args) != 1 or node.keywords or isinstance(node.args[0], ast.Starred)
    ):
        yield _Hit(
            rel=rel,
            lineno=node.lineno,
            kind="engine-call",
            detail=(
                f"{_ENGINE_CLASS}(...) takes exactly one positional "
                f"{_ROOT_TYPE}. Its wiring arrives as one declared object or "
                f"it does not arrive at all."
            ),
        )


def _scan_all(project_root: Path, *, gated: frozenset[str]) -> tuple[list[_Hit], bool]:
    """Scan every root, returning the hits and whether the exemption is used.

    Returns:
        ``(hits, sanctioned_builds)``.

    Raises:
        GateSourceError: If a source file cannot be read or parsed.
    """
    hits: list[_Hit] = []
    sanctioned_builds = False
    for root in _SCAN_ROOTS:
        abs_root = project_root / root
        if not abs_root.is_dir():
            continue
        instrument = root in _INSTRUMENT_ROOTS
        for path, rel in _git_tracked_python_files(abs_root, project_root):
            if not path.is_file():
                continue
            _, tree = read_and_parse(path)
            if rel == _SANCTIONED_REL:
                sanctioned_builds = any(
                    _call_name(node) == _ROOT_TYPE
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                )
            hits.extend(_scan_file(rel, tree, gated=gated, instrument=instrument))
    return hits, sanctioned_builds


def _report(messages: list[str]) -> None:
    """Print each configuration fault on stderr."""
    for message in messages:
        print(f"check_engine_dependencies_total: {message}", file=sys.stderr)


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
        anchors = _collect_declarations(project_root)
        faults = [*anchors.faults]
        if not faults:
            faults.extend(_engine_faults(project_root))
        if faults:
            _report(faults)
            return 2
        hits, sanctioned_builds = _scan_all(project_root, gated=anchors.gated)
    except GateSourceError as exc:
        print(f"check_engine_dependencies_total: {exc}", file=sys.stderr)
        return 2

    if not sanctioned_builds:
        _report(
            [
                (
                    f"{_SANCTIONED_REL}: declared as the one place defaults may "
                    f"be supplied ({_SANCTIONED_REASON}) and it builds no "
                    f"{_ROOT_TYPE}. Point the declaration at the module that "
                    f"took it over: an unused exemption is one the next "
                    f"builder inherits silently."
                )
            ]
        )
        return 2

    hits = [*anchors.default_hits, *hits]
    if not hits:
        return 0
    for hit in sorted(hits, key=lambda h: (h.rel, h.lineno, h.kind)):
        print(hit.message())
    print(
        f"\n{len(hits)} site(s) lose the engine-wiring contract. A partially "
        f"wired engine must not be constructable: every field of every bundle "
        f"is named at every call site, and absence is written down.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
