"""Where a settings write can and cannot reach, read from the source tree.

Evidence that a ``(namespace, key)`` pair reaches something running takes a
handful of shapes in this tree, and this module recognises all of them: a
subscriber's watched pair, a subsystem's declared setting, a resolver read
(positional, keyword, or bundled through the bridge-config builder), a
namespace-wide read, and a dotted ``"ns.key"`` literal. The Pydantic mirror
declarations fall out of the keyword shape, since ``MirrorField`` names its
``namespace=`` and ``key=`` the same way the keyword-only resolver helpers do.

The same shapes found on the construction path prove the opposite. A read
inside ``build_runtime_services`` or inside a subsystem activation runs once,
when the object graph is assembled, so a write to the key it reads changes
nothing until something rebuilds. Those reads are collected separately.

The activation analysis follows one import hop: the registry's ``_activate_*``
wrapper to the wiring function it calls, and excludes reads lexically inside
that function. It matches on the function name rather than tracing calls, so a
read in a helper the wiring function calls is not excluded and stays live. A
call graph would move that boundary; a whole-module rule would overshoot, since
those modules also hold per-request helpers.

The runtime-build path is derived, not listed: every ``synthorg.workers``
module reachable from the one defining ``build_runtime_services`` is assembly
code, so the set grows with the tree instead of going stale against it.
"""

