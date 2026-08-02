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
wrapper to the wiring function it calls. A read inside a helper that wiring
function then calls still counts as live, which is the boundary a call graph
would move and a whole-module rule would overshoot (those modules also hold
per-request helpers).
"""

import ast
import itertools
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import read_and_parse  # type: ignore[import-not-found]
    from _setting_reachability_helpers import (  # type: ignore[import-not-found]
        KEY_KWARGS,
        NAMESPACE_KWARGS,
        NAMESPACE_READS,
        HelperCollector,
    )
    from _setting_reachability_literals import (  # type: ignore[import-not-found]
        called_name,
        name_bindings,
        resolve_literal,
        walk_with_scopes,
    )
else:
    from scripts._gate_source import read_and_parse
    from scripts._setting_reachability_helpers import (
        KEY_KWARGS,
        NAMESPACE_KWARGS,
        NAMESPACE_READS,
        HelperCollector,
    )
    from scripts._setting_reachability_literals import (
        called_name,
        name_bindings,
        resolve_literal,
        walk_with_scopes,
    )

_SRC_REL: Final[str] = "src/synthorg"
_DEFINITIONS_REL: Final[str] = "src/synthorg/settings/definitions"
_REGISTRY_REL: Final[str] = "src/synthorg/api/subsystems/registry.py"
_WEB_REL: Final[str] = "web/src"
_WEB_SUFFIXES: Final[tuple[str, ...]] = (".ts", ".tsx")
_WEB_TEST_SUFFIXES: Final[tuple[str, ...]] = (".test.ts", ".test.tsx")
_WEB_TEST_DIR: Final[str] = "__tests__"
_WEB_TOKEN: Final[re.Pattern[str]] = re.compile(r"['\"`]([A-Za-z0-9_]+)['\"`]")

# Everything in these two modules runs inside ``build_runtime_services``.
_RUNTIME_BUILD_RELS: Final[frozenset[str]] = frozenset(
    {
        "src/synthorg/workers/_engine_assembly.py",
        "src/synthorg/workers/_openhands_wiring.py",
    }
)
_BRIDGE_BUILDER: Final[str] = "_resolve_bridge_fields"
# The bridge builder takes the namespace and the field specs, and nothing else.
_BRIDGE_ARITY: Final[int] = 2
_SPEC_CALL: Final[str] = "SubsystemSpec"
_SPEC_SETTING_KWARGS: Final[tuple[str, ...]] = ("settings", "enabled_by")


@dataclass(frozen=True)
class Evidence:
    """Where each setting pair was seen."""

    live: frozenset[tuple[str, str]]
    construction: frozenset[tuple[str, str]]


def collect_evidence(repo_root: Path, pairs: frozenset[tuple[str, str]]) -> Evidence:
    """Scan the source tree for every seam that reaches *pairs*.

    Args:
        repo_root: Project root to scan.
        pairs: The registered ``(namespace, key)`` pairs to look for.

    Returns:
        The pairs seen live and the pairs seen only while the runtime is built.

    Raises:
        GateSourceError: If a source file cannot be read or parsed.
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
        collector=HelperCollector(),
    )
    for path, rel in _source_modules(repo_root):
        _scan_module(path, rel, scan)
    scan.resolve_deferred()
    scan.live.update(_web_referenced(repo_root, pairs))
    return Evidence(
        live=frozenset(scan.live), construction=frozenset(scan.construction)
    )


@dataclass(frozen=True)
class _Scan:
    """The inputs and accumulators one pass over the tree shares."""

    pairs: frozenset[tuple[str, str]]
    targets: dict[str, frozenset[str]]
    addressable: frozenset[str]
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
    scan of 3,700 modules. Recording the little a call needs (its name, the
    literals it passes, and whether it sits on the construction path) defers
    the decision without a second parse.
    """

    name: str
    rel: str
    attribute_call: bool
    positional: tuple[frozenset[str], ...]
    keywords: tuple[tuple[str, frozenset[str]], ...]
    is_construction: bool

    def argument(self, index: int | None, parameter: str) -> frozenset[str]:
        """Return the literals this call binds to a parameter."""
        for name, values in self.keywords:
            if name == parameter:
                return values
        if index is not None and 0 <= index < len(self.positional):
            return self.positional[index]
        return frozenset()


def _source_modules(repo_root: Path) -> list[tuple[Path, str]]:
    """Return every module the evidence scan covers, definitions excluded.

    Args:
        repo_root: Project root to scan.

    Returns:
        ``(path, repository-relative path)`` pairs in deterministic order.
    """
    return [
        (path, rel)
        for path in sorted((repo_root / _SRC_REL).rglob("*.py"))
        if not (rel := path.relative_to(repo_root).as_posix()).startswith(
            f"{_DEFINITIONS_REL}/"
        )
    ]


def _scan_module(path: Path, rel: str, scan: _Scan) -> None:
    """Route one module's evidence into the live or construction set."""
    _, tree = read_and_parse(path)
    aliases, iterated = name_bindings(tree)
    names = _ModuleNames(aliases=aliases, iterated=iterated)
    whole_file_is_construction = rel in _RUNTIME_BUILD_RELS
    activations = scan.targets.get(rel, frozenset())
    for node, scope in walk_with_scopes(tree):
        is_construction = whole_file_is_construction or bool(
            activations.intersection(func.name for func in scope)
        )
        sink = scan.sink(is_construction=is_construction)
        sink.update(_node_evidence(node, names, scan.pairs))
        if not isinstance(node, ast.Call):
            continue
        if scope:
            scan.collector.observe(node, rel, scope[-1], aliases)
        recorded = _record_call(
            node, rel, names, scan.addressable, is_construction=is_construction
        )
        if recorded is not None:
            scan.deferred.append(recorded)


