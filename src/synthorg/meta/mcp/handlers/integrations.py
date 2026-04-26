"""Integrations domain MCP handlers.

21 tools across MCP catalog, OAuth providers, external clients,
artifacts, and ontology.  Each handler shims through the corresponding
facade on :class:`AppState`; capability gaps raise typed
``not_supported`` via :class:`CapabilityNotSupportedError`.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
    invalid_argument,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    err,
    ok,
    paginate_sequence,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import coerce_pagination, require_arg
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_ARGUMENT_INVALID,
    MCP_HANDLER_CAPABILITY_GAP,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
    MCP_HANDLER_INVOKE_FAILED,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_TY_STRING = "non-blank string"
_TY_UUID = "UUID string"
_TY_LIST = "sequence of strings"
_TY_INT = "non-negative int"


def _log_invalid(tool: str, exc: ArgumentValidationError) -> None:
    """Emit ``MCP_HANDLER_ARGUMENT_INVALID`` at WARNING for client-input errors."""
    logger.warning(
        MCP_HANDLER_ARGUMENT_INVALID,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _log_failed(tool: str, exc: Exception) -> None:
    """Emit ``MCP_HANDLER_INVOKE_FAILED`` at WARNING with safe error context."""
    logger.warning(
        MCP_HANDLER_INVOKE_FAILED,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _log_guardrail(tool: str, exc: GuardrailViolationError) -> None:
    """Emit ``MCP_HANDLER_GUARDRAIL_VIOLATED`` for destructive-op rejections."""
    logger.warning(
        MCP_HANDLER_GUARDRAIL_VIOLATED,
        tool_name=tool,
        violation=exc.violation,
    )


def _map_capability(tool: str, exc: CapabilityNotSupportedError) -> str:
    """Translate a facade-side capability gap into a typed error envelope.

    Emits :data:`MCP_HANDLER_CAPABILITY_GAP` so capability telemetry is
    distinct from invoke failures.
    """
    logger.info(
        MCP_HANDLER_CAPABILITY_GAP,
        tool_name=tool,
        capability=exc.capability,
    )
    return err(exc, domain_code=exc.domain_code)


def _actor_name(actor: AgentIdentity | None) -> NotBlankStr:
    """Return a stable audit identifier, preferring ``actor.id`` over ``name``."""
    if actor is None:
        return NotBlankStr("mcp-anonymous")
    actor_id = getattr(actor, "id", None)
    if actor_id is not None:
        return NotBlankStr(str(actor_id))
    name = getattr(actor, "name", None)
    if isinstance(name, str) and name.strip():
        return NotBlankStr(name)
    return NotBlankStr("mcp-anonymous")


def _get_str(arguments: dict[str, Any], key: str) -> NotBlankStr | None:
    """Extract an optional non-blank string argument."""
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise invalid_argument(key, _TY_STRING)
    return NotBlankStr(raw)


def _require_str(arguments: dict[str, Any], key: str) -> NotBlankStr:
    """Extract a required non-blank string or raise ``ArgumentValidationError``."""
    value = _get_str(arguments, key)
    if value is None:
        raise invalid_argument(key, _TY_STRING)
    return value


def _require_uuid(arguments: dict[str, Any], key: str) -> NotBlankStr:
    """Extract a required UUID-shaped string or raise ``ArgumentValidationError``."""
    value = require_arg(arguments, key, str)
    try:
        UUID(value)
    except ValueError as exc:
        raise invalid_argument(key, _TY_UUID) from exc
    return NotBlankStr(value)


def _get_list_str(arguments: dict[str, Any], key: str) -> tuple[str, ...]:
    """Extract an optional sequence of strings; returns ``()`` when absent."""
    raw = arguments.get(key)
    if raw in (None, ""):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise invalid_argument(key, _TY_LIST)
    for item in raw:
        if not isinstance(item, str):
            raise invalid_argument(key, _TY_LIST)
    return tuple(raw)


def _require_int(arguments: dict[str, Any], key: str) -> int:
    """Extract a required non-negative int (rejects bool) or raise."""
    raw = arguments.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise invalid_argument(key, _TY_INT)
    return raw


def _to_jsonable(value: Any) -> Any:
    """Coerce a Pydantic / ``to_dict`` value into a JSON-serialisable form."""
    dump_fn = getattr(value, "model_dump", None)
    if callable(dump_fn):
        return dump_fn(mode="json")
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return value


# ── mcp_catalog ────────────────────────────────────────────────────


async def _mcp_catalog_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List available MCP catalog entries (paginated)."""
    tool = "synthorg_mcp_catalog_list"
    try:
        offset, limit = coerce_pagination(arguments)
        entries = await app_state.mcp_catalog_facade_service.list_catalog()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    sequence = tuple(entries)
    page, pagination = paginate_sequence(
        sequence,
        offset=offset,
        limit=limit,
        total=len(sequence),
    )
    return ok([_to_jsonable(e) for e in page], pagination=pagination)


