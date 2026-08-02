"""The registered-settings inventory, resolved without importing the tree.

Every ``SettingDefinition(...)`` under ``settings/definitions/`` is one record.
The scan resolves the namespace through module aliases and the key through
either a literal or the registration-helper shape (a module-level function that
takes the key as a parameter and registers it, which is how
``self_improvement.py`` declares six of its flags).

A registration the scan cannot pin raises :class:`SettingScanError` rather than
being skipped. Skipping is what makes a gate worse than absent: the setting it
dropped is the one nothing then checks.
"""

import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeIs

if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import read_and_parse  # type: ignore[import-not-found]
    from _setting_reachability_literals import (  # type: ignore[import-not-found]
        module_aliases,
        resolve_literal,
    )
else:
    from scripts._gate_source import read_and_parse
    from scripts._setting_reachability_literals import module_aliases, resolve_literal

DEFINITIONS_REL: Final[str] = "src/synthorg/settings/definitions"
_DEFINITION_CALL: Final[str] = "SettingDefinition"


class SettingScanError(Exception):
    """A registration could not be resolved to a ``(namespace, key)`` pair.

    Raised so the gate exits 2 instead of reporting a verdict derived from an
    inventory that silently lost entries.
    """


@dataclass(frozen=True)
class SettingRecord:
    """One registered setting and where it is declared."""

    namespace: str
    key: str
    compose_set: bool
    source_file: str
    source_line: int

    @property
    def pair(self) -> tuple[str, str]:
        """The ``(namespace, key)`` pair every seam is matched against."""
        return (self.namespace, self.key)

    @property
    def setting_key(self) -> str:
        """The dotted ``namespace.key`` form used in reports and baselines."""
        return f"{self.namespace}.{self.key}"


def load_definitions(repo_root: Path) -> tuple[SettingRecord, ...]:
    """Return every registered setting under ``settings/definitions/``.

    Args:
        repo_root: Project root to scan.

    Returns:
        The records, in file then declaration order.

    Raises:
        SettingScanError: If the definitions tree is missing or empty, or a
            registration cannot be resolved.
        GateSourceError: If a definitions module cannot be read or parsed.
    """
    paths = sorted(
        path
        for path in (repo_root / DEFINITIONS_REL).glob("*.py")
        if path.name != "__init__.py"
    )
    if not paths:
        msg = (
            f"{DEFINITIONS_REL}: no setting definitions found. A scan with an"
            " empty inventory would pass every rule vacuously."
        )
        raise SettingScanError(msg)
    records: list[SettingRecord] = []
    for path in paths:
        rel = path.relative_to(repo_root).as_posix()
        records.extend(_scan_file(path, rel))
    return tuple(records)


def _scan_file(path: Path, rel: str) -> Iterator[SettingRecord]:
    """Yield the settings declared in one definitions module.

    Args:
        path: The module to scan.
        rel: Repository-relative path, used in records and error messages.

    Yields:
        One record per resolved registration.

    Raises:
        SettingScanError: If a registration cannot be resolved.
    """
    _, tree = read_and_parse(path)
    aliases = module_aliases(tree)
    helper_values = _helper_parameter_values(tree)
    for call in ast.walk(tree):
        if not _is_definition_call(call):
            continue
        kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        namespace = resolve_literal(kwargs.get("namespace"), aliases)
        if namespace is None:
            raise SettingScanError(_unresolved(rel, call.lineno, "namespace"))
        flag = kwargs.get("compose_set")
        compose_set = isinstance(flag, ast.Constant) and flag.value is True
        for key in _resolve_keys(kwargs.get("key"), helper_values, rel, call.lineno):
            yield SettingRecord(
                namespace=namespace,
                key=key,
                compose_set=compose_set,
                source_file=rel,
                source_line=call.lineno,
            )


def _is_definition_call(node: ast.AST) -> TypeIs[ast.Call]:
    """Whether *node* constructs a ``SettingDefinition``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == _DEFINITION_CALL
    )


def _resolve_keys(
    node: ast.expr | None,
    helper_values: dict[str, tuple[str, ...]],
    rel: str,
    lineno: int,
) -> tuple[str, ...]:
    """Resolve the ``key=`` argument to every key it registers.

    Args:
        node: The ``key=`` expression.
        helper_values: Parameter-name to literal values, from the enclosing
            module's registration helpers.
        rel: Repository-relative path, for the error message.
        lineno: Declaration line, for the error message.

    Returns:
        One key for a literal, or every call-site literal for a helper
        parameter.

    Raises:
        SettingScanError: If the key cannot be resolved.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.Name):
        values = helper_values.get(node.id, ())
        if values:
            return values
    raise SettingScanError(_unresolved(rel, lineno, "key"))


def _unresolved(rel: str, lineno: int, field: str) -> str:
    """Build the message for a registration the scan cannot pin."""
    return (
        f"{rel}:{lineno}: cannot resolve the {field} of this SettingDefinition."
        " Declare it as a literal, a SettingNamespace member, or a module-level"
        " constant, or register it through a helper called with literals."
    )


def _helper_parameter_values(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    """Resolve the key literals each registration helper is called with.

    A registration helper is a module-level function whose body constructs a
    ``SettingDefinition`` with ``key=<parameter>``. Its call sites in the same
    module supply the actual keys.

    Args:
        tree: The parsed definitions module.

    Returns:
        Parameter name to the literals its call sites pass. A parameter is
        absent when any call site passes something unresolvable, so the
        registration fails loud rather than registering a partial key set.
    """
    resolved: dict[str, tuple[str, ...]] = {}
    for func in tree.body:
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        parameter = _key_parameter(func)
        if parameter is None:
            continue
        values = _call_site_literals(tree, func, parameter)
        if values is not None:
            resolved[parameter] = values
    return resolved


def _key_parameter(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return the parameter *func* passes as a ``SettingDefinition`` key.

    Args:
        func: A module-level function.

    Returns:
        The parameter name, or ``None`` when *func* is not a registration
        helper.
    """
    names = {
        arg.arg
        for arg in (
            *func.args.posonlyargs,
            *func.args.args,
            *func.args.kwonlyargs,
        )
    }
    for call in ast.walk(func):
        if not _is_definition_call(call):
            continue
        for kw in call.keywords:
            if (
                kw.arg == "key"
                and isinstance(kw.value, ast.Name)
                and kw.value.id in names
            ):
                return kw.value.id
    return None


def _call_site_literals(
    tree: ast.Module,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    parameter: str,
) -> tuple[str, ...] | None:
    """Collect the literals *func*'s call sites bind to *parameter*.

    Args:
        tree: The module holding the call sites.
        func: The registration helper.
        parameter: The parameter carrying the setting key.

    Returns:
        The literals, or ``None`` when a call site passes a non-literal (which
        leaves the registration unresolvable, and so a hard failure).
    """
    positional = [arg.arg for arg in (*func.args.posonlyargs, *func.args.args)]
    index = positional.index(parameter) if parameter in positional else None
    values: list[str] = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            continue
        if call.func.id != func.name:
            continue
        node = _bound_argument(call, index, parameter)
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            return None
        values.append(node.value)
    return tuple(values) or None


def _bound_argument(
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
    for kw in call.keywords:
        if kw.arg == parameter:
            return kw.value
    if index is not None and index < len(call.args):
        return call.args[index]
    return None