def _record_call(
    call: ast.Call,
    rel: str,
    names: _ModuleNames,
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
    keywords = tuple(
        (kw.arg, names.resolve(kw.value) & addressable)
        for kw in call.keywords
        if kw.arg
    )
    if not any(positional) and not any(values for _, values in keywords):
        return None
    return _DeferredCall(
        name=name,
        rel=rel,
        attribute_call=isinstance(call.func, ast.Attribute),
        positional=positional,
        keywords=keywords,
        is_construction=is_construction,
    )


@dataclass(frozen=True)
class _ModuleNames:
    """What the names in one module resolve to."""

    aliases: dict[str, str]
    iterated: dict[str, frozenset[str]]

    def resolve(self, node: ast.expr | None) -> frozenset[str]:
        """Return every string *node* can denote in this module.

        Args:
            node: The expression to resolve, or ``None``.

        Returns:
            One value for a literal or single binding, several for a loop
            variable bound across a literal collection, none when unresolvable.
        """
        single = resolve_literal(node, self.aliases)
        if single is not None:
            return frozenset({single})
        if isinstance(node, ast.Name):
            return self.iterated.get(node.id, frozenset())
        return frozenset()


def _node_evidence(
    node: ast.AST,
    names: _ModuleNames,
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
    for first, second in itertools.pairwise(_adjacent_sequence(node)):
        found.update(_matching(names.resolve(first), names.resolve(second), pairs))
    if isinstance(node, ast.Call):
        found.update(_call_evidence(node, names, pairs))
    return found


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
    names: _ModuleNames,
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
    names: _ModuleNames,
) -> frozenset[str]:
    """Resolve the first present keyword among *candidates*."""
    for candidate in candidates:
        resolved = names.resolve(keywords.get(candidate))
        if resolved:
            return resolved
    return frozenset()


def _declared_settings(
    keywords: dict[str, ast.expr],
    names: _ModuleNames,
    pairs: frozenset[tuple[str, str]],
) -> Iterator[tuple[str, str]]:
    """Yield the pairs a ``SubsystemSpec`` declares.

    A declared key puts the subsystem in the reconciler's watched set, so a
    write to it triggers the pass that re-runs activation. That is what makes a
    read inside the activation live rather than construction-only.
    """
    for name in _SPEC_SETTING_KWARGS:
        node = keywords.get(name)
        elements = node.elts if isinstance(node, ast.Tuple | ast.List) else [node]
        for element in elements:
            for entry in names.resolve(element):
                namespace, _, key = entry.partition(".")
                if (namespace, key) in pairs:
                    yield (namespace, key)


def _bridge_fields(
    call: ast.Call,
    names: _ModuleNames,
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
        GateSourceError: If the registry cannot be read or parsed. A missing
            registry would leave every activation read looking live.
    """
    _, tree = read_and_parse(repo_root / _REGISTRY_REL)
    wrappers = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    targets: dict[str, set[str]] = {}
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and _is_spec_call(call)):
            continue
        for kw in call.keywords:
            if kw.arg not in {"activate", "deactivate"}:
                continue
            wrapper = (
                wrappers.get(kw.value.id) if isinstance(kw.value, ast.Name) else None
            )
            if wrapper is None:
                continue
            for rel, name in _imported_targets(wrapper):
                targets.setdefault(rel, set()).add(name)
    return {rel: frozenset(names) for rel, names in targets.items()}


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
    """Return the pairs the dashboard names as a quoted string.

    The dashboard persists no domain state and re-fetches through
    ``GET /settings``, so a key it reads applies on the next render with no
    restart. Test files are excluded: naming a key in a test proves the store
    parses it, not that anything reads it.

    Args:
        repo_root: Project root to scan.
        pairs: The registered pairs to match against.

    Returns:
        Every pair whose key appears as a complete quoted token.
    """
    tokens: set[str] = set()
    for path in sorted((repo_root / _WEB_REL).rglob("*")):
        if not path.is_file() or path.suffix not in _WEB_SUFFIXES:
            continue
        name = path.name
        if name.endswith(_WEB_TEST_SUFFIXES) or _WEB_TEST_DIR in path.parts:
            continue
        tokens.update(
            _WEB_TOKEN.findall(path.read_text(encoding="utf-8", errors="replace"))
        )
    return {pair for pair in pairs if pair[1] in tokens}
