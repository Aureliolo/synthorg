"""Notification sink factory.

Builds ``NotificationDispatcher`` instances from
``NotificationConfig`` by instantiating the appropriate adapter
for each configured sink.
"""

from synthorg.core.normalization import (
    normalize_ascii_lowercase_or_default,
    parse_comma_list_stripped,
)
from synthorg.core.registry import StrategyRegistry
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.notifications.adapters.console import ConsoleNotificationSink
from synthorg.notifications.config import (
    NotificationConfig,
    NotificationSinkConfig,
    NotificationSinkType,
)
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.protocol import NotificationSink
from synthorg.observability import get_logger
from synthorg.observability.events.notification import (
    NOTIFICATION_SINK_CONFIG_INVALID,
    NOTIFICATION_SINK_DEFAULT_FALLBACK,
    NOTIFICATION_SINK_DISABLED,
    NOTIFICATION_SINK_UNKNOWN_TYPE,
)
from synthorg.settings.bridge_configs import NotificationsBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.network_validator import NetworkPolicy

logger = get_logger(__name__)


def _build_network_policy(params: dict[str, str]) -> NetworkPolicy:
    """Build the SSRF policy for a webhook sink from operator params.

    The default policy is fail-closed (private/internal IPs blocked).
    Operators running a self-hosted ntfy / Slack-compatible receiver on
    an internal address opt in explicitly via a comma-separated
    ``hostname_allowlist`` param so those hosts bypass the private-IP
    block while still being DNS-pinned.

    Returns:
        A ``NetworkPolicy`` carrying the parsed allowlist (empty by
        default).
    """
    allowlist = tuple(parse_comma_list_stripped(params.get("hostname_allowlist", "")))
    return NetworkPolicy(hostname_allowlist=allowlist)