import ast
import itertools
import re
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
        read_source,
    )
    from _setting_reachability_helpers import (  # type: ignore[import-not-found]
        KEY_KWARGS,
        NAMESPACE_KWARGS,
        NAMESPACE_READS,
        HelperCollector,
    )
    from _setting_reachability_literals import (  # type: ignore[import-not-found]
        ModuleBindings,
        bindings_from_nodes,
        called_name,
        walk_with_scopes,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse, read_source
    from scripts._setting_reachability_helpers import (
        KEY_KWARGS,
        NAMESPACE_KWARGS,
        NAMESPACE_READS,
        HelperCollector,
    )
    from scripts._setting_reachability_literals import (
        ModuleBindings,
        bindings_from_nodes,
        called_name,
        walk_with_scopes,
    )

_SRC_REL: Final[str] = "src/synthorg"
_DEFINITIONS_REL: Final[str] = "src/synthorg/settings/definitions"
_REGISTRY_REL: Final[str] = "src/synthorg/api/subsystems/registry.py"
_WEB_REL: Final[str] = "web/src"
_WEB_SUFFIXES: Final[tuple[str, ...]] = (".ts", ".tsx")
_WEB_TEST_SUFFIXES: Final[tuple[str, ...]] = (".test.ts", ".test.tsx")
_WEB_TEST_DIR: Final[str] = "__tests__"
# Generated API types spell every enum member and field name in the schema, so
# they name a key without anything reading it.
_WEB_GENERATED_SUFFIX: Final[str] = ".gen.ts"
_WEB_TOKEN: Final[re.Pattern[str]] = re.compile(r"['\"`]([A-Za-z0-9_]+)['\"`]")

_RUNTIME_BUILD_REL: Final[str] = "src/synthorg/workers/runtime_builder.py"
_WORKERS_PACKAGE: Final[str] = "synthorg.workers"
_PACKAGE_INIT: Final[str] = "__init__.py"
_BRIDGE_BUILDER: Final[str] = "_resolve_bridge_fields"
# The bridge builder takes the namespace and the field specs, and nothing else.
_BRIDGE_ARITY: Final[int] = 2
# A declared setting pair is a 2-tuple, and a resolver read puts its namespace
# and key inside the first three positional arguments.
_PAIR_ARITY: Final[int] = 2
_READ_WINDOW: Final[int] = 3
_SPEC_CALL: Final[str] = "SubsystemSpec"
_SPEC_REBUILD_KWARG: Final[str] = "rebuild_on_change"
_SPEC_SETTING_KWARGS: Final[frozenset[str]] = frozenset({"settings", "enabled_by"})
_ACTIVATION_KWARGS: Final[frozenset[str]] = frozenset({"activate", "deactivate"})

type Reach = Literal["live", "construction"]

# Public: the gate classifies each violation by comparing against these. Two
# independent spellings of the same value would let a change here silently
# reclassify every setting instead of failing at import. Annotated rather than
# ``Final`` so the gate's dual-import shim can bind them in both branches.
LIVE: Reach = "live"
CONSTRUCTION: Reach = "construction"


@dataclass(frozen=True)
class Evidence:
    """Where each setting pair was seen."""

    live: frozenset[tuple[str, str]]
    construction: frozenset[tuple[str, str]]

    def status(self, pair: tuple[str, str]) -> Reach | None:
        """Return how *pair* is reached, or ``None`` when nothing reaches it.

        One live read settles it however many construction-path reads sit
        beside it, so the precedence lives here rather than in each caller.
        """
        if pair in self.live:
            return LIVE
        return CONSTRUCTION if pair in self.construction else None


def collect_evidence(repo_root: Path, pairs: frozenset[tuple[str, str]]) -> Evidence:
    """Scan the source tree for every seam that reaches *pairs*.

    Args:
        repo_root: Project root to scan.
        pairs: The registered ``(namespace, key)`` pairs to look for.

    Returns:
        The pairs seen live and the pairs seen only while the runtime is built.

    Raises:
        GateSourceError: If a source file cannot be read or parsed, or if a
            tree the scan depends on resolves to nothing.
    """
    targets = activation_targets(repo_root)
    # A forwarding helper can only be completed by a literal that names a
    # registered setting, so a call passing any other string is not worth
    # recording. Filtering here rather than at resolution time is what keeps
    # the deferred list to the handful of call sites that can matter instead
    # of every call in the tree that passes any string at all.
    addressable = frozenset(namespace for namespace, _ in pairs) | frozenset(
        key for _, key in pairs
    )
    scan = _Scan(
        pairs=pairs,
        targets=targets,
        addressable=addressable,
        build_rels=construction_modules(repo_root),
        collector=HelperCollector(),
    )
    for path, rel in _source_modules(repo_root):
        _scan_module(path, rel, scan)
    scan.resolve_deferred()
    scan.live.update(_web_referenced(repo_root, pairs))
    return Evidence(
        live=frozenset(scan.live), construction=frozenset(scan.construction)
    )


@dataclass
class _Scan:
    """The inputs and accumulators one pass over the tree shares.

    Deliberately not frozen: the three collections below are filled in place as
    the walk proceeds, and freezing would claim an immutability the accumulator
    does not have while silently making the type unhashable.
    """

    pairs: frozenset[tuple[str, str]]
    targets: dict[str, frozenset[str]]
    addressable: frozenset[str]
    build_rels: frozenset[str]
    collector: HelperCollector
    live: set[tuple[str, str]] = field(default_factory=set)
    construction: set[tuple[str, str]] = field(default_factory=set)
    deferred: list[_DeferredCall] = field(default_factory=list)

    def sink(self, *, is_construction: bool) -> set[tuple[str, str]]:
        """Return the set evidence found at this position belongs in."""
        return self.construction if is_construction else self.live

    def resolve_deferred(self) -> None:
        """Credit the recorded call sites against the finished helper index."""
        helpers = self.collector.index()
        for record in self.deferred:
            helper = helpers.lookup(record.rel, record.name)
            if helper is None:
                continue
            resolved = record.argument(
                helper.index_for(attribute_call=record.attribute_call),
                helper.parameter,
            )
            if not resolved:
                continue
            sink = self.sink(is_construction=record.is_construction)
            if helper.namespace is None:
                sink.update(pair for pair in self.pairs if pair[0] in resolved)
            else:
                sink.update(
                    _matching(frozenset({helper.namespace}), resolved, self.pairs)
                )


@dataclass(frozen=True)
class _DeferredCall:
    """A call site that may reach a setting through a forwarding helper.

    A call cannot be resolved against the helper index while the walk is still
    building it, and re-reading the tree to build the index first doubles the
    scan of every module in the tree. Recording the little a call needs (its
    name, the
    literals it passes, and whether it sits on the construction path) defers
    the decision without a second parse.
    """

    name: str
    rel: str
    attribute_call: bool
    positional: tuple[frozenset[str], ...]
    keywords: Mapping[str, frozenset[str]]
    is_construction: bool

    def argument(self, index: int | None, parameter: str) -> frozenset[str]:
        """Return the literals this call binds to a parameter."""
        bound = self.keywords.get(parameter)
        if bound is not None:
            return bound
        if index is not None and 0 <= index < len(self.positional):
            return self.positional[index]
        return frozenset()


def _source_modules(repo_root: Path) -> list[tuple[Path, str]]:
    """Return every module the evidence scan covers, definitions excluded.

    Args:
        repo_root: Project root to scan.

    Returns:
        ``(path, repository-relative path)`` pairs in deterministic order.

    Raises:
        GateSourceError: If the source tree resolves to nothing. Every setting
            would then look unreachable, blaming the settings for a tree that
            moved.
    """
    modules = [
        (path, rel)
        for path in sorted((repo_root / _SRC_REL).rglob("*.py"))
        if not (rel := path.relative_to(repo_root).as_posix()).startswith(
            f"{_DEFINITIONS_REL}/"
        )
    ]
    if not modules:
        message = f"{_SRC_REL}: no modules found to scan for evidence"
        raise GateSourceError(message)
    return modules


def _scan_module(path: Path, rel: str, scan: _Scan) -> None:
    """Route one module's evidence into the live or construction set."""
    _, tree = read_and_parse(path)
    # Materialised so name resolution reads the same walk the evidence pass
    # uses, rather than traversing every module in the tree a second time.
    walked = list(walk_with_scopes(tree))
    names = bindings_from_nodes(node for node, _ in walked)
    # A spec's own declaration strings are dotted "ns.key" literals, which the
    # generic literal rule would credit as live whatever the spec says. Only
    # _declared_settings knows what they mean, so it decides alone.
    declared = _spec_declared_nodes(walked)
    whole_file_is_construction = rel in scan.build_rels
    activations = scan.targets.get(rel, frozenset())
    for node, scope in walked:
        if id(node) in declared:
            continue
        # ``activations`` is empty for nearly every module, so the membership
        # walk over the enclosing scope is worth skipping rather than running
        # it once per node across the tree.
        is_construction = whole_file_is_construction or (
            bool(activations) and any(func.name in activations for func in scope)
        )
        sink = scan.sink(is_construction=is_construction)
        sink.update(_node_evidence(node, names, scan.pairs))
        if not isinstance(node, ast.Call):
            continue
        if scope:
            scan.collector.observe(node, rel, scope[-1], names.aliases)
        recorded = _record_call(
            node, rel, names, scan.addressable, is_construction=is_construction
        )
        if recorded is not None:
            scan.deferred.append(recorded)


def _spec_declared_nodes(
    walked: list[tuple[ast.AST, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]]],
) -> frozenset[int]:
    """Return the node identities inside a ``SubsystemSpec``'s declarations.

    Args:
        walked: The module's nodes, already traversed.

    Returns:
        Identities to skip in the generic literal scan, empty for the modules
        (all but one) that construct no spec.
    """
    declared: set[int] = set()
    for node, _ in walked:
        if not (isinstance(node, ast.Call) and _is_spec_call(node)):
            continue
        for kw in node.keywords:
            if kw.arg in _SPEC_SETTING_KWARGS:
                declared.update(id(child) for child in ast.walk(kw.value))
    return frozenset(declared)


