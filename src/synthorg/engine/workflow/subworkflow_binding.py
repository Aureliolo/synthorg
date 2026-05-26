"""Input/output binding resolution for subworkflow calls.

Subworkflow invocations declare typed I/O contracts via
:class:`WorkflowIODeclaration`.  When the executor walks a
``SUBWORKFLOW`` node, it pushes a new frame whose ``variables`` map
contains exactly the declared inputs (resolved against the caller's
frame).  On child completion, declared outputs are projected back into
the caller's frame.

This module contains the pure evaluation helpers.  They never touch
persistence, the registry, or the execution service -- they operate
on plain mappings so they can be unit-tested with Hypothesis.

Binding expression language (minimal, deliberately):

- **Literal**: any non-string value or a string not starting with ``@``.
  Literals pass through unchanged (subject to type validation).
- **Dotted path lookup**: a string like ``"@parent.current_quarter"``.
  The prefix before the first ``.`` names the source scope; the rest
  is a dotted path into that scope's variable mapping. Two scopes are
  currently recognized:
    - ``@parent`` -- the parent frame's variables (used in input bindings)
    - ``@child``  -- the child frame's variables (used in output bindings)
"""

import copy
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING

from synthorg.core.enums import WorkflowValueType
from synthorg.engine.errors import SubworkflowIOError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_definition import SUBWORKFLOW_IO_INVALID

if TYPE_CHECKING:
    from synthorg.engine.workflow.definition import WorkflowIODeclaration

logger = get_logger(__name__)

_PARENT_PREFIX = "@parent."
_CHILD_PREFIX = "@child."


def _lookup_path(source: Mapping[str, object], path: str) -> object:
    """Walk a dotted path through a nested mapping.

    Raises ``KeyError`` if any segment is missing or traverses a
    non-mapping leaf.

    Returns:
        The leaf value addressed by the dotted ``path``.

    Raises:
        KeyError: When ``path`` is empty, a segment is missing, or a
            non-mapping leaf is traversed before reaching the leaf.
    """
    if not path:
        msg = "Empty lookup path"
        raise KeyError(msg)

    parts = path.split(".")
    current: object = source
    for part in parts:
        if not isinstance(current, Mapping):
            msg = f"Lookup path {path!r} traversed non-mapping at segment {part!r}"
            raise KeyError(msg)
        if part not in current:
            msg = f"Lookup path {path!r} missing segment {part!r}"
            raise KeyError(msg)
        current = current[part]
    return current


def _resolve_expression(
    expression: object,
    *,
    parent_vars: Mapping[str, object],
    child_vars: Mapping[str, object] | None = None,
) -> object:
    """Resolve a single binding expression to a concrete value.

    ``@parent.<path>`` and ``@child.<path>`` are dotted-path lookups.
    Non-string values and strings not starting with ``@`` are literals.
    Any other ``@``-prefixed string raises ``SubworkflowIOError``.

    Returns:
        The resolved value (looked up via ``@parent`` / ``@child`` or
        the literal ``expression`` itself when no prefix matches).

    Raises:
        SubworkflowIOError: When ``@child`` is used without a child
            scope or an unsupported ``@``-prefix is encountered.
    """
    if not isinstance(expression, str):
        return expression
    if expression.startswith(_PARENT_PREFIX):
        path = expression[len(_PARENT_PREFIX) :]
        return _lookup_path(parent_vars, path)
    if expression.startswith(_CHILD_PREFIX):
        if child_vars is None:
            msg = (
                f"Binding {expression!r} references '@child' but "
                f"no child variables are available in this context"
            )
            raise SubworkflowIOError(msg)
        path = expression[len(_CHILD_PREFIX) :]
        return _lookup_path(child_vars, path)
    if expression.startswith("@"):
        msg = (
            f"Unsupported binding prefix in {expression!r}; use '@parent.' or '@child.'"
        )
        raise SubworkflowIOError(msg)
    return expression


def _validate_value_type(
    name: str,
    value: object,
    expected: WorkflowValueType,
) -> None:
    """Enforce that *value* is compatible with *expected*.

    Raises ``SubworkflowIOError`` on mismatch.  ``JSON`` accepts any
    value (it is deliberately permissive for structured payloads);
    ``TASK_REF``/``AGENT_REF`` accept non-blank strings.

    Raises:
        SubworkflowIOError: When ``value`` is not compatible with
            the declared ``expected`` type.
    """
    if expected is WorkflowValueType.STRING:
        if not isinstance(value, str):
            msg = f"Declaration {name!r} expects STRING, got {type(value).__name__}"
            raise SubworkflowIOError(msg)
        return
    if expected is WorkflowValueType.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            msg = f"Declaration {name!r} expects INTEGER, got {type(value).__name__}"
            raise SubworkflowIOError(msg)
        return
    if expected is WorkflowValueType.FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            msg = f"Declaration {name!r} expects FLOAT, got {type(value).__name__}"
            raise SubworkflowIOError(msg)
        return
    if expected is WorkflowValueType.BOOLEAN:
        if not isinstance(value, bool):
            msg = f"Declaration {name!r} expects BOOLEAN, got {type(value).__name__}"
            raise SubworkflowIOError(msg)
        return
    if expected is WorkflowValueType.DATETIME:
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value)
            except ValueError:
                msg = (
                    f"Declaration {name!r} expects DATETIME"
                    f" (ISO-8601 string or datetime), got {value!r}"
                )
                raise SubworkflowIOError(msg) from None
            return
        if not isinstance(value, datetime):
            msg = f"Declaration {name!r} expects DATETIME, got {type(value).__name__}"
            raise SubworkflowIOError(msg)
        return
    if expected in (WorkflowValueType.TASK_REF, WorkflowValueType.AGENT_REF):
        if not isinstance(value, str) or not value.strip():
            msg = (
                f"Declaration {name!r} expects {expected.value.upper()}, got "
                f"{type(value).__name__}"
            )
            raise SubworkflowIOError(msg)
        return
    # WorkflowValueType.JSON is permissive by design.
    return


