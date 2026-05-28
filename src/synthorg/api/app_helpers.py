"""Callback factories and config helpers for the Litestar application.

Small, self-contained helpers that :mod:`synthorg.api.app` composes
while wiring the app -- pulled out so ``app.py`` stays under the size
budget.
"""

import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

# ``ChannelsPlugin`` appears in the public signatures of the helpers
# below. Under PEP 649 lazy annotations, ``typing.get_type_hints()``
# resolves names against module globals at introspection time, so the
# import must be runtime-resolvable; keeping it under TYPE_CHECKING
# would raise ``NameError`` for any caller that introspects the
# annotation surface (Litestar's plugin loader, test harnesses, etc.).
from litestar.channels import (
    ChannelsPlugin,
)

from synthorg.api.channels import (
    CHANNEL_AGENTS,
    CHANNEL_APPROVALS,
    CHANNEL_MEETINGS,
)
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.agent_engine import (
    PersonalityTrimNotifier,
    PersonalityTrimPayload,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_APPROVAL_PUBLISH_FAILED,
    API_WS_SEND_FAILED,
)
from synthorg.observability.events.prompt import (
    PROMPT_PERSONALITY_NOTIFY_FAILED,
)

logger = get_logger(__name__)

_AGENT_WORKSPACES_SUBDIR: Final[str] = "agent-workspaces"
_POSTGRES_VOLUME_DATA_DIR: Final[str] = "/data"


