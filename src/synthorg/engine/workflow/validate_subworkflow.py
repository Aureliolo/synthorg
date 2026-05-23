"""Subworkflow I/O binding and cycle-detection validation."""

from datetime import datetime
from typing import TYPE_CHECKING

from synthorg.core.enums import WorkflowNodeType, WorkflowValueType
from synthorg.engine.workflow.validation_types import (
    ValidationErrorCode,
    WorkflowValidationError,
    WorkflowValidationResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow_definition import (
    SUBWORKFLOW_CYCLE_DETECTED,
    SUBWORKFLOW_IO_INVALID,
)

if TYPE_CHECKING:
    from synthorg.engine.workflow.definition import (
        WorkflowDefinition,
        WorkflowIODeclaration,
    )
    from synthorg.engine.workflow.subworkflow_registry import SubworkflowRegistry

logger = get_logger(__name__)


def extract_subworkflow_config(
    node_config: object,
) -> tuple[str, str | None, dict[str, object], dict[str, object]] | None:
    """Unpack subworkflow node config into ``(id, version, ib, ob)``."""
    if not isinstance(node_config, dict):
        return None
    subworkflow_id = node_config.get("subworkflow_id")
    if not isinstance(subworkflow_id, str) or not subworkflow_id.strip():
        return None
    version_obj = node_config.get("version")
    if version_obj is None:
        version = None
    elif isinstance(version_obj, str):
        version = version_obj.strip() or None
    else:
        return None
    ib = node_config.get("input_bindings") or {}
    ob = node_config.get("output_bindings") or {}
    if not isinstance(ib, dict):
        ib = {}
    if not isinstance(ob, dict):
        ob = {}
    return subworkflow_id, version, ib, ob


def _literal_matches_type(
    value: object,
    value_type: WorkflowValueType,
) -> bool:
    """Return ``True`` if *value* is compatible with *value_type*."""
    if value_type is WorkflowValueType.STRING:
        return isinstance(value, str)
    if value_type is WorkflowValueType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type is WorkflowValueType.FLOAT:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type is WorkflowValueType.BOOLEAN:
        return isinstance(value, bool)
    if value_type in (
        WorkflowValueType.TASK_REF,
        WorkflowValueType.AGENT_REF,
    ):
        return isinstance(value, str) and bool(value.strip())
    if value_type is WorkflowValueType.DATETIME:
        if isinstance(value, datetime):
            return True
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value)
            except ValueError:
                return False
            else:
                return True
        return False
    return True


def _is_deferred_expression(value: object, *, direction: str = "") -> bool:
    """Return ``True`` when *value* is a valid deferred lookup for *direction*."""
    if not isinstance(value, str):
        return False
    if direction == "input":
        return value.startswith("@parent.")
    if direction == "output":
        return value.startswith(("@child.", "@parent."))
    return value.startswith(("@parent.", "@child."))


def _check_bindings_against_declarations(  # noqa: PLR0913
    *,
    node_id: str,
    ref_label: str,
    bindings: dict[str, object],
    declarations: tuple[WorkflowIODeclaration, ...],
    direction: str,
    missing_code: ValidationErrorCode,
    unknown_code: ValidationErrorCode,
    type_code: ValidationErrorCode,
) -> list[WorkflowValidationError]:
    """Validate binding keys/literals against a set of declarations."""
    errors: list[WorkflowValidationError] = [
        WorkflowValidationError(
            code=missing_code,
            message=(
                f"Subworkflow node {node_id!r} missing required "
                f"{direction} {d.name!r} for {ref_label}"
            ),
            node_id=node_id,
        )
        for d in declarations
        if d.required and d.name not in bindings
    ]
    by_name = {d.name: d for d in declarations}
    for name, value in bindings.items():
        if name not in by_name:
            errors.append(
                WorkflowValidationError(
                    code=unknown_code,
                    message=(
                        f"Subworkflow node {node_id!r} binds unknown "
                        f"{direction} {name!r} for {ref_label}"
                    ),
                    node_id=node_id,
                ),
            )
            continue
        decl = by_name[name]
        if _is_deferred_expression(value, direction=direction):
            continue
        if isinstance(value, str) and value.startswith("@"):
            errors.append(
                WorkflowValidationError(
                    code=type_code,
                    message=(
                        f"Subworkflow node {node_id!r} binds {direction} "
                        f"{name!r} with unsupported expression {value!r}"
                    ),
                    node_id=node_id,
                ),
            )
            continue
        if not _literal_matches_type(value, decl.type):
            errors.append(
                WorkflowValidationError(
                    code=type_code,
                    message=(
                        f"Subworkflow node {node_id!r} binds {direction} "
                        f"{name!r} with literal incompatible with "
                        f"declared type {decl.type.value}"
                    ),
                    node_id=node_id,
                ),
            )
    return errors


