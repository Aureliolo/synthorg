"""Settings reads a helper performs on its caller's behalf.

Several live seams put the read one call away from the value that names it. The
Chief-of-Staff capability gate takes the key as a parameter and pairs it with a
namespace it holds itself; the meta config overlay's namespace reader
(``_read_namespace``) takes the namespace as a parameter and reads the whole
namespace through it. In both cases the read is
live and the caller supplies the missing half as a literal, so a scan that only
looked at the read site would call the setting unreachable.

This module resolves that one hop: it indexes the functions that forward a
parameter into a settings read, and the caller-side pass then credits whatever
literal each call site binds. One hop, deliberately: a chain of forwarders is a
call graph, and the gate says so rather than pretending to follow it.
"""

import ast
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _setting_reachability_literals import (  # type: ignore[import-not-found]
        called_name,
        parameter_names,
        positional_index,
        receives_instance,
        resolve_literal,
    )
else:
    from scripts._setting_reachability_literals import (
        called_name,
        parameter_names,
        positional_index,
        receives_instance,
        resolve_literal,
    )

# A positional settings read is ``(namespace, key)`` and nothing else.
_READ_ARITY: Final[int] = 2
NAMESPACE_KWARGS: Final[tuple[str, ...]] = ("namespace", "ns")
KEY_KWARGS: Final[tuple[str, ...]] = ("key", "setting_key")
NAMESPACE_READS: Final[frozenset[str]] = frozenset(
    {"get_namespace", "get_page", "get_all"}
)
# The reads that name one setting by ``(namespace, key)``. Pinning the callee is
# what keeps a helper index out of calls that merely happen to take a string and
# a parameter, such as a log line tagged with a namespace, which would otherwise
# mint evidence that a setting is read live when nothing reads it at all.
#
# These names are unambiguous: nothing else in the tree defines them.
SCALAR_READS: Final[frozenset[str]] = frozenset(
    {
        "get_entry",
        "get_versioned",
        "get_str",
        "get_int",
        "get_float",
        "get_bool",
        "get_enum",
        "get_json",
    }
)
# ``SettingsService.get`` is deliberately absent. Its name is the one every
# mapping also carries, and a mapping's ``get(key, default)`` means something
# else entirely, so ``data.get("engine", fallback)`` reads as the pair
# ("engine", fallback). Nothing available to an AST scan tells the two apart:
# a receiver-name test credits ``settings_payload.get(...)`` on the strength of
# a substring, and proving the receiver is a settings object needs type
# inference this gate does not have. Omitting it can only cost evidence, which
# surfaces as a violation to look at; including it invents evidence, which is
# the failure that makes a gate worse than absent.


@dataclass(frozen=True)
class ForwardingHelper:
    """A function that reads a setting named partly by its caller."""

    parameter: str
    index: int | None
    """Positional index in the signature, for a plain-name call site."""
    receives_instance: bool
    """Whether the signature opens with ``self`` / ``cls``, which an attribute
    call supplies through the attribute rather than as an argument."""
    namespace: str | None
    """The namespace the helper holds itself; ``None`` when the caller names it
    and the helper reads the namespace whole."""

    def index_for(self, *, attribute_call: bool) -> int | None:
        """Return the positional index to read the caller's argument from.

        Derived here rather than stored per call shape, so the one fact the
        signature carries cannot disagree with itself.
        """
        if self.index is None:
            return None
        offset = 1 if (attribute_call and self.receives_instance) else 0
        return self.index - offset


@dataclass(frozen=True)
class HelperIndex:
    """The forwarding helpers a call site can be resolved against."""

    _per_module: Mapping[str, Mapping[str, ForwardingHelper]]
    _shared: Mapping[str, ForwardingHelper]

    def lookup(self, rel: str, name: str | None) -> ForwardingHelper | None:
        """Return the helper *name* refers to from inside *rel*.

        The module's own declaration wins. Two classes each declaring a private
        ``_resolve_int`` over a different namespace are unambiguous from inside
        either module and hopeless from outside, which is exactly what this
        ordering expresses.

        Args:
            rel: Repository-relative path of the calling module.
            name: The called function's bare name.

        Returns:
            The helper, or ``None`` when the name resolves to none or to
            several incompatible ones.
        """
        if name is None:
            return None
        local = self._per_module.get(rel, {})
        return local.get(name) or self._shared.get(name)


