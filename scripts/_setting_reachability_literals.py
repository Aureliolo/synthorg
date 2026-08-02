"""Static resolution of the namespace / key literals a settings scan reads.

Settings are addressed as ``(namespace, key)`` at every seam in the tree, but
the tree spells that pair four ways: plain strings, ``SettingNamespace.X``,
``SettingNamespace.X.value``, and module-level constants standing in for any of
those (``_NS``, ``_TOOLS_NS``, ``_CLASSIFIER_MODEL_KEY``). A scan that only
matched plain strings would miss a large, deliberate minority of the call
sites, so resolution lives here and both halves of the gate share it.

A name bound to two different values in one module resolves to nothing rather
than to one of them: the ambiguity would otherwise attribute a read to whichever
binding the walk happened to see last.
"""

import ast
from collections.abc import Iterator, Mapping
from typing import Final

_NAMESPACE_ENUM: Final[str] = "SettingNamespace"
_MAX_COLLECTION_DEPTH: Final[int] = 3
_COLLECTION_CALLS: Final[frozenset[str]] = frozenset(
    {"frozenset", "set", "tuple", "list"}
)


def resolve_literal(node: ast.expr | None, aliases: Mapping[str, str]) -> str | None:
    """Resolve *node* to the string it denotes.

    Args:
        node: The expression to resolve, or ``None``.
        aliases: Name-to-value bindings from :func:`module_aliases`.

    Returns:
        The string, or ``None`` when the expression is not statically a
        namespace or key.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Attribute):
        return _resolve_attribute(node)
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    return None


def _resolve_attribute(node: ast.Attribute) -> str | None:
    """Resolve ``SettingNamespace.X`` and ``SettingNamespace.X.value``.

    Args:
        node: The attribute expression.

    Returns:
        The lower-case namespace string, or ``None``.
    """
    if isinstance(node.value, ast.Name) and node.value.id == _NAMESPACE_ENUM:
        return node.attr.lower()
    if node.attr == "value" and isinstance(node.value, ast.Attribute):
        return _resolve_attribute(node.value)
    return None


def module_aliases(tree: ast.Module) -> dict[str, str]:
    """Collect the names *tree* binds to a resolvable string.

    Bindings anywhere in the module count, function bodies included: the
    charter and eval-loop wiring bind a local ``ns`` and then read several keys
    through it, and dropping those would lose the only evidence those settings
    have.

    Args:
        tree: The parsed module.

    Returns:
        Name-to-value bindings, excluding every name bound to more than one
        distinct value.
    """
    return _collect_bindings(tree)[0]


def _collect_bindings(
    tree: ast.Module,
) -> tuple[dict[str, str], dict[str, ast.expr], list[tuple[ast.expr, ast.expr]]]:
    """Walk *tree* once for everything name resolution needs.

    Args:
        tree: The parsed module.

    Returns:
        The unambiguous string aliases, the collection literals by name, and
        the ``(target, iterable)`` pairs of every loop and comprehension.
    """
    candidates: dict[str, set[str]] = {}
    collections: dict[str, ast.expr] = {}
    iterations: list[tuple[ast.expr, ast.expr]] = []
    for node in ast.walk(tree):
        source = _iteration_source(node)
        if source is not None:
            iterations.append(source)
        target, value = _single_binding(node)
        if target is None or value is None:
            continue
        if isinstance(value, ast.Tuple | ast.List | ast.Set | ast.Dict | ast.Call):
            collections[target] = value
        resolved = resolve_literal(value, {})
        if resolved is not None:
            candidates.setdefault(target, set()).add(resolved)
    aliases = {
        name: next(iter(values))
        for name, values in candidates.items()
        if len(values) == 1
    }
    return aliases, collections, iterations


def name_bindings(tree: ast.Module) -> tuple[dict[str, str], dict[str, frozenset[str]]]:
    """Resolve every name *tree* binds to a setting namespace or key.

    Two kinds. A plain binding gives one value (``_NS = SettingNamespace.X``).
    A loop or comprehension over a literal collection gives several: the OAuth
    token manager loops a ``(key, apply)`` tuple, and the Kanban service
    comprehends a ``{column: "kanban_wip_review"}`` map. Both read the settings
    live; without resolving the collection the read names only a loop variable
    and the settings look unread.

    Collection bindings are collected from anywhere in the module, function
    bodies included: the fine-tune preflight declares its key-to-fallback map
    inside the function that loops over it.

    Args:
        tree: The parsed module.

    Returns:
        The single-valued aliases, and the names an iteration can bind to
        several strings.
    """
    aliases, collections, iterations = _collect_bindings(tree)
    bound: dict[str, set[str]] = {}
    for target, iterable in iterations:
        elements = _iterable_elements(iterable, collections)
        for name, values in _bind_target(target, elements).items():
            bound.setdefault(name, set()).update(values)
    iterated = {name: frozenset(values) for name, values in bound.items() if values}
    return aliases, iterated


def _iteration_source(node: ast.AST) -> tuple[ast.expr, ast.expr] | None:
    """Return the ``(target, iterable)`` of a loop or comprehension clause."""
    if isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
        return node.target, node.iter
    return None


def _iterable_elements(
    node: ast.expr, collections: dict[str, ast.expr], depth: int = 0
) -> list[ast.expr]:
    """Return the element expressions iterating *node* yields.

    Args:
        node: The iterable expression.
        collections: Module-level collection literals.
        depth: Recursion guard for a name bound to another name.

    Returns:
        The elements, or empty when the iterable is not a resolvable literal.
    """
    if depth > _MAX_COLLECTION_DEPTH:
        return []
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return list(node.elts)
    if isinstance(node, ast.Dict):
        return [key for key in node.keys if key is not None]
    if isinstance(node, ast.Name):
        target = collections.get(node.id)
        return _iterable_elements(target, collections, depth + 1) if target else []
    if isinstance(node, ast.Call):
        return _call_elements(node, collections, depth)
    return []


def _call_elements(
    node: ast.Call, collections: dict[str, ast.expr], depth: int
) -> list[ast.expr]:
    """Return the elements a ``.items()`` view or a collection call yields."""
    if isinstance(node.func, ast.Attribute) and node.func.attr == "items":
        return _dict_items(node.func.value, collections, depth)
    if isinstance(node.func, ast.Name) and node.func.id in _COLLECTION_CALLS:
        if not node.args:
            return []
        return _iterable_elements(node.args[0], collections, depth + 1)
    return []


def _dict_items(
    node: ast.expr, collections: dict[str, ast.expr], depth: int
) -> list[ast.expr]:
    """Return synthetic ``(key, value)`` pairs for a dict literal."""
    if isinstance(node, ast.Name):
        resolved = collections.get(node.id)
        return _dict_items(resolved, collections, depth + 1) if resolved else []
    if not isinstance(node, ast.Dict) or depth > _MAX_COLLECTION_DEPTH:
        return []
    return [
        ast.Tuple(elts=[key, value], ctx=ast.Load())
        for key, value in zip(node.keys, node.values, strict=True)
        if key is not None
    ]


def _bind_target(target: ast.expr, elements: list[ast.expr]) -> dict[str, set[str]]:
    """Bind a loop target to the literals its elements supply."""
    bound: dict[str, set[str]] = {}
    if isinstance(target, ast.Name):
        bound[target.id] = {
            value
            for element in elements
            if (value := _string_constant(element)) is not None
        }
        return bound
    if not isinstance(target, ast.Tuple | ast.List):
        return bound
    for position, name in enumerate(target.elts):
        if isinstance(name, ast.Name):
            bound[name.id] = {
                value
                for element in elements
                if (value := _element_at(element, position)) is not None
            }
    return bound


def _element_at(element: ast.expr, position: int) -> str | None:
    """Return the string an unpacked element supplies at *position*."""
    if not isinstance(element, ast.Tuple | ast.List):
        return None
    if position >= len(element.elts):
        return None
    return _string_constant(element.elts[position])


def _string_constant(node: ast.expr) -> str | None:
    """Return the value of a plain string literal, or ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