def resolve_input_bindings(
    bindings: Mapping[str, object],
    parent_vars: Mapping[str, object],
    declarations: tuple[WorkflowIODeclaration, ...],
) -> dict[str, object]:
    """Resolve a subworkflow call's input bindings.

    Args:
        bindings: Mapping from child input name to a literal or
            ``@parent.<path>`` expression.
        parent_vars: The calling frame's variable map.
        declarations: The child's declared input contract.

    Returns:
        A new ``dict`` mapping each declared input name to its
        resolved, type-validated value.  Missing optional inputs
        fall back to ``default`` (when the declaration supplies one).

    Raises:
        SubworkflowIOError: On unknown binding keys, missing required
            inputs, invalid dotted paths, or type mismatches.
    """
    declaration_by_name = {d.name: d for d in declarations}
    unknown = set(bindings) - set(declaration_by_name)
    if unknown:
        msg = (
            f"Unknown input bindings: {sorted(unknown)}; "
            f"declared inputs are {sorted(declaration_by_name)}"
        )
        logger.warning(SUBWORKFLOW_IO_INVALID, error=msg)
        raise SubworkflowIOError(msg)

    resolved: dict[str, object] = {}
    for decl in declarations:
        if decl.name in bindings:
            expression = bindings[decl.name]
            try:
                value = _resolve_expression(
                    expression,
                    parent_vars=parent_vars,
                )
            except KeyError as exc:
                msg = f"Cannot resolve input binding {decl.name!r}: {safe_error_description(exc)}"  # noqa: E501
                logger.warning(SUBWORKFLOW_IO_INVALID, error=msg)
                raise SubworkflowIOError(msg) from exc
            _validate_value_type(decl.name, value, decl.type)
            resolved[decl.name] = copy.deepcopy(value)
            continue
        if decl.required:
            msg = f"Missing required input {decl.name!r}"
            logger.warning(SUBWORKFLOW_IO_INVALID, error=msg)
            raise SubworkflowIOError(msg)
        if decl.default is not None:
            _validate_value_type(decl.name, decl.default, decl.type)
            resolved[decl.name] = copy.deepcopy(decl.default)
    return resolved


def project_output_bindings(
    bindings: Mapping[str, object],
    child_vars: Mapping[str, object],
    declarations: tuple[WorkflowIODeclaration, ...],
    *,
    parent_vars: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Project a subworkflow's outputs into the caller's scope.

    Args:
        bindings: Mapping from ``parent_target_name`` to a literal,
            ``@child.<path>`` expression, or ``@parent.<path>``
            expression (pass-through).  The binding key names the
            variable to set in the caller's frame; it must match one
            of the child's output declarations for type validation.
        child_vars: The child frame's final variable map.
        declarations: The child's declared output contract.
        parent_vars: The calling frame's variable map, required for
            ``@parent.*`` pass-through bindings.

    Returns:
        A new ``dict`` containing values to merge into the caller's
        frame.

    Raises:
        SubworkflowIOError: On unknown binding keys (names not declared
            as outputs), missing required outputs, invalid dotted paths,
            or type mismatches.
    """
    declaration_by_name = {d.name: d for d in declarations}
    unknown = set(bindings) - set(declaration_by_name)
    if unknown:
        msg = (
            f"Unknown output bindings: {sorted(unknown)}; "
            f"declared outputs are {sorted(declaration_by_name)}"
        )
        logger.warning(SUBWORKFLOW_IO_INVALID, error=msg)
        raise SubworkflowIOError(msg)

    caller_vars = parent_vars if parent_vars is not None else {}
    projected: dict[str, object] = {}
    for decl in declarations:
        if decl.name in bindings:
            expression = bindings[decl.name]
            try:
                value = _resolve_expression(
                    expression,
                    parent_vars=caller_vars,
                    child_vars=child_vars,
                )
            except KeyError as exc:
                msg = f"Cannot resolve output binding {decl.name!r}: {safe_error_description(exc)}"  # noqa: E501
                logger.warning(SUBWORKFLOW_IO_INVALID, error=msg)
                raise SubworkflowIOError(msg) from exc
            _validate_value_type(decl.name, value, decl.type)
            projected[decl.name] = copy.deepcopy(value)
            continue
        if decl.required:
            msg = f"Missing required output binding for {decl.name!r}"
            logger.warning(SUBWORKFLOW_IO_INVALID, error=msg)
            raise SubworkflowIOError(msg)
        if decl.default is not None:
            _validate_value_type(decl.name, decl.default, decl.type)
            projected[decl.name] = copy.deepcopy(decl.default)
    return projected


__all__ = [
    "project_output_bindings",
    "resolve_input_bindings",
]
