"""Callback factories and config helpers for the Litestar application.

Small, self-contained helpers that :mod:`synthorg.api.app` composes
while wiring the app -- pulled out so ``app.py`` stays under the size
budget.
"""

import asyncio
import os
from collections.abc import Callable  # noqa: TC003
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, get_args
from urllib.parse import unquote, urlparse

from pydantic import SecretStr

from synthorg.api.channels import (
    CHANNEL_AGENTS,
    CHANNEL_APPROVALS,
    CHANNEL_MEETINGS,
)
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.core.approval import ApprovalItem  # noqa: TC001
from synthorg.engine.agent_engine import (  # noqa: TC001
    PersonalityTrimNotifier,
    PersonalityTrimPayload,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_APPROVAL_PUBLISH_FAILED,
    API_WS_SEND_FAILED,
)
from synthorg.observability.events.prompt import (
    PROMPT_PERSONALITY_NOTIFY_FAILED,
)
from synthorg.persistence.config import (
    PostgresConfig,
    PostgresSslMode,
)

if TYPE_CHECKING:
    from litestar.channels import ChannelsPlugin

logger = get_logger(__name__)


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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                API_APPROVAL_PUBLISH_FAILED,
                approval_id=item.id,
                event_type=WsEventType.APPROVAL_EXPIRED.value,
                exc_info=True,
            )

    return _on_expire


def _postgres_config_from_url(db_url: str) -> PostgresConfig:  # noqa: C901
    """Build a PostgresConfig from a libpq-style URL.

    Accepts the canonical form the CLI compose template emits:
    ``postgresql://user:password@host:5432/dbname``. Userinfo,
    hostname, port, and path are URL-decoded so credentials with
    reserved characters survive the round-trip. The parser is strict
    about presence of the user, password, host, and database fields;
    ambiguous URLs are rejected up front so the auto-wire path
    fails fast rather than producing a half-configured backend that
    explodes later under load.

    The default ``ssl_mode`` from PostgresConfig (``"require"``)
    rejects plaintext connections; for local Docker compose where the
    backend talks to Postgres over an internal network without TLS,
    callers can override via ``SYNTHORG_POSTGRES_SSL_MODE`` env var.
    """

    def _fail(msg: str, reason: str, cause: Exception | None = None) -> NoReturn:
        logger.warning(API_APP_STARTUP, error=msg, reason=reason)
        raise ValueError(msg) from cause

    try:
        parsed = urlparse(db_url)
    except ValueError as exc:
        _fail(
            f"SYNTHORG_DATABASE_URL could not be parsed: {exc}",
            "url_parse_failed",
            exc,
        )
    if parsed.query:
        # Silently dropping query options (``?sslmode=disable`` etc.) would
        # let startup proceed with a different connection config than the
        # operator supplied. Route ssl_mode through the env var instead.
        _fail(
            "SYNTHORG_DATABASE_URL must not include query parameters; use "
            "SYNTHORG_POSTGRES_SSL_MODE for ssl_mode overrides",
            "unsupported_query_params",
        )
    if parsed.scheme not in {"postgres", "postgresql"}:
        _fail(
            f"SYNTHORG_DATABASE_URL scheme {parsed.scheme!r} is not "
            f"supported; expected 'postgresql://...'",
            "invalid_scheme",
        )
    try:
        hostname = parsed.hostname
        parsed_port = parsed.port
    except ValueError as exc:
        # ``.port`` raises ``ValueError`` for non-numeric ports or malformed
        # bracketed IPv6 literals; surface that as a configuration error.
        _fail(
            f"SYNTHORG_DATABASE_URL has an invalid host/port: {exc}",
            "invalid_host_port",
            exc,
        )
    if not hostname:
        _fail("SYNTHORG_DATABASE_URL is missing a host component", "missing_host")
    if not parsed.username or not parsed.password:
        _fail(
            "SYNTHORG_DATABASE_URL must include a username and password "
            "(postgresql://user:pass@host:port/db)",
            "missing_credentials",
        )
    database = parsed.path.lstrip("/")
    if not database:
        _fail(
            "SYNTHORG_DATABASE_URL must include a database name in the "
            "path (postgresql://user:pass@host:port/db)",
            "missing_database",
        )

    ssl_override = (os.environ.get("SYNTHORG_POSTGRES_SSL_MODE") or "").strip()
    ssl_kwargs: dict[str, Any] = {}
    if ssl_override:
        valid_modes = set(get_args(PostgresSslMode))
        if ssl_override not in valid_modes:
            _fail(
                f"SYNTHORG_POSTGRES_SSL_MODE={ssl_override!r} is invalid; "
                f"must be one of: {sorted(valid_modes)}",
                "invalid_ssl_mode",
            )
        ssl_kwargs["ssl_mode"] = ssl_override

    return PostgresConfig(
        host=unquote(hostname),
        port=parsed_port or 5432,
        database=unquote(database),
        username=unquote(parsed.username),
        password=SecretStr(unquote(parsed.password)),
        **ssl_kwargs,
    )


def _resolve_artifact_dir_env() -> str:
    """Resolve the postgres-mode artifact directory from the environment.

    Reads ``SYNTHORG_ARTIFACT_DIR`` and falls back to ``/data`` (the
    compose template's mount point) when the variable is unset or
    consists only of whitespace. Rejects relative or traversal paths
    at the env boundary so artifacts cannot end up in the process
    working directory or outside the mounted volume.
    """
    artifact_dir_str = os.environ.get("SYNTHORG_ARTIFACT_DIR", "").strip()
    if not artifact_dir_str:
        return "/data"
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


def _make_meeting_publisher(
    channels_plugin: ChannelsPlugin,
) -> Callable[[str, dict[str, Any]], None]:
    """Create a sync callback that publishes meeting events to WS."""

    def _on_meeting_event(
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        # Construct the WsEvent inside the guarded block: an unknown
        # ``event_name`` raises ``ValueError`` from the enum lookup and
        # must never abort the meeting-path caller. Failures are logged
        # at WARNING and swallowed.
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                API_WS_SEND_FAILED,
                note="Failed to publish meeting WebSocket event",
                event_name=event_name,
                exc_info=True,
            )

    return _on_meeting_event


def make_personality_trim_notifier(
    channels_plugin: ChannelsPlugin,
) -> PersonalityTrimNotifier:
    """Create an async callback that publishes ``personality.trimmed`` events.

    The returned callback matches the ``PersonalityTrimNotifier`` contract
    and is intended for passing to ``AgentEngine`` via the
    ``personality_trim_notifier`` constructor parameter.
    """

    async def _on_personality_trimmed(payload: PersonalityTrimPayload) -> None:
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                PROMPT_PERSONALITY_NOTIFY_FAILED,
                reason="failed to publish personality.trimmed WebSocket event",
                agent_id=payload.get("agent_id"),
                agent_name=payload.get("agent_name"),
                task_id=payload.get("task_id"),
                trim_tier=payload.get("trim_tier"),
                before_tokens=payload.get("before_tokens"),
                after_tokens=payload.get("after_tokens"),
                exc_info=True,
            )

    return _on_personality_trimmed