class HelperCollector:
    """Accumulates helper declarations as the single tree walk finds them.

    The evidence walk cannot resolve a call to a forwarding helper until every
    module has been read, and re-reading the tree to build the index first
    doubles the scan. So the walk feeds declarations in here as it goes and
    asks for the finished index once, at the end.
    """

    def __init__(self) -> None:
        self._per_module: dict[str, dict[str, set[ForwardingHelper]]] = {}
        self._dropped: set[str] = set()
        self._index: HelperIndex | None = None

    def observe(
        self,
        call: ast.Call,
        rel: str,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        aliases: Mapping[str, str],
    ) -> None:
        """Record the forwarding reads *call* performs on *func*'s behalf.

        Args:
            call: A call inside the candidate helper.
            rel: Repository-relative path of the declaring module.
            func: The innermost function enclosing the call.
            aliases: The declaring module's name bindings.
        """
        for helper in _helpers_from(call, func, aliases):
            self._per_module.setdefault(rel, {}).setdefault(func.name, set()).add(
                helper
            )

    def index(self) -> HelperIndex:
        """Return the finished index.

        Returns:
            The index, keyed per declaring module and, for names declared
            identically everywhere, shared across the tree. A name declared
            with two different readings is dropped from the shared map:
            crediting one of them would attribute a caller's literal to a
            namespace it never touches.
        """
        if self._index is not None:
            return self._index
        shared: dict[str, set[ForwardingHelper]] = {}
        for declarations in self._per_module.values():
            for name, helpers in declarations.items():
                shared.setdefault(name, set()).update(helpers)
        self._index = HelperIndex(
            _per_module={
                rel: _unambiguous(declarations)
                for rel, declarations in self._per_module.items()
            },
            _shared=_unambiguous(shared, report=self._dropped),
        )
        return self._index

    def dropped_names(self) -> tuple[str, ...]:
        """Return the helper names ambiguity removed from the shared index.

        A setting whose only evidence runs through such a helper is reported
        unreachable, and without this the developer has no way to see that a
        name collision, not their wiring, is what the gate objected to.
        """
        self.index()
        return tuple(sorted(self._dropped))


def _unambiguous(
    declarations: dict[str, set[ForwardingHelper]],
    report: set[str] | None = None,
) -> dict[str, ForwardingHelper]:
    """Keep only the names that resolve to exactly one reading."""
    if report is not None:
        report.update(
            name for name, helpers in declarations.items() if len(helpers) != 1
        )
    return {
        name: next(iter(helpers))
        for name, helpers in declarations.items()
        if len(helpers) == 1
    }


def _helpers_from(
    call: ast.Call,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: Mapping[str, str],
) -> Iterator[ForwardingHelper]:
    """Yield the forwarding reads *call* performs on *func*'s behalf.

    Args:
        call: A call inside the candidate helper.
        func: The innermost function enclosing the call.
        aliases: Module-level name bindings.

    Yields:
        One record per parameter the function forwards into a settings read.
    """
    parameters = parameter_names(func)
    yield from _key_forward(call, func, parameters, aliases)
    yield from _namespace_forward(call, func, parameters)


def _key_forward(
    call: ast.Call,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    parameters: frozenset[str],
    aliases: Mapping[str, str],
) -> Iterator[ForwardingHelper]:
    """Yield a helper record when *call* reads ``(known namespace, parameter)``."""
    if called_name(call) not in SCALAR_READS:
        return
    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
    namespace = _first_resolved(keywords, NAMESPACE_KWARGS, aliases)
    key_node = next(
        (keywords[name] for name in KEY_KWARGS if name in keywords),
        None,
    )
    # Positional arity is a floor, not an equality: every pinned read opens with
    # ``(namespace, key)`` and ``get_enum`` then takes the enum class, so
    # demanding exactly two would drop the one getter that carries a third.
    if namespace is None and len(call.args) >= _READ_ARITY:
        namespace = resolve_literal(call.args[0], aliases)
        key_node = call.args[1]
    if namespace is None or not isinstance(key_node, ast.Name):
        return
    if key_node.id not in parameters:
        return
    yield _record(func, key_node.id, namespace)


def _namespace_forward(
    call: ast.Call,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    parameters: frozenset[str],
) -> Iterator[ForwardingHelper]:
    """Yield a helper record when *call* reads a whole namespace by parameter."""
    if (
        not isinstance(call.func, ast.Attribute)
        or call.func.attr not in NAMESPACE_READS
    ):
        return
    if not call.args or not isinstance(call.args[0], ast.Name):
        return
    name = call.args[0].id
    if name not in parameters:
        return
    yield _record(func, name, None)


def _record(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    parameter: str,
    namespace: str | None,
) -> ForwardingHelper:
    """Build the helper record for one forwarded parameter."""
    return ForwardingHelper(
        parameter=parameter,
        index=positional_index(func, parameter),
        receives_instance=receives_instance(func),
        namespace=namespace,
    )


def _first_resolved(
    keywords: dict[str, ast.expr],
    names: tuple[str, ...],
    aliases: Mapping[str, str],
) -> str | None:
    """Resolve the first present keyword among *names*."""
    for name in names:
        resolved: str | None = resolve_literal(keywords.get(name), aliases)
        if resolved is not None:
            return resolved
    return None
