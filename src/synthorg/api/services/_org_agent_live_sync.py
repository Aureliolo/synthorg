# module-kind: code
"""Keeping the live roster in step with a committed agent config write.

The config row is what boot reads; the registry is what everything at
runtime asks. Leaving the two apart makes a role grant true only after a
restart, which is when it is least useful: the staffing sweep, gate
selection and ``GET /agents/active`` all read the registry, and an operator
who just granted ``Completion Reviewer`` expects the parked work to move.

Both functions are best-effort by construction, and that is the whole
reason they live together: the config write has already committed by the
time either runs, so an unwired registry, an agent that was never
registered, or a rejected update must not turn a successful mutation into
an error the caller would retry. Boot re-registers from the row regardless.
"""

from collections.abc import Mapping
from typing import Final

from synthorg.api.bootstrap import identity_from_config
from synthorg.config.agent_schema import AgentConfig
from synthorg.core.actor_context import resolve_actor_label
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import AgentAlreadyRegisteredError, AgentNotFoundError
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_AGENT_CREATED, API_AGENT_UPDATED

logger = get_logger(__name__)

#: Committed config fields that are pushed straight onto the live roster
#: entry. ``name`` and ``department`` are immutable on a registered identity
#: (the registry blocks both), so a rename or a departmental move remains a
#: config fact until the agent is registered afresh.
LIVE_SYNC_FIELDS: Final[frozenset[str]] = frozenset({"role", "model", "autonomy_level"})


async def register_on_live_roster(
    registry: AgentRegistryService | None,
    config: AgentConfig,
    *,
    clock: Clock,
) -> None:
    """Put a freshly created agent on the live roster, not just in config.

    Creating an agent wrote only the row, so until the next restart the new
    agent was absent from ``GET /agents/active``, invisible to gate
    selection and to the staffing sweep, and every registry-backed route
    404'd for it.

    Args:
        registry: The live roster, or ``None`` when it is not wired.
        config: The agent row as persisted.
        clock: Clock the derived identity is stamped from.
    """
    if registry is None:
        return
    try:
        await registry.register(identity_from_config(config, clock=clock))
    except AgentAlreadyRegisteredError:
        logger.debug(
            API_AGENT_CREATED,
            agent=config.name,
            note="already on the live roster",
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the config write has committed; a
        # registration fault must not report a create that happened as a
        # failure, and boot re-registers from the row either way.
        reraise_critical(exc)
        logger.warning(
            API_AGENT_CREATED,
            agent=config.name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="created in config but not registered on the live roster",
        )


async def sync_registered_identity(
    registry: AgentRegistryService | None,
    config: AgentConfig,
    updates: Mapping[str, object],
) -> None:
    """Push a committed config change onto the live roster entry.

    Args:
        registry: The live roster, or ``None`` when it is not wired.
        config: The agent row as persisted.
        updates: The fields the mutation changed.
    """
    live = {k: v for k, v in updates.items() if k in LIVE_SYNC_FIELDS}
    if not live or registry is None:
        return
    try:
        await registry.apply_identity_update(
            NotBlankStr(str(config.id)),
            live,
            saved_by=resolve_actor_label("api"),
            # The operator's own PATCH is where a binding is meant to change,
            # so it carries ``model`` onto the live roster; the config row has
            # already committed it and a roster that disagreed until the next
            # restart is the divergence this sync exists to close.
            allow_binding=True,
        )
    except AgentNotFoundError:
        # Not every config row is a live principal: an agent added after boot
        # exists in config until something registers it, and there is nothing
        # to keep in step until then.
        logger.debug(
            API_AGENT_UPDATED,
            agent=config.name,
            note="not on the live roster; config only",
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_AGENT_UPDATED,
            agent=config.name,
            note="live roster not updated; config committed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = [
    "LIVE_SYNC_FIELDS",
    "register_on_live_roster",
    "sync_registered_identity",
]
