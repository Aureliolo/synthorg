"""One-time startup bootstraps: owner promotion and agent re-hydration.

``_maybe_promote_first_owner`` is an idempotent migration that fires
on every boot until at least one user holds ``OrgRole.OWNER``.

``_maybe_bootstrap_agents`` re-hydrates the runtime agent registry
from persisted config once setup is complete; on first boot setup is
incomplete and bootstrap is deferred to ``POST /setup/complete``.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.core.normalization import normalize_ascii_lowercase_or_default
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.setup import SETUP_AGENT_BOOTSTRAP_FAILED

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _maybe_promote_first_owner(app_state: AppState) -> None:
    """Promote the first user to owner if no owner exists.

    This is a one-time idempotent migration that runs on every boot
    until at least one user has the ``OrgRole.OWNER`` role.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    if not app_state.has_persistence:
        return
    try:
        users = await app_state.persistence.users.list_users()
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            note="Owner auto-promote skipped: failed to list users",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    if not users:
        return

    from synthorg.core.auth.models import OrgRole  # noqa: PLC0415

    has_owner = any(OrgRole.OWNER in u.org_roles for u in users)
    if has_owner:
        return

    first = users[0]
    promoted = first.model_copy(
        update={
            "org_roles": (*first.org_roles, OrgRole.OWNER),
            "updated_at": datetime.now(UTC),
        },
    )
    try:
        await app_state.persistence.users.save(promoted)
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            note="Owner auto-promote failed",
            user_id=first.id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(
        API_APP_STARTUP,
        note="Auto-promoted first user to owner",
        user_id=first.id,
        username=first.username,
    )


async def _maybe_bootstrap_agents(app_state: AppState) -> None:
    """Bootstrap agents if setup is complete and services are available.

    On first run, setup isn't complete yet so bootstrap is deferred
    to ``POST /setup/complete``. On subsequent starts, agents are
    loaded from persisted config into the runtime registry.
    """
    if not (
        app_state.has_config_resolver
        and app_state.has_agent_registry
        and app_state.has_settings_service
    ):
        logger.debug(
            API_APP_STARTUP,
            note="Agent bootstrap skipped: required services not available",
        )
        return

    try:
        setup_entry = await app_state.settings_service.get_entry(
            "api",
            "setup_complete",
        )
        is_complete = normalize_ascii_lowercase_or_default(setup_entry.value) == "true"
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            note="Could not read setup_complete setting; skipping agent bootstrap",
            namespace="api",
            key="setup_complete",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        is_complete = False

    if not is_complete:
        logger.debug(
            API_APP_STARTUP,
            note="Agent bootstrap skipped: setup not complete",
        )
        return

    try:
        from synthorg.api.bootstrap import bootstrap_agents  # noqa: PLC0415

        await bootstrap_agents(
            config_resolver=app_state.config_resolver,
            agent_registry=app_state.agent_registry,
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            SETUP_AGENT_BOOTSTRAP_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