def build_notification_dispatcher(
    config: NotificationConfig,
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
    config_resolver: ConfigResolver | None = None,
    connection_catalog: ConnectionCatalog | None = None,
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
        config_resolver: Optional resolver enabling the
            ``notifications.dispatcher_enabled`` runtime kill-switch.
            ``None`` disables the gate (always-on dispatcher); the
            startup wiring threads the resolver in via the dispatcher
            rebuild in ``_apply_bridge_config``.
        connection_catalog: Optional connection catalog used by the
            Slack sink to resolve its bound ``SLACK`` connection's bot
            token. ``None`` means the Slack sink cannot be built (it is
            skipped with a warning).

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
        sink = _create_notification_sink(
            sink_cfg,
            bridge_config=bridge_config,
            connection_catalog=connection_catalog,
        )
        if sink is not None:
            sinks.append(sink)
    if not sinks:
        sinks.append(ConsoleNotificationSink())
    return NotificationDispatcher(
        sinks=tuple(sinks),
        min_severity=config.min_severity,
        config_resolver=config_resolver,
    )


def _create_console_sink(
    params: dict[str, str],
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
    connection_catalog: ConnectionCatalog | None = None,
) -> NotificationSink | None:
    del params, bridge_config, connection_catalog  # console sink takes no params
    return ConsoleNotificationSink()


def _create_ntfy_sink(
    params: dict[str, str],
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
    connection_catalog: ConnectionCatalog | None = None,
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
        connection_catalog: Unused (ntfy targets a topic URL).

    Returns:
        Configured ntfy sink or ``None`` if topic is missing.
    """
    from synthorg.notifications.adapters.ntfy import (  # noqa: PLC0415
        NtfyNotificationSink,
    )

    del connection_catalog  # ntfy targets a topic URL, not a connection
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
    if not server_url:
        # ntfy_default_url ships empty: degrade honestly rather than
        # building a sink with a blank endpoint (which would raise deep
        # in the adapter). An operator must set notifications.ntfy_default_url
        # or pass a per-sink server_url.
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="ntfy",
            error="server_url is required (set notifications.ntfy_default_url)",
        )
        return None
    token = params.get("token")
    network_policy = _build_network_policy(params)
    if bridge_config is None:
        return NtfyNotificationSink(
            server_url=server_url,
            topic=topic,
            token=token,
            network_policy=network_policy,
        )
    return NtfyNotificationSink(
        server_url=server_url,
        topic=topic,
        token=token,
        webhook_timeout_seconds=bridge_config.ntfy_webhook_timeout_seconds,
        network_policy=network_policy,
    )


def _create_slack_sink(
    params: dict[str, str],
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
    connection_catalog: ConnectionCatalog | None = None,
) -> NotificationSink | None:
    """Create a Slack Web API notification sink.

    Args:
        params: Adapter-specific parameters. Requires ``connection``
            (the bound ``SLACK`` connection name) and ``channel`` (the
            target channel id).
        bridge_config: Optional operator-tuned notification bridge
            config. When provided, threads ``slack_timeout_seconds`` into
            the adapter.
        connection_catalog: Connection catalog used to resolve the bound
            connection's bot token at send time. Required.

    Returns:
        Configured Slack sink, or ``None`` when the catalog is absent or
        ``connection`` / ``channel`` is missing (logged, fail-closed).
    """
    from synthorg.notifications.adapters.slack import (  # noqa: PLC0415
        SlackNotificationSink,
    )

    if connection_catalog is None:
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="slack",
            error="connection catalog unavailable",
        )
        return None
    connection = (params.get("connection") or "").strip()
    channel = (params.get("channel") or "").strip()
    if not connection or not channel:
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type="slack",
            error="both 'connection' and 'channel' are required",
        )
        return None
    if bridge_config is None:
        return SlackNotificationSink(
            connection_catalog=connection_catalog,
            connection_name=connection,
            channel=channel,
        )
    return SlackNotificationSink(
        connection_catalog=connection_catalog,
        connection_name=connection,
        channel=channel,
        timeout_seconds=bridge_config.slack_timeout_seconds,
    )


def _create_email_sink(
    params: dict[str, str],
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
    connection_catalog: ConnectionCatalog | None = None,
) -> NotificationSink | None:
    """Create an email SMTP notification sink.

    Args:
        params: Adapter-specific parameters.
        bridge_config: Optional operator-tuned notification bridge
            config. When provided, threads
            ``email_smtp_timeout_seconds`` into the adapter.
        connection_catalog: Unused (email uses SMTP params).

    Returns:
        Configured email sink or ``None`` if required params
        are missing.
    """
    from synthorg.notifications.adapters.email import (  # noqa: PLC0415
        EmailNotificationSink,
    )

    del connection_catalog  # email uses SMTP params, not a connection
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
    to_addrs = tuple(parse_comma_list_stripped(params.get("to_addrs", "")))
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
    # silently coerced every value that was not literally ``"true"`` to
    # ``False``: alternative truthy spellings (``"on"``, ``"1"``) AND
    # genuine misspellings AND outright garbage were all collapsed to
    # the same insecure default, flipping the intended transport without
    # warning. Accept only the literal ``true``/``false`` strings
    # (case-insensitive, trimmed).
    use_tls_raw = normalize_ascii_lowercase_or_default(
        params.get("use_tls"),
        default="true",
    )
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


_NOTIFICATION_SINK_REGISTRY: StrategyRegistry[NotificationSink | None] = (
    StrategyRegistry(
        {
            NotificationSinkType.CONSOLE.value: _create_console_sink,
            NotificationSinkType.NTFY.value: _create_ntfy_sink,
            NotificationSinkType.SLACK.value: _create_slack_sink,
            NotificationSinkType.EMAIL.value: _create_email_sink,
        },
        kind="notification_sink",
    )
)


def _create_notification_sink(
    cfg: NotificationSinkConfig,
    *,
    bridge_config: NotificationsBridgeConfig | None = None,
    connection_catalog: ConnectionCatalog | None = None,
) -> NotificationSink | None:
    """Instantiate a notification sink from config.

    Args:
        cfg: Single sink configuration.
        bridge_config: Optional operator-tuned bridge settings.
        connection_catalog: Optional catalog threaded to connection-backed
            sinks (Slack).

    Returns:
        Sink instance, or ``None`` if the adapter declines to build
        (invalid params) or the sink type is not registered (forward
        compatibility: emits ``NOTIFICATION_SINK_UNKNOWN_TYPE``).
    """
    try:
        return _NOTIFICATION_SINK_REGISTRY.build(
            cfg.type.value,
            cfg.params,
            bridge_config=bridge_config,
            connection_catalog=connection_catalog,
        )
    except StrategyFactoryNotFoundError:
        # Forward compatibility: a new ``NotificationSinkType`` value
        # added before the factory is updated falls through to None
        # rather than crashing dispatcher construction.
        logger.warning(NOTIFICATION_SINK_UNKNOWN_TYPE, sink_type=cfg.type)
        return None