def _make_expire_callback(
    channels_plugin: ChannelsPlugin,
) -> Callable[[ApprovalItem], None]:
    """Create a sync callback that publishes APPROVAL_EXPIRED events.

    The callback is invoked by ``ApprovalStore._check_expiration_locked``
    when lazy expiry transitions an item to EXPIRED.  Best-effort:
    publish errors are logged and swallowed.

    Args:
        channels_plugin: Litestar channels plugin for WebSocket delivery.

    Returns:
        Sync callback accepting an expired ``ApprovalItem``.
    """

    def _on_expire(item: ApprovalItem) -> None:
        """Handle the expire event."""
        event = WsEvent(
            event_type=WsEventType.APPROVAL_EXPIRED,
            channel=CHANNEL_APPROVALS,
            timestamp=datetime.now(UTC),
            payload={
                "approval_id": item.id,
                "status": item.status.value,
                "action_type": item.action_type,
                "risk_level": item.risk_level.value,
            },
        )
        try:
            channels_plugin.publish(
                event.model_dump_json(),
                channels=[CHANNEL_APPROVALS],
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                API_APPROVAL_PUBLISH_FAILED,
                approval_id=item.id,
                event_type=WsEventType.APPROVAL_EXPIRED.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    return _on_expire


def _resolve_artifact_dir_env() -> str:
    """Resolve the postgres-mode artifact directory from the environment.

    Reads ``SYNTHORG_ARTIFACT_DIR`` and falls back to ``/data`` (the
    compose template's mount point) when the variable is unset or
    consists only of whitespace. Rejects relative or traversal paths
    at the env boundary so artifacts cannot end up in the process
    working directory or outside the mounted volume.

    Returns:
        Resulting string.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    artifact_dir_str = os.environ.get("SYNTHORG_ARTIFACT_DIR", "").strip()
    if not artifact_dir_str:
        return _POSTGRES_VOLUME_DATA_DIR
    artifact_path = Path(artifact_dir_str)
    if not artifact_path.is_absolute():
        msg = (
            f"SYNTHORG_ARTIFACT_DIR={artifact_dir_str!r} must be an absolute "
            f"path to avoid writing artifacts to the process working directory"
        )
        logger.warning(API_APP_STARTUP, error=msg, reason="non_absolute_artifact_dir")
        raise ValueError(msg)
    if ".." in artifact_path.parts:
        msg = (
            f"SYNTHORG_ARTIFACT_DIR={artifact_dir_str!r} must not contain '..' "
            f"path traversal segments"
        )
        logger.warning(API_APP_STARTUP, error=msg, reason="artifact_dir_traversal")
        raise ValueError(msg)
    return artifact_dir_str


def resolve_agent_workspace_root_env() -> Path | None:
    """Resolve the agent sandbox workspace root from the environment.

    Returns ``<runtime data dir>/agent-workspaces`` when an env-driven
    deployment is in effect, so the agent's file-system / sandbox tools
    write onto the mounted data volume rather than a process temp dir.
    Returns ``None`` for injected / dev apps (no deployment env vars),
    where :attr:`AppState.agent_workspace_root` keeps its documented
    process-stable temp fallback.

    Precedence mirrors the persistence env resolution:
    ``SYNTHORG_ARTIFACT_DIR`` (explicit), then ``SYNTHORG_DB_PATH``
    parent (sqlite volume), then ``/data`` when only
    ``SYNTHORG_DATABASE_URL`` is set (postgres compose volume).

    Returns:
        The ``Path`` value when present, ``None`` otherwise.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    artifact_dir = os.environ.get("SYNTHORG_ARTIFACT_DIR", "").strip()
    if artifact_dir:
        return Path(_resolve_artifact_dir_env()) / _AGENT_WORKSPACES_SUBDIR
    db_path = os.environ.get("SYNTHORG_DB_PATH", "").strip()
    if db_path:
        db_path_obj = Path(db_path)
        if not db_path_obj.is_absolute():
            msg = (
                f"SYNTHORG_DB_PATH={db_path!r} must be an absolute path when "
                f"deriving the agent workspace root so sandbox writes land on "
                f"the mounted data volume, not the process working directory"
            )
            logger.warning(API_APP_STARTUP, error=msg, reason="non_absolute_db_path")
            raise ValueError(msg)
        return db_path_obj.parent / _AGENT_WORKSPACES_SUBDIR
    if os.environ.get("SYNTHORG_DATABASE_URL", "").strip():
        return Path(_POSTGRES_VOLUME_DATA_DIR) / _AGENT_WORKSPACES_SUBDIR
    return None


def _make_meeting_publisher(
    channels_plugin: ChannelsPlugin,
) -> Callable[[str, dict[str, Any]], None]:
    """Create a sync callback that publishes meeting events to WS.

    Returns:
        ``Callable[[str, dict[str, Any]], None]`` instance.
    """

    def _on_meeting_event(
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        # Construct the WsEvent inside the guarded block: an unknown
        # ``event_name`` raises ``ValueError`` from the enum lookup and
        # must never abort the meeting-path caller. Failures are logged
        # at WARNING and swallowed.
        """Handle the meeting event event."""
        try:
            event = WsEvent(
                event_type=WsEventType(event_name),
                channel=CHANNEL_MEETINGS,
                timestamp=datetime.now(UTC),
                payload=payload,
            )
            channels_plugin.publish(
                event.model_dump_json(),
                channels=[CHANNEL_MEETINGS],
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                API_WS_SEND_FAILED,
                note="Failed to publish meeting WebSocket event",
                event_name=event_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    return _on_meeting_event


def make_personality_trim_notifier(
    channels_plugin: ChannelsPlugin,
) -> PersonalityTrimNotifier:
    """Create an async callback that publishes ``personality.trimmed`` events.

    The returned callback matches the ``PersonalityTrimNotifier`` contract
    and is intended for passing to ``AgentEngine`` via the
    ``personality_trim_notifier`` constructor parameter.

    Returns:
        ``PersonalityTrimNotifier`` instance.
    """

    async def _on_personality_trimmed(payload: PersonalityTrimPayload) -> None:
        """Handle the personality trimmed event."""
        event = WsEvent(
            event_type=WsEventType.PERSONALITY_TRIMMED,
            channel=CHANNEL_AGENTS,
            timestamp=datetime.now(UTC),
            payload=dict(payload),
        )
        try:
            await asyncio.to_thread(
                channels_plugin.publish,
                event.model_dump_json(),
                channels=[CHANNEL_AGENTS],
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PROMPT_PERSONALITY_NOTIFY_FAILED,
                reason="failed to publish personality.trimmed WebSocket event",
                agent_id=payload.get("agent_id"),
                agent_name=payload.get("agent_name"),
                task_id=payload.get("task_id"),
                trim_tier=payload.get("trim_tier"),
                before_tokens=payload.get("before_tokens"),
                after_tokens=payload.get("after_tokens"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    return _on_personality_trimmed
