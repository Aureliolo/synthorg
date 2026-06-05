"""Artifact and ontology integration handlers.

Artifact management (list / get / create / delete) through
:func:`artifact_facade_service_of`, plus the read-only ontology surface
(list entities / get entity / get relationships / search) through
:func:`ontology_facade_service_of`. The destructive ``artifacts_delete``
path enforces the admin guardrail triple and emits
``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from typing import TYPE_CHECKING, Any

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import (
    artifact_facade_service_of,
    ontology_facade_service_of,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._integrations_helpers import _require_int
from synthorg.meta.mcp.handlers._mcp_handler_common import (
    _map_capability,
    _require_str,
    _require_uuid,
    _to_jsonable,
)
from synthorg.meta.mcp.handlers.common import (
    err,
    ok,
    paginate_sequence,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
    require_actor_id,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


async def _artifacts_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List recorded artifacts (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_artifacts_list"
    try:
        offset, limit = coerce_pagination(arguments)
        artifacts = await artifact_facade_service_of(app_state).list_artifacts()
        page, pagination = paginate_sequence(
            artifacts,
            offset=offset,
            limit=limit,
            total=len(artifacts),
        )
        return ok([a.to_dict() for a in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _artifacts_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single artifact by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_artifacts_get"
    try:
        artifact_id = _require_uuid(arguments, "artifact_id")
        artifact = await artifact_facade_service_of(app_state).get_artifact(artifact_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if artifact is None:
        return err(
            LookupError(f"Artifact {artifact_id} not found"),
            domain_code="not_found",
        )
    return ok(artifact.to_dict())


async def _artifacts_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Record a new artifact (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_artifacts_create"
    try:
        name = _require_str(arguments, "name")
        content_type = _require_str(arguments, "content_type")
        size_bytes = _require_int(arguments, "size_bytes")
        storage_ref = _require_str(arguments, "storage_ref")
        artifact = await artifact_facade_service_of(app_state).create_artifact(
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_ref=storage_ref,
            actor_id=require_actor_id(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(artifact.to_dict())


async def _artifacts_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete an artifact (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_artifacts_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        artifact_id = _require_uuid(arguments, "artifact_id")
        actor_id = require_actor_id(resolved_actor)
        removed = await artifact_facade_service_of(app_state).delete_artifact(
            artifact_id=artifact_id,
            actor_id=actor_id,
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                artifact_id=artifact_id,
                removed=removed,
            )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({"removed": removed})


async def _ontology_list_entities(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List known ontology entity types.

    Returns:
        Resulting string.
    """
    tool = "synthorg_ontology_list_entities"
    try:
        entities = await ontology_facade_service_of(app_state).list_entities()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(e) for e in entities])


async def _ontology_get_entity(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single ontology entity by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_ontology_get_entity"
    try:
        entity_id = _require_str(arguments, "entity_id")
        entity = await ontology_facade_service_of(app_state).get_entity(entity_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if entity is None:
        return err(
            LookupError(f"Entity {entity_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(entity))


async def _ontology_get_relationships(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the relationship graph for a single ontology entity.

    Returns:
        Resulting string.
    """
    tool = "synthorg_ontology_get_relationships"
    try:
        entity_id = _require_str(arguments, "entity_id")
        result = await ontology_facade_service_of(app_state).get_relationships(
            entity_id,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(r) for r in result])


async def _ontology_search(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Search ontology entities by query string.

    Returns:
        Resulting string.
    """
    tool = "synthorg_ontology_search"
    try:
        query = _require_str(arguments, "query")
        result = await ontology_facade_service_of(app_state).search(query)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(r) for r in result])
