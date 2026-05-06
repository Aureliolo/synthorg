"""Notification sink factory.

Builds ``NotificationDispatcher`` instances from
``NotificationConfig`` by instantiating the appropriate adapter
for each configured sink.
"""

from typing import TYPE_CHECKING

from synthorg.notifications.adapters.console import ConsoleNotificationSink
from synthorg.notifications.config import (
    NotificationConfig,
    NotificationSinkConfig,
    NotificationSinkType,
)
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.notification import (
    NOTIFICATION_SINK_CONFIG_INVALID,
    NOTIFICATION_SINK_DEFAULT_FALLBACK,
    NOTIFICATION_SINK_DISABLED,
    NOTIFICATION_SINK_UNKNOWN_TYPE,
)

if TYPE_CHECKING:
    from synthorg.notifications.protocol import NotificationSink
    from synthorg.settings.bridge_configs import NotificationsBridgeConfig

logger = get_logger(__name__)


def build_notification_dispatcher(
    config: NotificationConfig,
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
) -> NotificationDispatcher:
    """Build a ``NotificationDispatcher`` from configuration.

    Always includes a console sink as a fallback if no sinks are
    configured or all configured sinks are disabled.

    Args:
        config: Notification subsystem configuration.
        bridge_config: Optional operator-tuned bridge settings
            (webhook/SMTP timeouts). When ``None``, adapter defaults
            are used. The API startup hook resolves this from
            ``ConfigResolver.get_notifications_bridge_config()`` and
            rebuilds the dispatcher so operator tuning takes effect
            on restart.

    Returns:
        Configured notification dispatcher.
    """
    sinks: list[NotificationSink] = []
    for sink_cfg in config.sinks:
        if not sink_cfg.enabled:
            logger.debug(
                NOTIFICATION_SINK_DISABLED,
                sink_type=sink_cfg.type,
            )
            continue
        sink = _create_notification_sink(sink_cfg, bridge_config=bridge_config)
        if sink is not None:
            sinks.append(sink)
    if not sinks:
        sinks.append(ConsoleNotificationSink())
    return NotificationDispatcher(
        sinks=tuple(sinks),
        min_severity=config.min_severity,
    )


def _create_notification_sink(
    cfg: NotificationSinkConfig,
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
) -> NotificationSink | None:
    """Instantiate a notification sink from config.

    Args:
        cfg: Single sink configuration.
        bridge_config: Optional operator-tuned bridge settings.

    Returns:
        Sink instance or ``None`` for unknown or invalid types.
    """
    sink_type = cfg.type
    params = cfg.params
    if sink_type is NotificationSinkType.CONSOLE:
        return ConsoleNotificationSink()
    if sink_type is NotificationSinkType.NTFY:
        return _create_ntfy_sink(params, bridge_config=bridge_config)
    if sink_type is NotificationSinkType.SLACK:
        return _create_slack_sink(params, bridge_config=bridge_config)
    if sink_type is NotificationSinkType.EMAIL:
        return _create_email_sink(params, bridge_config=bridge_config)
    # Defensive fallback for forward compatibility if new sink
    # types are added to NotificationSinkType before the factory
    # is updated.
    logger.warning(  # type: ignore[unreachable]
        NOTIFICATION_SINK_UNKNOWN_TYPE,
        sink_type=sink_type,
    )
    return None


def _create_ntfy_sink(
    params: dict[str, str],
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
) -> NotificationSink | None:
    """Create an ntfy notification sink.

    Requires ``topic`` in params. The ``server_url`` defaults to
    ``https://ntfy.sh`` when not provided. Returns ``None`` with
    a warning if ``topic`` is missing -- public ntfy.sh topics
    should never be used by default.

    Args:
        params: Adapter-specific parameters.
        bridge_config: Optional operator-tuned notification bridge
            config. When provided, threads
            ``ntfy_webhook_timeout_seconds`` into the adapter.

    Returns:
        Configured ntfy sink or ``None`` if topic is missing.
    """
    from synthorg.notifications.adapters.ntfy import (  # noqa: PLC0415
        NtfyNotificationSink,
    )

    topic = params.get("topic", "")
    if not topic:
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="ntfy",
            error="topic is required",
        )
        return None
    # ``server_url`` falls back to the operator-tunable
    # ``notifications.ntfy_default_url`` setting (carried on
    # ``bridge_config``) so a self-hosted ntfy deployment can avoid
    # leaking topic names to the public ntfy.sh instance.  When
    # bridge_config is unavailable (early boot / test stub) the
    # documented default lives on ``NotificationsBridgeConfig``.
    if bridge_config is not None:
        default_url = bridge_config.ntfy_default_url
    else:
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            NotificationsBridgeConfig,
        )

        default_url = NotificationsBridgeConfig().ntfy_default_url
        # Fallback signal for operators reading boot logs: the runtime
        # bridge config was unavailable, so the documented default
        # mirroring ``notifications.ntfy_default_url`` was used in its
        # place.  Reaching this branch in production means
        # ``NotificationsBridgeConfig`` was not threaded through the
        # caller and the registry override (if any) was bypassed.
        logger.debug(
            NOTIFICATION_SINK_DEFAULT_FALLBACK,
            sink_type="ntfy",
            reason="bridge_config_unavailable",
            default_url=default_url,
        )
    server_url = params.get("server_url") or default_url
    token = params.get("token")
    if bridge_config is None:
        return NtfyNotificationSink(
            server_url=server_url,
            topic=topic,
            token=token,
        )
    return NtfyNotificationSink(
        server_url=server_url,
        topic=topic,
        token=token,
        webhook_timeout_seconds=bridge_config.ntfy_webhook_timeout_seconds,
    )