type Scope = tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]


def walk_with_scopes(node: ast.AST) -> Iterator[tuple[ast.AST, Scope]]:
    """Yield every descendant of *node* with the functions enclosing it.

    Carrying the chain replaces one ``ast.walk`` per function: a read buried in
    a closure still belongs to the activation or the helper it runs inside.
    Iterative rather than recursive because this runs over every node of every
    module, where a generator frame per nesting level is the dominant cost.

    Args:
        node: The node to descend from.

    Yields:
        Each descendant paired with its chain of enclosing functions,
        outermost first.
    """
    stack: list[tuple[ast.AST, Scope]] = [(node, ())]
    while stack:
        parent, scope = stack.pop()
        for child in ast.iter_child_nodes(parent):
            child_scope = (
                (*scope, child)
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else scope
            )
            yield child, child_scope
            stack.append((child, child_scope))


def parameter_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """Return every parameter name *func* declares.

    Args:
        func: The function to inspect.

    Returns:
        The parameter names, positional and keyword-only alike.
    """
    return frozenset(
        arg.arg
        for arg in (
            *func.args.posonlyargs,
            *func.args.args,
            *func.args.kwonlyargs,
        )
    )


def positional_index(
    func: ast.FunctionDef | ast.AsyncFunctionDef, parameter: str
) -> int | None:
    """Return the position *parameter* occupies in *func*'s signature.

    Args:
        func: The function to inspect.
        parameter: The parameter name.

    Returns:
        The index, or ``None`` when the parameter is keyword-only.
    """
    positional = [arg.arg for arg in (*func.args.posonlyargs, *func.args.args)]
    return positional.index(parameter) if parameter in positional else None


def receives_instance(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether *func*'s first parameter is the implicit instance or class.

    An attribute-style call site does not pass it, so a positional index taken
    from the signature is one too high for every argument after it.

    Args:
        func: The function to inspect.

    Returns:
        ``True`` when the first parameter is ``self`` or ``cls``.
    """
    positional = [*func.args.posonlyargs, *func.args.args]
    return bool(positional) and positional[0].arg in {"self", "cls"}


def bound_argument(
    call: ast.Call, index: int | None, parameter: str
) -> ast.expr | None:
    """Return the expression *call* binds to a parameter.

    Args:
        call: The call site.
        index: Positional index of the parameter, or ``None`` when it is
            keyword-only.
        parameter: The parameter name.

    Returns:
        The bound expression, or ``None`` when the call binds nothing to it.
    """
    for keyword in call.keywords:
        if keyword.arg == parameter:
            return keyword.value
    if index is not None and 0 <= index < len(call.args):
        return call.args[index]
    return None


def called_name(call: ast.Call) -> str | None:
    """Return the bare name *call* invokes.

    Args:
        call: The call site.

    Returns:
        The function name for a plain or attribute call, or ``None``.
    """
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _single_binding(node: ast.AST) -> tuple[str | None, ast.expr | None]:
    """Return the ``(name, value)`` a single-target assignment binds.

    Args:
        node: Any AST node.

    Returns:
        The bound name and its value expression, or ``(None, None)``.
    """
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id, node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    return None, None
