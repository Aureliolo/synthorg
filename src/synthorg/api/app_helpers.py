# module-kind: code
"""Callback factories and config helpers for the Litestar application.

Small, self-contained helpers that :mod:`synthorg.api.app` composes
while wiring the app -- pulled out so ``app.py`` stays under the size
budget.
"""

import asyncio
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

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
    CHANNEL_APPROVALS,
    CHANNEL_COCKPIT,
)
from synthorg.api.controllers.approvals._shared import to_response_without_context
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.intervention import SteeringNotifier
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_APPROVAL_PUBLISH_FAILED,
    API_WS_SEND_FAILED,
)

logger = get_logger(__name__)

_AGENT_WORKSPACES_SUBDIR: Final[str] = "agent-workspaces"
_POSTGRES_VOLUME_DATA_DIR: Final[str] = "/data"


def _make_lifecycle_callback(
    channels_plugin: ChannelsPlugin,
    event_type: WsEventType,
    clock: Clock | None = None,
) -> Callable[[ApprovalItem], None]:
    """Create a sync callback publishing one approval-lifecycle event.

    Both store-level hooks (a new item, a lazily expired one) publish the
    same envelope shape and degrade the same way, so they share one body:
    the only thing that differs is which event type the frame carries.

    The un-enriched projection is deliberate. The hook runs inside a
    synchronous store callback with no request scope, so resolving the
    agent / task / project context is not available to it; the dashboard
    upserts by approval id and fills the context from its own GET.

    Args:
        channels_plugin: Litestar channels plugin for WebSocket delivery.
        event_type: Lifecycle event the frame announces.
        clock: Clock seam for the event timestamp; defaults to
            ``SystemClock`` so tests can inject a ``FakeClock`` for a
            deterministic timestamp.

    Returns:
        Sync callback accepting the ``ApprovalItem`` that changed.
    """
    resolved_clock = clock or SystemClock()

    def _publish(item: ApprovalItem) -> None:
        """Handle one approval-lifecycle transition."""
        now = resolved_clock.now()
        # Build the event inside the guard: the WsEvent payload validator
        # rejects a malformed payload (e.g. a non-string approval_id) at
        # construction, and a store callback must degrade to a logged
        # no-op rather than let that error escape into the store.
        try:
            response = to_response_without_context(item, now=now)
            event = WsEvent(
                event_type=event_type,
                channel=CHANNEL_APPROVALS,
                timestamp=now,
                payload={
                    "approval_id": str(item.id),
                    "status": item.status.value,
                    "approval": response.model_dump(mode="json"),
                },
            )
            channels_plugin.publish(
                event.model_dump_json(),
                channels=[CHANNEL_APPROVALS],
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APPROVAL_PUBLISH_FAILED,
                approval_id=str(item.id),
                event_type=event_type.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    return _publish


def _make_submitted_callback(
    channels_plugin: ChannelsPlugin,
    clock: Clock | None = None,
) -> Callable[[ApprovalItem], None]:
    """Create the store's ``on_add`` hook, publishing APPROVAL_SUBMITTED.

    Announcing from the store rather than from the REST create handler is
    what makes the frame reach every producer. An agent that parks a
    question calls ``store.add`` directly and never touches the endpoint,
    so a handler-level publish left the dashboard's live path unreachable
    for exactly the items an operator most needs to see arrive.

    Args:
        channels_plugin: Litestar channels plugin for WebSocket delivery.
        clock: Clock seam for the event timestamp.

    Returns:
        Sync callback accepting the newly stored ``ApprovalItem``.
    """
    return _make_lifecycle_callback(
        channels_plugin,
        WsEventType.APPROVAL_SUBMITTED,
        clock,
    )


def _make_expire_callback(
    channels_plugin: ChannelsPlugin,
    clock: Clock | None = None,
) -> Callable[[ApprovalItem], None]:
    """Create the store's ``on_expire`` hook, publishing APPROVAL_EXPIRED.

    Invoked by ``ApprovalStore._check_expiration_locked`` when lazy expiry
    transitions an item to EXPIRED.

    Args:
        channels_plugin: Litestar channels plugin for WebSocket delivery.
        clock: Clock seam for the event timestamp.

    Returns:
        Sync callback accepting an expired ``ApprovalItem``.
    """
    return _make_lifecycle_callback(
        channels_plugin,
        WsEventType.APPROVAL_EXPIRED,
        clock,
    )


def _resolve_artifact_dir_env(raw: str | None = None) -> str:
    """Resolve the postgres-mode artifact directory from the environment.

    Reads ``SYNTHORG_ARTIFACT_DIR`` and falls back to ``/data`` (the
    compose template's mount point) when the variable is unset or
    consists only of whitespace. Rejects relative or traversal paths
    at the env boundary so artifacts cannot end up in the process
    working directory or outside the mounted volume.

    Args:
        raw: Pre-read ``SYNTHORG_ARTIFACT_DIR`` value (already stripped).
            Pass it from a caller that has already read the env var to
            avoid a redundant second read; ``None`` reads the env var
            here.

    Returns:
        Resulting string.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    artifact_dir_str = (
        raw if raw is not None else os.environ.get("SYNTHORG_ARTIFACT_DIR", "").strip()
    )
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
        return Path(_resolve_artifact_dir_env(artifact_dir)) / _AGENT_WORKSPACES_SUBDIR
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


def make_steering_notifier(channels_plugin: ChannelsPlugin) -> SteeringNotifier:
    """Create an async notifier that publishes steering events to the cockpit WS.

    The returned callback matches the ``SteeringNotifier`` contract and is
    injected into ``SteeringService``. An ``event_name`` without a matching
    :class:`WsEventType` (e.g. a worker-only steering event) raises
    ``ValueError`` from the enum lookup; that and any publish failure are
    logged at WARNING and swallowed so the steering write path is never
    aborted by the best-effort notify.

    Returns:
        ``SteeringNotifier`` instance.
    """

    async def _on_steering_event(
        event_name: str,
        payload: Mapping[str, object],
    ) -> None:
        """Handle a steering event."""
        try:
            event = WsEvent(
                event_type=WsEventType(event_name),
                channel=CHANNEL_COCKPIT,
                timestamp=datetime.now(UTC),
                payload=dict(payload),
            )
            await asyncio.to_thread(
                channels_plugin.publish,
                event.model_dump_json(),
                channels=[CHANNEL_COCKPIT],
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_WS_SEND_FAILED,
                note="Failed to publish steering WebSocket event",
                event_name=event_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    return _on_steering_event
