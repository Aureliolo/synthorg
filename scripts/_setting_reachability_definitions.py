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
import io
import sys
import tokenize
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeIs

if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import read_and_parse  # type: ignore[import-not-found]
    from _setting_reachability_literals import (  # type: ignore[import-not-found]
        Scope,
        bound_argument,
        module_aliases,
        parameter_names,
        positional_index,
        resolve_literal,
        walk_with_scopes,
    )
else:
    from scripts._gate_source import read_and_parse
    from scripts._setting_reachability_literals import (
        Scope,
        bound_argument,
        module_aliases,
        parameter_names,
        positional_index,
        resolve_literal,
        walk_with_scopes,
    )

DEFINITIONS_REL: Final[str] = "src/synthorg/settings/definitions"
_DEFINITION_CALL: Final[str] = "SettingDefinition"
# Spans that spell the call without performing it. FSTRING_MIDDLE is the literal
# run inside an f-string, which 3.12 onward reports separately from STRING.
_UNSPOKEN_TOKENS: Final[frozenset[int]] = frozenset(
    {tokenize.COMMENT, tokenize.STRING, tokenize.FSTRING_MIDDLE}
)


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
    blank_default: bool
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
    # Recursive: a nested definitions package would otherwise register settings
    # the inventory never lists, and a setting absent from the inventory is one
    # the gate never checks.
    paths = sorted(
        path
        for path in (repo_root / DEFINITIONS_REL).rglob("*.py")
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
        SettingScanError: If a registration cannot be resolved, or if the
            module spells more registrations than the scan recognised.
    """
    source, tree = read_and_parse(path)
    aliases = module_aliases(tree)
    names = _definition_names(tree)
    helper_values = _helper_parameter_values(tree, names)
    seen = 0
    for node, scope in walk_with_scopes(tree):
        if not _is_definition_call(node, names):
            continue
        seen += 1
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        namespace = resolve_literal(kwargs.get("namespace"), aliases)
        if namespace is None:
            raise SettingScanError(_unresolved(rel, node.lineno, "namespace"))
        for key in _resolve_keys(
            kwargs.get("key"), helper_values, scope, rel, node.lineno
        ):
            yield SettingRecord(
                namespace=namespace,
                key=key,
                compose_set=_compose_set(kwargs, rel, node.lineno),
                blank_default=_blank_default(kwargs, aliases),
                source_file=rel,
                source_line=node.lineno,
            )
    _check_all_recognised(source, seen, rel)


def _definition_names(tree: ast.Module) -> frozenset[str]:
    """Return every name this module can call ``SettingDefinition`` by.

    An aliased import is the one shape a missed match would drop silently
    rather than raise on, so the alias is resolved instead of assumed away.
    """
    names = {_DEFINITION_CALL}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name == _DEFINITION_CALL and alias.asname:
                names.add(alias.asname)
    return frozenset(names)


def _is_definition_call(node: ast.AST, names: frozenset[str]) -> TypeIs[ast.Call]:
    """Whether *node* constructs a ``SettingDefinition``, however it is named."""
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id in names
    return isinstance(node.func, ast.Attribute) and node.func.attr == _DEFINITION_CALL


def _blank_unspoken(source: str, rel: str) -> str:
    """Overwrite comment and string spans with spaces, keeping every position.

    Masking beats subtracting a separately counted total: a discount has to
    rediscover every way the text can name the call, and it silently went wrong
    for a ``#`` comment, which no AST node covers. Counting adjacent tokens
    instead cannot work at all, because the tokeniser reports the name and its
    parenthesis separately, so the needle matches no single token and the check
    would pass everything.

    Args:
        source: The module text.
        rel: Repository-relative path, for the error message.

    Returns:
        The text with masked spans blanked, so offsets still line up.

    Raises:
        SettingScanError: If the module cannot be tokenised.
    """
    grid = [list(line) for line in source.splitlines(keepends=True)]
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, tokenize.TokenError) as exc:
        message = (
            f"{rel}: cannot be tokenised, so the registration count is"
            " unverifiable and the scan cannot be trusted"
        )
        raise SettingScanError(message) from exc
    for token in tokens:
        if token.type not in _UNSPOKEN_TOKENS:
            continue
        (first_row, first_col), (last_row, last_col) = token.start, token.end
        for row in range(first_row - 1, last_row):
            line = grid[row]
            start = first_col if row == first_row - 1 else 0
            stop = last_col if row == last_row - 1 else len(line)
            for col in range(start, min(stop, len(line))):
                if line[col] != "\n":
                    line[col] = " "
    return "".join("".join(line) for line in grid)


def _check_all_recognised(source: str, seen: int, rel: str) -> None:
    """Fail when the text spells more registrations than the AST matched.

    A shape the matcher does not know yet drops its setting from the inventory
    without raising anywhere else, which is the one failure this scan cannot
    detect from its own results.

    Args:
        source: The module text, counted for spelled registrations.
        seen: How many registrations the AST walk resolved.
        rel: Repository-relative path, for the error message.

    Raises:
        SettingScanError: If the counts disagree.
    """
    needle = f"{_DEFINITION_CALL}("
    # A docstring, error message or comment naming the call is prose, not a
    # registration; counting it would fail the scan for a documentation edit.
    spelled = _blank_unspoken(source, rel).count(needle)
    if spelled > seen:
        message = (
            f"{rel}: found {spelled} '{_DEFINITION_CALL}(' in the source but"
            f" resolved {seen}. A registration is spelled in a shape this scan"
            " does not recognise, so it would be silently unchecked."
        )
        raise SettingScanError(message)


def _compose_set(kwargs: dict[str, ast.expr], rel: str, lineno: int) -> bool:
    """Resolve ``compose_set=``, refusing a value the scan cannot pin.

    Raises:
        SettingScanError: If present but not a literal bool.
    """
    flag = kwargs.get("compose_set")
    if flag is None:
        return False
    if isinstance(flag, ast.Constant) and isinstance(flag.value, bool):
        return flag.value
    raise SettingScanError(_unresolved(rel, lineno, "compose_set"))


def _blank_default(kwargs: dict[str, ast.expr], aliases: Mapping[str, str]) -> bool:
    """Whether the setting ships with no value at all.

    A blank default is what makes the chicken-and-egg possible: the component
    the setting configures is not built until an operator names a value, so
    the only live read of the setting lives inside an object that does not yet
    exist. The gate needs to know which settings are in that position.

    Args:
        kwargs: The registration's keyword arguments.
        aliases: Module-level name bindings, so a default spelled as a
            constant resolves to the string it denotes.

    Returns:
        ``True`` for an absent, ``None`` or empty-string default. A computed
        default (``str(CONST)``, ``json.dumps(...)``) is a value, so it is not
        blank whether or not the scan can evaluate it.
    """
    node = kwargs.get("default")
    if node is None:
        return True
    if isinstance(node, ast.Constant):
        return node.value is None or node.value == ""
    if isinstance(node, ast.Name):
        return aliases.get(node.id) == ""
    return False


def _resolve_keys(
    node: ast.expr | None,
    helper_values: dict[str, dict[str, tuple[str, ...]]],
    scope: Scope,
    rel: str,
    lineno: int,
) -> tuple[str, ...]:
    """Resolve the ``key=`` argument to every key it registers.

    Args:
        node: The ``key=`` expression.
        helper_values: Per-helper parameter-name to literal values.
        scope: The functions lexically enclosing this registration.
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
        # Resolved against the helper this registration actually sits in, so
        # two helpers sharing a parameter name keep their own call sites
        # instead of one silently answering for the other.
        for func in reversed(scope):
            values = helper_values.get(func.name, {}).get(node.id, ())
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


def _helper_parameter_values(
    tree: ast.Module, definition_names: frozenset[str]
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Resolve the key literals each registration helper is called with.

    A registration helper is a module-level function whose body constructs a
    ``SettingDefinition`` with ``key=<parameter>``. Its call sites in the same
    module supply the actual keys.

    Args:
        tree: The parsed definitions module.
        definition_names: Every name the module calls ``SettingDefinition`` by.

    Returns:
        Helper name to its parameter-name to the literals its call sites pass.
        Keying by helper is what stops two helpers that happen to share a
        parameter name from answering for each other's registrations. A
        parameter is absent when any call site passes something unresolvable,
        so the registration fails loud rather than registering a partial set.
    """
    resolved: dict[str, dict[str, tuple[str, ...]]] = {}
    for func in tree.body:
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        parameter = _key_parameter(func, definition_names)
        if parameter is None:
            continue
        values = _call_site_literals(tree, func, parameter)
        if values is not None:
            resolved.setdefault(func.name, {})[parameter] = values
    return resolved


def _key_parameter(
    func: ast.FunctionDef | ast.AsyncFunctionDef, definition_names: frozenset[str]
) -> str | None:
    """Return the parameter *func* passes as a ``SettingDefinition`` key.

    Args:
        func: A module-level function.
        definition_names: Every name the module calls ``SettingDefinition`` by.

    Returns:
        The parameter name, or ``None`` when *func* is not a registration
        helper.
    """
    names = parameter_names(func)
    for call in ast.walk(func):
        if not _is_definition_call(call, definition_names):
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
    index = positional_index(func, parameter)
    values: list[str] = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            continue
        if call.func.id != func.name:
            continue
        node = bound_argument(call, index, parameter)
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            return None
        values.append(node.value)
    return tuple(values) or None
