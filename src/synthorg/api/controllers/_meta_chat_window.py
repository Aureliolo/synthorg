"""Live-resolved trailing window for the Chief of Staff chat snapshot.

Sibling of ``meta.py``: resolves ``chief_of_staff.chat_snapshot_window_days``
fresh per chat turn (DB > env > default), falling back to a fixed
default on a resolver outage or missing resolver.
"""

import asyncio
from datetime import timedelta
from typing import Final

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_CHAT_DEPENDENCY_UNAVAILABLE
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)

# Fallback trailing window, used only when no settings resolver is wired
# (anonymous / test boots) or the live setting fails to resolve; mirrors
# that setting's registered default.
_DEFAULT_CHAT_SNAPSHOT_WINDOW_DAYS: Final[int] = 7

# Module-level log-once guard for the settings-resolution fallback: a
# prolonged settings outage must not flood the logs with an identical
# warning on every chat turn.
_chat_window_fallback_logged: bool = False


async def resolve_chat_snapshot_window(app_state: AppState) -> timedelta:
    """Resolve the live chat snapshot window, falling back to the default.

    A settings outage or malformed value must not fail the chat turn;
    the fallback constant keeps the snapshot window bounded. Warnings
    are log-once per run of failures (cleared on recovery).

    Returns:
        The trailing window as a ``timedelta``.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    global _chat_window_fallback_logged  # noqa: PLW0603
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return timedelta(days=_DEFAULT_CHAT_SNAPSHOT_WINDOW_DAYS)
    try:
        days = await config_resolver_of(app_state).get_int(
            SettingNamespace.CHIEF_OF_STAFF.value, "chat_snapshot_window_days"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        if not _chat_window_fallback_logged:
            logger.warning(
                META_CHAT_DEPENDENCY_UNAVAILABLE,
                dependency="chat_snapshot_window_days",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                fallback_days=_DEFAULT_CHAT_SNAPSHOT_WINDOW_DAYS,
            )
            _chat_window_fallback_logged = True
        return timedelta(days=_DEFAULT_CHAT_SNAPSHOT_WINDOW_DAYS)
    _chat_window_fallback_logged = False
    return timedelta(days=days)
