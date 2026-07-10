"""Per-request org-state read model for the Chief of Staff chat.

Sibling of ``meta.py``: builds the real org-state snapshot (in-flight
tasks, active projects, pending approvals) fresh per chat turn from the
live persistence backend and approval store, or returns ``None`` when
that read model cannot be built (persistence disconnected / approval
store unwired). The chat backend renders that ``None`` as an explicit
"cannot see task/project state" answer rather than an idleness
inference. A genuine backend fault during the read propagates (the read
model is fail-loud): the caller's error handling surfaces it instead of
a fabricated answer.
"""

import asyncio
from typing import Final

from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.chief_of_staff.org_state import OrgStateReader, OrgStateSnapshot
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_CHAT_DEPENDENCY_UNAVAILABLE
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)

# Fallback per-section cap, used only when no settings resolver is wired
# (anonymous / test boots) or the live setting fails to resolve; mirrors
# the registered ``chat_org_state_max_items_per_section`` default.
_DEFAULT_MAX_ITEMS_PER_SECTION: Final[int] = 10


async def _resolve_max_items(app_state: AppState) -> int:
    """Resolve the live per-section cap, falling back to the default.

    A settings outage must not fail the chat turn; the fallback keeps the
    org-state sample bounded.

    Returns:
        The per-section record cap.

    Raises:
        CancelledError: Propagated so cancellation tears down promptly.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return _DEFAULT_MAX_ITEMS_PER_SECTION
    try:
        return await config_resolver_of(app_state).get_int(
            SettingNamespace.CHIEF_OF_STAFF.value,
            "chat_org_state_max_items_per_section",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="chat_org_state_max_items_per_section",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback=_DEFAULT_MAX_ITEMS_PER_SECTION,
        )
        return _DEFAULT_MAX_ITEMS_PER_SECTION


async def resolve_chat_org_state(app_state: AppState) -> OrgStateSnapshot | None:
    """Build the org-state read model for a chat turn, or ``None``.

    Returns ``None`` when persistence is disconnected or the approval
    store is unwired, so the chat backend degrades to the "cannot see
    state" answer. A genuine backend fault during the read propagates.

    Returns:
        The org-state snapshot, or ``None`` when the read model is
        unavailable.
    """
    persistence = app_state.slice(PersistenceStateSlice).backend
    store = app_state.slice(ApprovalStateSlice).store
    if persistence is None or not persistence.is_connected or store is None:
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="org_state",
            hint=(
                "Connect a persistence backend and wire the approval store"
                " so the Chief of Staff can see task, project, and approval"
                " state."
            ),
        )
        return None
    reader = OrgStateReader(
        task_repo=persistence.tasks,
        project_repo=persistence.projects,
        approval_store=store,
        max_items_per_section=await _resolve_max_items(app_state),
        clock=app_state.clock,
    )
    return await reader.read()


__all__ = ["resolve_chat_org_state"]