def _create_slack_sink(
    params: dict[str, str],
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
) -> NotificationSink | None:
    """Create a Slack webhook notification sink.

    Args:
        params: Adapter-specific parameters. Reads
            ``webhook_url``; falls back to
            ``bridge_config.slack_default_webhook_url`` when the
            per-sink value is missing or blank.
        bridge_config: Optional operator-tuned notification bridge
            config. When provided, threads
            ``slack_webhook_timeout_seconds`` into the adapter and
            supplies the namespace-level ``slack_default_webhook_url``
            fallback.

    Returns:
        Configured Slack sink, or ``None`` when neither *params* nor
        the bridge default supplies a webhook URL, or when the
        resolved URL fails outbound-target validation
        (loopback / private IP / non-HTTP scheme).
    """
    from synthorg.notifications.adapters.slack import (  # noqa: PLC0415
        SlackNotificationSink,
    )

    # Treat whitespace-only as blank so the bridge fallback fires
    # instead of letting "   " reach SlackNotificationSink which would
    # then raise ValueError and drop the sink entirely.
    webhook_url = (params.get("webhook_url") or "").strip()
    used_bridge_default = False
    if not webhook_url and bridge_config is not None:
        webhook_url = bridge_config.slack_default_webhook_url
        used_bridge_default = bool(webhook_url)
    if not webhook_url:
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="slack",
            error="webhook_url is required",
        )
        return None
    if used_bridge_default:
        logger.info(
            NOTIFICATION_SINK_DEFAULT_FALLBACK,
            sink_type="slack",
            source="bridge_config.slack_default_webhook_url",
        )
    try:
        if bridge_config is None:
            return SlackNotificationSink(webhook_url=webhook_url)
        return SlackNotificationSink(
            webhook_url=webhook_url,
            webhook_timeout_seconds=bridge_config.slack_webhook_timeout_seconds,
        )
    except MemoryError, RecursionError:
        raise
    except ValueError as exc:
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="slack",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


def _create_email_sink(  # noqa: PLR0911 - each return is a distinct validation guard
    params: dict[str, str],
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
) -> NotificationSink | None:
    """Create an email SMTP notification sink.

    Args:
        params: Adapter-specific parameters.
        bridge_config: Optional operator-tuned notification bridge
            config. When provided, threads
            ``email_smtp_timeout_seconds`` into the adapter.

    Returns:
        Configured email sink or ``None`` if required params
        are missing.
    """
    from synthorg.notifications.adapters.email import (  # noqa: PLC0415
        EmailNotificationSink,
    )

    host = (params.get("host") or "").strip()
    if not host:
        # Treat whitespace-only ("   ") the same as missing; otherwise
        # the adapter only fails at connect time with a cryptic error.
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="email",
            error="host is required",
        )
        return None
    to_addrs = tuple(
        a.strip() for a in params.get("to_addrs", "").split(",") if a.strip()
    )
    if not to_addrs:
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="email",
            error="to_addrs is required",
        )
        return None
    if any("\r" in a or "\n" in a for a in to_addrs):
        # Same CR/LF header-injection guard we apply to ``from_addr``:
        # ``msg["To"] = ...`` would otherwise let an operator with
        # config-edit access inject arbitrary extra headers by splitting
        # across a newline.
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="email",
            error="to_addrs must not contain CR/LF",
        )
        return None
    try:
        port = int(params.get("port", "587"))
    except ValueError:
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="email",
            error=f"invalid port: {params.get('port')!r}",
        )
        return None
    if port < 1 or port > 65535:  # noqa: PLR2004
        # Parses as an int but falls outside the TCP port range; reject
        # at the boundary so delivery-time failures aren't the first
        # signal of misconfiguration.
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="email",
            error=f"invalid port range: {port}",
        )
        return None
    from_addr = (params.get("from_addr") or "").strip()
    if not from_addr:
        # Previously defaulted to ``synthorg@localhost``, which works
        # in dev but is rejected by most production SMTP relays for
        # ambiguous sender hostname. Fail loudly so operators wire a
        # real sender address.
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="email",
            error="from_addr is required",
        )
        return None
    if "\r" in from_addr or "\n" in from_addr:
        # Reject CR/LF before they reach ``msg["From"] = ...``; the
        # stdlib ``email`` package does not auto-sanitize header values
        # so an unchecked newline lets an operator with config-edit
        # access inject arbitrary extra headers (Bcc, Reply-To, ...).
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="email",
            error="from_addr must not contain CR/LF",
        )
        return None
    username = params.get("username")
    password = params.get("password")
    # Strict ``use_tls`` parsing: the previous ``.lower() == "true"`` form
    # silently coerced typos ("yse", "on", "1") to ``False``, which flipped
    # the intended transport without warning. Accept only the literal
    # ``true``/``false`` strings (case-insensitive, trimmed).
    use_tls_raw = (params.get("use_tls") or "true").strip().lower()
    if use_tls_raw not in {"true", "false"}:
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="email",
            error=f"use_tls must be 'true' or 'false'; got {params.get('use_tls')!r}",
        )
        return None
    use_tls = use_tls_raw == "true"
    if bridge_config is None:
        return EmailNotificationSink(
            host=host,
            port=port,
            username=username,
            password=password,
            from_addr=from_addr,
            to_addrs=to_addrs,
            use_tls=use_tls,
        )
    return EmailNotificationSink(
        host=host,
        port=port,
        username=username,
        password=password,
        from_addr=from_addr,
        to_addrs=to_addrs,
        use_tls=use_tls,
        smtp_timeout_seconds=bridge_config.email_smtp_timeout_seconds,
    )