def _record_call(
    call: ast.Call,
    rel: str,
    names: ModuleBindings,
    addressable: frozenset[str],
    *,
    is_construction: bool,
) -> _DeferredCall | None:
    """Return what a forwarding helper would need from this call site.

    Args:
        call: The call to record.
        rel: Repository-relative path of the calling module.
        names: What this module's names resolve to.
        addressable: The strings that name a registered setting.
        is_construction: Whether the call sits on the construction path.

    Returns:
        The record, or ``None`` when the call names no registered setting and
        so can complete no helper's read.
    """
    name = called_name(call)
    if name is None:
        return None
    positional = tuple(names.resolve(arg) & addressable for arg in call.args)
    keywords = {
        kw.arg: names.resolve(kw.value) & addressable for kw in call.keywords if kw.arg
    }
    if not any(positional) and not any(keywords.values()):
        return None
    return _DeferredCall(
        name=name,
        rel=rel,
        attribute_call=isinstance(call.func, ast.Attribute),
        positional=positional,
        keywords=keywords,
        is_construction=is_construction,
    )


def _node_evidence(
    node: ast.AST,
    names: ModuleBindings,
    pairs: frozenset[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Return the setting pairs one node names.

    Args:
        node: The node to inspect.
        names: What this module's names resolve to.
        pairs: The registered pairs to match against.

    Returns:
        Every registered pair the node addresses.
    """
    found: set[tuple[str, str]] = set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        namespace, _, key = node.value.partition(".")
        if (namespace, key) in pairs:
            found.add((namespace, key))
        return found
    for first, second in itertools.pairwise(_pairable_sequence(node)):
        found.update(names.resolve_jointly(first, second) & pairs)
    if isinstance(node, ast.Call):
        found.update(_call_evidence(node, names, pairs))
    return found


def _pairable_sequence(node: ast.AST) -> list[ast.expr]:
    """Return the expressions worth sliding a namespace / key window over.

    Bounded on both shapes, because an unbounded window over an arbitrary
    sequence credits any two neighbours that happen to spell a registered pair,
    and namespaces are ordinary words. A declared pair is a 2-tuple, and a read
    puts the pair in its first three arguments (the resolver takes them first,
    a feature gate takes the app state first). Both bounds were measured to
    cost no real evidence on this tree.
    """
    if isinstance(node, ast.Call):
        return list(node.args[:_READ_WINDOW])
    if isinstance(node, ast.Tuple | ast.List):
        elements = list(node.elts)
        return elements if len(elements) == _PAIR_ARITY else []
    return []


def _matching(
    namespaces: frozenset[str],
    keys: frozenset[str],
    pairs: frozenset[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Return the registered pairs drawn from *namespaces* and *keys*."""
    return {
        (namespace, key)
        for namespace in namespaces
        for key in keys
        if (namespace, key) in pairs
    }


def _adjacent_sequence(node: ast.AST) -> list[ast.expr]:
    """Return the expressions a node lists in order.

    Returns:
        Positional call arguments, or tuple / list elements; empty for
        anything else.
    """
    if isinstance(node, ast.Call):
        return list(node.args)
    if isinstance(node, ast.Tuple | ast.List):
        return list(node.elts)
    return []


def _call_evidence(
    call: ast.Call,
    names: ModuleBindings,
    pairs: frozenset[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Return the setting pairs one call addresses through its keywords.

    Covers the keyword-only resolver helpers, the ``MirrorField``
    declarations, the bridge-config field bundles, the namespace-wide reads,
    and a subsystem's declared settings.

    Args:
        call: The call to inspect.
        names: What this module's names resolve to.
        pairs: The registered pairs to match against.

    Returns:
        Every registered pair the call addresses.
    """
    found: set[tuple[str, str]] = set()
    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
    found.update(
        _matching(
            _first_present(keywords, NAMESPACE_KWARGS, names),
            _first_present(keywords, KEY_KWARGS, names),
            pairs,
        )
    )
    if _is_spec_call(call):
        found.update(_declared_settings(keywords, names, pairs))
    attribute = call.func.attr if isinstance(call.func, ast.Attribute) else None
    if attribute == _BRIDGE_BUILDER and len(call.args) == _BRIDGE_ARITY:
        found.update(_bridge_fields(call, names, pairs))
    if attribute in NAMESPACE_READS and call.args:
        bulk = names.resolve(call.args[0])
        found.update(pair for pair in pairs if pair[0] in bulk)
    return found


def _is_spec_call(call: ast.Call) -> bool:
    """Whether *call* constructs a ``SubsystemSpec``."""
    return isinstance(call.func, ast.Name) and call.func.id == _SPEC_CALL


def _first_present(
    keywords: dict[str, ast.expr],
    candidates: tuple[str, ...],
    names: ModuleBindings,
) -> frozenset[str]:
    """Resolve the first present keyword among *candidates*."""
    for candidate in candidates:
        resolved: frozenset[str] = names.resolve(keywords.get(candidate))
        if resolved:
            return resolved
    return frozenset()


def _declared_settings(
    keywords: dict[str, ast.expr],
    names: ModuleBindings,
    pairs: frozenset[tuple[str, str]],
) -> Iterator[tuple[str, str]]:
    """Yield the pairs a ``SubsystemSpec`` declares as reaching a running one.

    ``enabled_by`` always counts: the reconciler evaluates it on every pass,
    before and independently of any rebuild, so a write flips the subsystem on
    or off.

    ``settings=`` counts only alongside ``rebuild_on_change=True``. Without it
    the reconciler short-circuits on an already-active subsystem
    (``reconciler.py``, the drift branch is gated on the flag), so a write is
    watched but replaces nothing: the value takes effect the next time the
    subsystem is wired from scratch, which is construction, not liveness.
    """
    rebuilds = keywords.get(_SPEC_REBUILD_KWARG)
    declares_rebuild = isinstance(rebuilds, ast.Constant) and rebuilds.value is True
    sources = ["enabled_by"] + (["settings"] if declares_rebuild else [])
    for name in sources:
        node = keywords.get(name)
        elements = node.elts if isinstance(node, ast.Tuple | ast.List) else [node]
        for element in elements:
            for entry in names.resolve(element):
                namespace, _, key = entry.partition(".")
                if (namespace, key) in pairs:
                    yield (namespace, key)


def _bridge_fields(
    call: ast.Call,
    names: ModuleBindings,
    pairs: frozenset[tuple[str, str]],
) -> Iterator[tuple[str, str]]:
    """Yield the pairs a ``_resolve_bridge_fields`` bundle resolves."""
    namespaces = names.resolve(call.args[0])
    for spec in _adjacent_sequence(call.args[1]):
        if not isinstance(spec, ast.Tuple) or not spec.elts:
            continue
        yield from _matching(namespaces, names.resolve(spec.elts[0]), pairs)


def activation_targets(repo_root: Path) -> dict[str, frozenset[str]]:
    """Resolve the wiring functions subsystem activation runs.

    Reads the registry, finds each spec's ``activate`` / ``deactivate``
    wrapper, and follows the wrapper's single local import to the real wiring
    function.

    Args:
        repo_root: Project root to scan.

    Returns:
        Repository-relative module path to the function names activation
        enters there.

    Raises:
        GateSourceError: If the registry cannot be read or parsed, declares no
            spec, or names an activation the scan cannot follow. Each would
            leave activation reads looking live, which is the direction that
            hides a violation rather than inventing one.
    """
    _, tree = read_and_parse(repo_root / _REGISTRY_REL)
    wrappers = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    specs = [
        call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and _is_spec_call(call)
    ]
    if not specs:
        message = (
            f"{_REGISTRY_REL}: no {_SPEC_CALL}(...) construction found, so no "
            "activation could be resolved"
        )
        raise GateSourceError(message)
    targets: dict[str, set[str]] = {}
    for call in specs:
        for kw in call.keywords:
            if kw.arg not in _ACTIVATION_KWARGS:
                continue
            if not isinstance(kw.value, ast.Name) or kw.value.id not in wrappers:
                message = (
                    f"{_REGISTRY_REL}:{call.lineno}: {kw.arg}= is not a bare "
                    "module-level function this scan can follow"
                )
                raise GateSourceError(message)
            for rel, name in _imported_targets(wrappers[kw.value.id]):
                targets.setdefault(rel, set()).add(name)
    return {rel: frozenset(names) for rel, names in targets.items()}


def construction_modules(repo_root: Path) -> frozenset[str]:
    """Return every module that only runs while the runtime is assembled.

    Derived by closing over the ``synthorg.workers`` imports of the module
    defining ``build_runtime_services``: assembly code reaches other assembly
    code, and a hand-listed pair of filenames goes stale the moment the
    assembly grows a module (it had gone stale by twelve).

    Args:
        repo_root: Project root to scan.

    Returns:
        Repository-relative paths whose every read is construction-path.

    Raises:
        GateSourceError: If a module in the closure cannot be read or parsed,
            or if the seed module is absent. An empty closure would put every
            assembly read in the live set, which passes the settings the gate
            exists to catch.
    """
    if not (repo_root / _RUNTIME_BUILD_REL).is_file():
        message = (
            f"{_RUNTIME_BUILD_REL}: module not found, so the construction path"
            " could not be derived"
        )
        raise GateSourceError(message)
    seen: set[str] = set()
    pending = [_RUNTIME_BUILD_REL]
    while pending:
        rel = pending.pop()
        if rel in seen:
            continue
        seen.add(rel)
        _, tree = read_and_parse(repo_root / rel)
        for node in ast.walk(tree):
            pending.extend(_imported_worker_modules(repo_root, node))
    return frozenset(seen)


def _imported_worker_modules(repo_root: Path, node: ast.AST) -> Iterator[str]:
    """Yield the worker module files *node* pulls into the construction path.

    Both statement forms count, because both execute the module: ``import
    synthorg.workers.x`` runs it exactly as ``from synthorg.workers.x import y``
    does, and a walk that only knew the second would skip the first in silence.

    Args:
        repo_root: Project root to resolve against.
        node: Any AST node; non-import nodes yield nothing.

    Yields:
        Repository-relative paths, one per worker module the import reaches.
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield from _package_chain(repo_root, alias.name)
    elif isinstance(node, ast.ImportFrom) and node.module == _WORKERS_PACKAGE:
        # ``from synthorg.workers import x`` binds either a submodule or a name
        # the initialiser re-exports. Both are resolved by existence rather than
        # demanded: a name backing no module is a symbol, and a package with no
        # initialiser is a namespace package. Neither is a broken scan, unlike
        # the dotted form below, where the name can only be a module.
        package = f"src/{_WORKERS_PACKAGE.replace('.', '/')}"
        stems = [package, *(f"{package}/{alias.name}" for alias in node.names)]
        for stem in stems:
            for candidate in (f"{stem}.py", f"{stem}/{_PACKAGE_INIT}"):
                if (repo_root / candidate).is_file():
                    yield candidate
                    break
    elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
        f"{_WORKERS_PACKAGE}."
    ):
        module = node.module or ""
        yield from _package_initialisers(repo_root, module)
        yield _module_file(repo_root, module)


def _package_chain(repo_root: Path, module: str) -> Iterator[str]:
    """Yield the files a plain ``import <module>`` executes, if it is a worker."""
    if module != _WORKERS_PACKAGE and not module.startswith(f"{_WORKERS_PACKAGE}."):
        return
    yield from _package_initialisers(repo_root, module)
    if module != _WORKERS_PACKAGE:
        yield _module_file(repo_root, module)


def _package_initialisers(repo_root: Path, module: str) -> Iterator[str]:
    """Yield every package initialiser importing *module* executes first.

    Python runs each parent package's ``__init__`` before the module itself, so
    a read there runs while the runtime is assembled just as surely as one in
    the module named by the import.
    """
    parts = module.split(".")
    for depth in range(1, len(parts) + 1):
        candidate = f"src/{'/'.join(parts[:depth])}/{_PACKAGE_INIT}"
        if (repo_root / candidate).is_file():
            yield candidate


def _module_file(repo_root: Path, module: str) -> str:
    """Return the repository-relative file backing dotted *module*.

    Args:
        repo_root: Project root to resolve against.
        module: Dotted module name inside the workers package.

    Returns:
        The path to the module file, or to the package initialiser when the
        name denotes a package.

    Raises:
        GateSourceError: If the name backs neither. Assembly code imports a
            package as readily as a module, and a name silently skipped for
            resolving to neither puts that whole subtree's reads back in the
            live set, which is the classification this closure exists to make.
    """
    stem = f"src/{module.replace('.', '/')}"
    for candidate in (f"{stem}.py", f"{stem}/{_PACKAGE_INIT}"):
        if (repo_root / candidate).is_file():
            return candidate
    message = (
        f"{module}: imported from the construction path but backs neither"
        f" {stem}.py nor {stem}/{_PACKAGE_INIT}"
    )
    raise GateSourceError(message)


def _imported_targets(
    wrapper: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterator[tuple[str, str]]:
    """Yield the ``(module path, function name)`` pairs *wrapper* imports."""
    for node in ast.walk(wrapper):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("synthorg."):
            continue
        rel = f"src/{node.module.replace('.', '/')}.py"
        for alias in node.names:
            yield rel, alias.name


def _web_referenced(
    repo_root: Path, pairs: frozenset[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Return the pairs the dashboard names as quoted strings.

    The dashboard persists no domain state and re-fetches through
    ``GET /settings``, so a key it reads applies on the next render with no
    restart.

    Both halves must appear in the SAME file, because a key alone proves
    nothing: eight settings are keyed ``enabled``, and one unrelated token
    would otherwise certify all eight. Generated API types are skipped for the
    same reason, since they spell every schema name whether or not the
    dashboard reads it. Test files are excluded too: naming a key in a test
    proves the store parses it, not that anything reads it.

    Args:
        repo_root: Project root to scan.
        pairs: The registered pairs to match against.

    Returns:
        Every pair whose namespace and key are both quoted in one file.

    Raises:
        GateSourceError: If a dashboard source file cannot be read.
    """
    found: set[tuple[str, str]] = set()
    for path in sorted((repo_root / _WEB_REL).rglob("*")):
        if not path.is_file() or path.suffix not in _WEB_SUFFIXES:
            continue
        name = path.name
        if name.endswith(_WEB_TEST_SUFFIXES) or _WEB_TEST_DIR in path.parts:
            continue
        if name.endswith(_WEB_GENERATED_SUFFIX):
            continue
        tokens = set(_WEB_TOKEN.findall(read_source(path)))
        found.update(pair for pair in pairs if pair[0] in tokens and pair[1] in tokens)
    return found