def _check_subworkflow_io_for_node(  # noqa: PLR0913
    *,
    node_id: str,
    subworkflow_id: str,
    version: str,
    input_bindings: dict[str, object],
    output_bindings: dict[str, object],
    child_inputs: tuple[WorkflowIODeclaration, ...],
    child_outputs: tuple[WorkflowIODeclaration, ...],
) -> list[WorkflowValidationError]:
    """Check a single SUBWORKFLOW node's bindings against child I/O."""
    ref_label = f"{subworkflow_id!r}@{version!r}"
    errors = _check_bindings_against_declarations(
        node_id=node_id,
        ref_label=ref_label,
        bindings=input_bindings,
        declarations=child_inputs,
        direction="input",
        missing_code=ValidationErrorCode.SUBWORKFLOW_INPUT_MISSING,
        unknown_code=ValidationErrorCode.SUBWORKFLOW_INPUT_UNKNOWN,
        type_code=ValidationErrorCode.SUBWORKFLOW_INPUT_TYPE_MISMATCH,
    )
    errors.extend(
        _check_bindings_against_declarations(
            node_id=node_id,
            ref_label=ref_label,
            bindings=output_bindings,
            declarations=child_outputs,
            direction="output",
            missing_code=ValidationErrorCode.SUBWORKFLOW_OUTPUT_MISSING,
            unknown_code=ValidationErrorCode.SUBWORKFLOW_OUTPUT_UNKNOWN,
            type_code=ValidationErrorCode.SUBWORKFLOW_OUTPUT_TYPE_MISMATCH,
        ),
    )
    return errors


async def validate_subworkflow_io(
    definition: WorkflowDefinition,
    registry: SubworkflowRegistry,
) -> WorkflowValidationResult:
    """Validate every SUBWORKFLOW node's bindings against its child."""
    errors: list[WorkflowValidationError] = []
    from synthorg.engine.errors import SubworkflowNotFoundError  # noqa: PLC0415

    for node in definition.nodes:
        if node.type is not WorkflowNodeType.SUBWORKFLOW:
            continue
        parsed = extract_subworkflow_config(dict(node.config))
        if parsed is None:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.SUBWORKFLOW_REF_MISSING,
                    message=(
                        f"Subworkflow node {node.id!r} is missing "
                        "subworkflow_id or version in config"
                    ),
                    node_id=node.id,
                ),
            )
            continue
        subworkflow_id, version, ib, ob = parsed

        if version is None:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.SUBWORKFLOW_VERSION_UNPINNED,
                    message=(
                        f"Subworkflow node {node.id!r} references "
                        f"{subworkflow_id!r} without a pinned version"
                    ),
                    node_id=node.id,
                ),
            )
            continue

        try:
            child = await registry.get(subworkflow_id, version)
        except SubworkflowNotFoundError:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.SUBWORKFLOW_NOT_FOUND,
                    message=(
                        f"Subworkflow node {node.id!r} references "
                        f"{subworkflow_id!r}@{version!r}, which is "
                        "not in the registry"
                    ),
                    node_id=node.id,
                ),
            )
            continue

        errors.extend(
            _check_subworkflow_io_for_node(
                node_id=node.id,
                subworkflow_id=subworkflow_id,
                version=version,
                input_bindings=ib,
                output_bindings=ob,
                child_inputs=child.inputs,
                child_outputs=child.outputs,
            ),
        )

    result = WorkflowValidationResult(errors=tuple(errors))
    if errors:
        logger.warning(
            SUBWORKFLOW_IO_INVALID,
            workflow_id=definition.id,
            error_count=len(errors),
        )
    return result


async def validate_subworkflow_graph(
    definition: WorkflowDefinition,
    registry: SubworkflowRegistry,
) -> WorkflowValidationResult:
    """Detect cycles across the static subworkflow reference graph."""
    errors: list[WorkflowValidationError] = []
    root_key = (definition.id, definition.version)
    visiting: set[tuple[str, str]] = set()
    finished: set[tuple[str, str]] = set()

    from synthorg.engine.errors import SubworkflowNotFoundError  # noqa: PLC0415

    async def _visit(
        node_key: tuple[str, str],
        source_definition: WorkflowDefinition,
        path: list[tuple[str, str]],
    ) -> None:
        if node_key in visiting:
            cycle_slice = [*path[path.index(node_key) :], node_key]
            cycle_repr = " -> ".join(f"{sid}@{ver}" for sid, ver in cycle_slice)
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.SUBWORKFLOW_CYCLE_DETECTED,
                    message=(f"Subworkflow reference cycle detected: {cycle_repr}"),
                ),
            )
            return
        if node_key in finished:
            return

        visiting.add(node_key)
        path.append(node_key)
        try:
            for child_node in source_definition.nodes:
                if child_node.type is not WorkflowNodeType.SUBWORKFLOW:
                    continue
                parsed = extract_subworkflow_config(dict(child_node.config))
                if parsed is None:
                    continue
                child_sub_id, child_version, _, _ = parsed
                if child_version is None:
                    continue
                child_key = (child_sub_id, child_version)

                try:
                    child_definition = await registry.get(
                        child_sub_id,
                        child_version,
                    )
                except SubworkflowNotFoundError:
                    continue
                await _visit(child_key, child_definition, path)
        finally:
            visiting.discard(node_key)
            path.pop()
            finished.add(node_key)

    await _visit(root_key, definition, [])

    if errors:
        logger.warning(
            SUBWORKFLOW_CYCLE_DETECTED,
            workflow_id=definition.id,
            cycle_count=len(errors),
        )
    return WorkflowValidationResult(errors=tuple(errors))
