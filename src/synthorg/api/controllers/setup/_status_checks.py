# module-kind: code
"""Read-only setup probes: admin presence, completion, agent existence.

Pure lookups used by the setup status controller to decide which
onboarding step the operator still needs. No state mutation; each
probe fails open on non-critical lookup errors so a transient
persistence flake never hard-blocks the status endpoint.
"""

from synthorg.api.controllers.setup_agents import validate_agents_value
from synthorg.core.auth.roles import HumanRole
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_AGENT_INDEX_OUT_OF_RANGE,
    SETUP_STATUS_SETTINGS_DEFAULT_USED,
    SETUP_STATUS_SETTINGS_UNAVAILABLE,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.enums import SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.service_protocol import SettingsServiceProtocol

logger = get_logger(__name__)


def validate_agent_index(
    agent_index: int,
    agents: list[dict[str, object]],
) -> None:
    """Raise ``NotFoundError`` if *agent_index* is out of range.

    Raises:
        NotFoundError: Raised on the corresponding failure path.
    """
    if agent_index < 0 or agent_index >= len(agents):
        if not agents:
            msg = f"Agent index {agent_index} out of range (no agents configured)"
        else:
            msg = f"Agent index {agent_index} out of range (0-{len(agents) - 1})"
        logger.warning(
            SETUP_AGENT_INDEX_OUT_OF_RANGE,
            agent_index=agent_index,
            agent_count=len(agents),
        )
        raise NotFoundError(msg)


async def check_needs_admin(
    persistence: PersistenceBackend,
) -> bool:
    """Return True if no CEO-role user exists.

    Fail-open on non-critical lookup errors; interpreter-critical
    errors propagate via ``reraise_critical``.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    count: int | None = None
    try:
        count = await persistence.users.count_by_role(HumanRole.CEO)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            context="admin_count",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return True
    return count == 0 if count is not None else True


async def check_needs_setup(
    settings_svc: SettingsServiceProtocol,
) -> bool:
    """Return True if setup is still needed (fail-open on error).

    Returns:
        ``True`` or ``False`` reflecting the condition.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        entry = await settings_svc.get_entry(
            "api",
            "setup_complete",
        )
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        return True
    except Exception:  # noqa: BLE001 -- settings best-effort: log and skip
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
        )
        return True
    else:
        return entry.value != "true"


async def check_has_agents(
    settings_svc: SettingsServiceProtocol,
    *,
    strict: bool = False,
) -> bool:
    """Check whether any agents have been explicitly created.

    Args:
        settings_svc: Settings service instance.
        strict: When True, propagate parsing exceptions.

    Returns:
        True if user-created agents exist.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
        Exception: Raised on the corresponding failure path.
    """
    try:
        entry = await settings_svc.get_entry("company", "agents")
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        logger.debug(
            SETUP_STATUS_SETTINGS_DEFAULT_USED,
            setting="agents",
        )
        return False
    except Exception:
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="agents",
        )
        if strict:
            raise
        return False

    if entry.source != SettingSource.DATABASE:
        logger.debug(
            SETUP_STATUS_SETTINGS_DEFAULT_USED,
            setting="agents",
            source=entry.source,
        )
        return False
    if not entry.value:
        return False
    return validate_agents_value(entry.value, strict=strict)
