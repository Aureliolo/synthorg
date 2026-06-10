"""Startup bootstraps: owner promotion and agent re-hydration.

``_maybe_promote_first_owner`` runs on every boot and promotes the
first user to ``OrgRole.OWNER`` when no owner exists; it is idempotent
once at least one owner is present.

``_maybe_bootstrap_agents`` re-hydrates the runtime agent registry
from persisted config once setup is complete; on first boot setup is
incomplete and bootstrap is deferred to ``POST /setup/complete``.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import normalize_ascii_lowercase_or_default
from synthorg.hr.state import HrStateSlice, agent_registry_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.setup import SETUP_AGENT_BOOTSTRAP_FAILED
from synthorg.persistence._shared import paginate
from synthorg.persistence.state import PersistenceStateSlice, persistence_of
from synthorg.settings.state import (
    SettingsStateSlice,
    config_resolver_of,
    settings_service_of,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.core.auth.models import User

logger = get_logger(__name__)


async def _find_first_user_when_no_owner(app_state: AppState) -> User | None:
    """Return the first user iff no user anywhere holds ``OrgRole.OWNER``.

    Sweeps every page, not just the first ``DEFAULT_LIST_LIMIT`` rows:
    an existing owner positioned past page one must still suppress the
    promotion, otherwise the "idempotent once an owner exists" guarantee
    breaks on installs with more than one page of users. Returns
    ``None`` when an owner exists, when there are no users, or when the
    sweep fails (all three mean "do not promote").

    Returns:
        The ``User`` value when present, ``None`` otherwise.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    from synthorg.core.auth.models import OrgRole  # noqa: PLC0415

    first: User | None = None
    try:
        async for page in paginate(
            lambda limit, offset: persistence_of(app_state).users.list_items(
                limit=limit, offset=offset
            ),
        ):
            if first is None:
                first = page[0]
            if any(OrgRole.OWNER in u.org_roles for u in page):
                return None
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            note="Owner auto-promote skipped: failed to list users",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    return first


async def _maybe_promote_first_owner(app_state: AppState) -> None:
    """Promote the first user to owner if no owner exists.

    Idempotent: once at least one user holds ``OrgRole.OWNER`` the
    function returns without modifying state.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    if app_state.slice(PersistenceStateSlice).backend is None:
        return

    from synthorg.core.auth.models import OrgRole  # noqa: PLC0415

    first = await _find_first_user_when_no_owner(app_state)
    if first is None:
        return
    promoted = first.model_copy(
        update={
            "org_roles": (*first.org_roles, OrgRole.OWNER),
            "updated_at": app_state.clock.now(),
        },
    )
    try:
        await persistence_of(app_state).users.save(promoted)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
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

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    settings_slice = app_state.slice(SettingsStateSlice)
    if not (
        settings_slice.config_resolver is not None
        and app_state.slice(HrStateSlice).agent_registry is not None
        and settings_slice.settings_service is not None
    ):
        logger.debug(
            API_APP_STARTUP,
            note="Agent bootstrap skipped: required services not available",
        )
        return

    try:
        setup_entry = await settings_service_of(app_state).get_entry(
            "api",
            "setup_complete",
        )
        is_complete = normalize_ascii_lowercase_or_default(setup_entry.value) == "true"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
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
            config_resolver=config_resolver_of(app_state),
            agent_registry=agent_registry_of(app_state),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETUP_AGENT_BOOTSTRAP_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