async def _mcp_catalog_search(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Search MCP catalog entries by query string."""
    tool = "synthorg_mcp_catalog_search"
    try:
        query = _require_str(arguments, "query")
        entries = await app_state.mcp_catalog_facade_service.search_catalog(query)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(e) for e in entries])


async def _mcp_catalog_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single MCP catalog entry by ID."""
    tool = "synthorg_mcp_catalog_get"
    try:
        entry_id = _require_str(arguments, "entry_id")
        entry = await app_state.mcp_catalog_facade_service.get_catalog_entry(
            entry_id,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    if entry is None:
        return err(
            LookupError(f"MCP catalog entry {entry_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(entry))


async def _mcp_catalog_install(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Install an MCP catalog entry (non-destructive create)."""
    tool = "synthorg_mcp_catalog_install"
    try:
        entry_id = _require_str(arguments, "entry_id")
        result = await app_state.mcp_catalog_facade_service.install_catalog_entry(
            entry_id=entry_id,
            actor_id=_actor_name(actor),
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok(_to_jsonable(result))


async def _mcp_catalog_uninstall(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Uninstall an MCP catalog entry (destructive; enforces guardrails)."""
    tool = "synthorg_mcp_catalog_uninstall"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        installation_id = _require_str(arguments, "installation_id")
        removed = await app_state.mcp_catalog_facade_service.uninstall_catalog_entry(
            installation_id=installation_id,
            actor_id=_actor_name(resolved_actor),
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_DESTRUCTIVE_OP_EXECUTED,
                tool_name=tool,
                actor=_actor_name(resolved_actor),
                reason=reason,
                installation_id=installation_id,
                removed=removed,
            )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok({"removed": removed})


# ── oauth ──────────────────────────────────────────────────────────


async def _oauth_list_providers(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List configured OAuth providers."""
    tool = "synthorg_oauth_list_providers"
    try:
        providers = await app_state.oauth_facade_service.list_providers()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(p) for p in providers])


async def _oauth_configure_provider(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Configure an OAuth provider (creates or updates credentials)."""
    tool = "synthorg_oauth_configure_provider"
    try:
        name = _require_str(arguments, "name")
        client_id = _require_str(arguments, "client_id")
        authorize_url = _require_str(arguments, "authorize_url")
        token_url = _require_str(arguments, "token_url")
        scopes = _get_list_str(arguments, "scopes")
        record = await app_state.oauth_facade_service.configure_provider(
            name=name,
            client_id=client_id,
            authorize_url=authorize_url,
            token_url=token_url,
            scopes=scopes,
            actor_id=_actor_name(actor),
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok(record.to_dict())


async def _oauth_remove_provider(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Remove an OAuth provider (destructive; enforces guardrails)."""
    tool = "synthorg_oauth_remove_provider"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        name = _require_str(arguments, "name")
        removed = await app_state.oauth_facade_service.remove_provider(
            name=name,
            actor_id=_actor_name(resolved_actor),
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_DESTRUCTIVE_OP_EXECUTED,
                tool_name=tool,
                actor=_actor_name(resolved_actor),
                reason=reason,
                provider_name=name,
                removed=removed,
            )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok({"removed": removed})


# ── clients ────────────────────────────────────────────────────────


async def _clients_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List registered client applications."""
    tool = "synthorg_clients_list"
    try:
        offset, limit = coerce_pagination(arguments)
        clients = await app_state.client_facade_service.list_clients()
        page, pagination = paginate_sequence(
            clients,
            offset=offset,
            limit=limit,
            total=len(clients),
        )
        return ok([c.to_dict() for c in page], pagination=pagination)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)


async def _clients_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single client by ID."""
    tool = "synthorg_clients_get"
    try:
        client_id = _require_uuid(arguments, "client_id")
        client = await app_state.client_facade_service.get_client(client_id)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    if client is None:
        return err(
            LookupError(f"Client {client_id} not found"),
            domain_code="not_found",
        )
    return ok(client.to_dict())


async def _clients_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new client application (non-destructive write)."""
    tool = "synthorg_clients_create"
    try:
        name = _require_str(arguments, "name")
        contact_email = _get_str(arguments, "contact_email")
        notes = _get_str(arguments, "notes")
        client = await app_state.client_facade_service.create_client(
            name=name,
            actor_id=_actor_name(actor),
            contact_email=contact_email,
            notes=notes,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok(client.to_dict())


async def _clients_deactivate(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Deactivate a client (destructive; enforces guardrails)."""
    tool = "synthorg_clients_deactivate"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        client_id = _require_uuid(arguments, "client_id")
        deactivated = await app_state.client_facade_service.deactivate_client(
            client_id=client_id,
            actor_id=_actor_name(resolved_actor),
            reason=reason,
        )
        if deactivated:
            logger.info(
                MCP_DESTRUCTIVE_OP_EXECUTED,
                tool_name=tool,
                actor=_actor_name(resolved_actor),
                reason=reason,
                client_id=client_id,
                deactivated=deactivated,
            )
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok({"deactivated": deactivated})


async def _clients_get_satisfaction(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the client satisfaction score (roll-up over recent interactions)."""
    tool = "synthorg_clients_get_satisfaction"
    try:
        client_id = _require_uuid(arguments, "client_id")
        result = await app_state.client_facade_service.get_satisfaction(client_id)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok(dict(result))


# ── artifacts ──────────────────────────────────────────────────────


async def _artifacts_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List recorded artifacts (paginated)."""
    tool = "synthorg_artifacts_list"
    try:
        offset, limit = coerce_pagination(arguments)
        artifacts = await app_state.artifact_facade_service.list_artifacts()
        page, pagination = paginate_sequence(
            artifacts,
            offset=offset,
            limit=limit,
            total=len(artifacts),
        )
        return ok([a.to_dict() for a in page], pagination=pagination)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)


async def _artifacts_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single artifact by ID."""
    tool = "synthorg_artifacts_get"
    try:
        artifact_id = _require_uuid(arguments, "artifact_id")
        artifact = await app_state.artifact_facade_service.get_artifact(artifact_id)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
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
    """Record a new artifact (non-destructive write)."""
    tool = "synthorg_artifacts_create"
    try:
        name = _require_str(arguments, "name")
        content_type = _require_str(arguments, "content_type")
        size_bytes = _require_int(arguments, "size_bytes")
        storage_ref = _require_str(arguments, "storage_ref")
        artifact = await app_state.artifact_facade_service.create_artifact(
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_ref=storage_ref,
            actor_id=_actor_name(actor),
        )
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok(artifact.to_dict())


async def _artifacts_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete an artifact (destructive; enforces guardrails)."""
    tool = "synthorg_artifacts_delete"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        artifact_id = _require_uuid(arguments, "artifact_id")
        removed = await app_state.artifact_facade_service.delete_artifact(
            artifact_id=artifact_id,
            actor_id=_actor_name(resolved_actor),
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_DESTRUCTIVE_OP_EXECUTED,
                tool_name=tool,
                actor=_actor_name(resolved_actor),
                reason=reason,
                artifact_id=artifact_id,
                removed=removed,
            )
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok({"removed": removed})


# ── ontology ───────────────────────────────────────────────────────


async def _ontology_list_entities(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List known ontology entity types."""
    tool = "synthorg_ontology_list_entities"
    try:
        entities = await app_state.ontology_facade_service.list_entities()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(e) for e in entities])


async def _ontology_get_entity(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single ontology entity by ID."""
    tool = "synthorg_ontology_get_entity"
    try:
        entity_id = _require_str(arguments, "entity_id")
        entity = await app_state.ontology_facade_service.get_entity(entity_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
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
    """Return the relationship graph for a single ontology entity."""
    tool = "synthorg_ontology_get_relationships"
    try:
        entity_id = _require_str(arguments, "entity_id")
        result = await app_state.ontology_facade_service.get_relationships(
            entity_id,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(r) for r in result])


async def _ontology_search(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Search ontology entities by query string."""
    tool = "synthorg_ontology_search"
    try:
        query = _require_str(arguments, "query")
        result = await app_state.ontology_facade_service.search(query)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(r) for r in result])


# ── dispatch table ─────────────────────────────────────────────────


INTEGRATION_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_mcp_catalog_list": _mcp_catalog_list,
        "synthorg_mcp_catalog_search": _mcp_catalog_search,
        "synthorg_mcp_catalog_get": _mcp_catalog_get,
        "synthorg_mcp_catalog_install": _mcp_catalog_install,
        "synthorg_mcp_catalog_uninstall": _mcp_catalog_uninstall,
        "synthorg_oauth_list_providers": _oauth_list_providers,
        "synthorg_oauth_configure_provider": _oauth_configure_provider,
        "synthorg_oauth_remove_provider": _oauth_remove_provider,
        "synthorg_clients_list": _clients_list,
        "synthorg_clients_get": _clients_get,
        "synthorg_clients_create": _clients_create,
        "synthorg_clients_deactivate": _clients_deactivate,
        "synthorg_clients_get_satisfaction": _clients_get_satisfaction,
        "synthorg_artifacts_list": _artifacts_list,
        "synthorg_artifacts_get": _artifacts_get,
        "synthorg_artifacts_create": _artifacts_create,
        "synthorg_artifacts_delete": _artifacts_delete,
        "synthorg_ontology_list_entities": _ontology_list_entities,
        "synthorg_ontology_get_entity": _ontology_get_entity,
        "synthorg_ontology_get_relationships": _ontology_get_relationships,
        "synthorg_ontology_search": _ontology_search,
    },
)
