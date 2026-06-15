# module-kind: code
"""Registry-fetch helpers for the Prometheus label snapshot.

Each fetcher returns ``frozenset()`` when its service is not wired, the
live id/name set on success, or ``None`` on a fetch exception so the
snapshot merge preserves the previous allowlist.
"""

from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.metrics import METRICS_SCRAPE_FAILED
from synthorg.organization.state import OrganizationStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.state import ProvidersStateSlice

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.engine.workflow.definition import WorkflowDefinition

logger = get_logger(__name__)


def agent_ids_from_agents(
    agents: tuple[AgentIdentity, ...] | None,
) -> frozenset[str] | None:
    """Derive the agent-id frozenset from the registry-fetch result.

    Returns ``None`` when *agents* is ``None`` (the registry fetch
    raised) so the snapshot merge can carry the previous allowlist
    forward; returns the (possibly empty) frozenset of stringified
    agent ids otherwise.

    Returns:
        ``None`` when *agents* is ``None`` (fetch failed), otherwise a
        frozenset of stringified agent IDs (possibly empty).
    """
    if agents is None:
        return None
    return frozenset(str(a.id) for a in agents)


async def fetch_workflow_definitions(
    app_state: AppState,
) -> frozenset[str] | None:
    """Pull the active workflow-definition id set from persistence.

    Returns ``frozenset()`` when the repo isn't wired up (the snapshot
    merge treats that as a "successful fetch with zero entries"), the
    real id set on success, or ``None`` on a registry-fetch exception
    so the merge step keeps the previous allowlist.

    Returns:
        ``frozenset()`` when the repo is not wired, the live frozenset of
        definition ID strings on success, or ``None`` on a fetch
        exception so the merge preserves the previous allowlist.
    """
    try:
        persistence = app_state.slice(PersistenceStateSlice).backend
        wf_repo = getattr(persistence, "workflow_definitions", None)
        if wf_repo is None:
            return frozenset()
        from synthorg.persistence._generics import (  # noqa: PLC0415
            DEFAULT_PAGE_SIZE,
        )
        from synthorg.persistence._shared import paginate  # noqa: PLC0415
        from synthorg.persistence.workflow_definition_protocol import (  # noqa: PLC0415
            WorkflowDefinitionFilterSpec,
        )

        definitions: list[WorkflowDefinition] = []
        async for page in paginate(
            lambda limit, offset: wf_repo.query(
                WorkflowDefinitionFilterSpec(), limit=limit, offset=offset
            ),
            page_size=DEFAULT_PAGE_SIZE,
        ):
            definitions.extend(page)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            METRICS_SCRAPE_FAILED,
            exc,
            component="workflow_definition_repo",
        )
        return None
    return frozenset(str(d.id) for d in definitions)


async def fetch_departments(app_state: AppState) -> frozenset[str] | None:
    """Pull the active department-name set from the department service.

    Same return contract as :func:`fetch_workflow_definitions`:
    empty frozenset for "service not wired", real set on success,
    ``None`` on exception so the merge step preserves the previous
    allowlist.

    Returns:
        ``frozenset()`` when the department service is not wired, the
        live frozenset of department-name strings on success, or
        ``None`` on a fetch exception.
    """
    try:
        dept_service = app_state.slice(OrganizationStateSlice).department_service
        if dept_service is None:
            return frozenset()
        records, _ = await dept_service.list_departments()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            METRICS_SCRAPE_FAILED,
            exc,
            component="department_service",
        )
        return None
    return frozenset(str(r.name) for r in records)


def fetch_provider_names(app_state: AppState) -> frozenset[str] | None:
    """Pull the registered provider-name set from the provider registry.

    Same return contract as :func:`fetch_tool_names`: empty frozenset
    when the registry is not wired, the real set on success, ``None``
    on exception so the merge step preserves the previous allowlist.
    The registry is a frozen ``MappingProxyType`` so the read cannot
    raise meaningfully today; wrapped for symmetry with the async
    registry fetchers and so a future async exposure path stays safe.

    Returns:
        ``frozenset()`` when the registry is not wired, the live
        frozenset of registered provider-name strings on success, or
        ``None`` on a fetch exception.
    """
    try:
        registry = app_state.slice(ProvidersStateSlice).registry
        if registry is None:
            return frozenset()
        return frozenset(registry.list_providers())
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        log_exception_redacted(
            logger, METRICS_SCRAPE_FAILED, exc, component="provider_registry"
        )
        return None


async def fetch_tool_names(app_state: AppState) -> frozenset[str] | None:
    """Pull the registered tool-name set from the tool registry.

    Same return contract as :func:`fetch_departments`: empty
    frozenset when the registry is not wired, real set on success,
    ``None`` on exception so the merge step preserves the previous
    allowlist. Synchronous reads from a frozen ``MappingProxyType``
    cannot raise meaningfully today, but the registry exposure path
    may grow async I/O later (plugin lazy-load, MCP server discovery)
    so this is wrapped for symmetry with the other registry fetchers.

    Returns:
        ``frozenset()`` when the tool registry is not wired, the live
        frozenset of registered tool-name strings on success, or
        ``None`` on a fetch exception.
    """
    try:
        registry = getattr(app_state, "tool_registry", None)
        if registry is None:
            return frozenset()
        return frozenset(registry.list_tools())
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # ``fetch_tool_names`` runs inside a ``TaskGroup`` alongside
        # the workflow / department fetchers; an uncaught exception
        # here would cancel its siblings via the structured-concurrency
        # contract and lose their snapshot updates too. Catch broadly,
        # emit a redacted structured error (the helper logs WITHOUT
        # attaching the traceback so frame-locals stay out of the
        # event), and fall back to ``None`` so the merge step preserves
        # the prior tool-name allowlist.
        log_exception_redacted(
            logger, METRICS_SCRAPE_FAILED, exc, component="tool_registry"
        )
        return None
