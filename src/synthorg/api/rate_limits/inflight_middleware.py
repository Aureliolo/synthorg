"""Per-operation inflight middleware.

``PerOpConcurrencyMiddleware`` reads a route-handler ``opt`` annotation
(``{"per_op_concurrency": (operation, max_inflight, key_policy)}``)
and caps simultaneous inflight requests for the (operation, subject)
bucket.  Denials raise :class:`ConcurrencyLimitExceededError`, which
flows through the existing RFC 9457 exception handler to a 429
response with ``Retry-After`` set.

Wired innermost in the middleware stack (see ``middleware_factory``)
so auth-populated ``scope["user"]`` is already available and the
permit is held only during actual handler execution.
"""

from typing import Any, Final

from litestar.connection import ASGIConnection
from litestar.enums import ScopeType
from litestar.middleware import ASGIMiddleware
from litestar.types import ASGIApp, Receive, Scope, Send

from synthorg.api.rate_limits._subject import (
    STATE_KEY_INFLIGHT_CONFIG,
    STATE_KEY_INFLIGHT_STORE,
    extract_subject_key,
)
from synthorg.api.rate_limits.inflight_config import (
    PerOpConcurrencyConfig,
)
from synthorg.api.rate_limits.inflight_protocol import InflightStore
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)

# Route-handler ``opt`` key inspected for the annotation tuple.
# Public because callers splat ``per_op_concurrency(...)`` into
# ``opt={}``; the literal must be stable across releases.
OPT_KEY: Final[str] = "per_op_concurrency"
_OPT_TUPLE_LEN: Final[int] = 3


def _read_live_inflight_config(state: Any) -> PerOpConcurrencyConfig | None:
    """Read the current per-op inflight config from app state.

    Primary source is :class:`AppState` (the settings subscriber
    hot-swaps the config there).  Falls back to the Litestar State
    dict key ``per_op_inflight_config`` for unit tests that build
    minimal state without an ``AppState``.  Returns ``None`` when
    neither source has a config (treated as a wiring error at the
    call site).

    Returns:
        The ``PerOpConcurrencyConfig`` value when present, ``None`` otherwise.
    """
    app_state = getattr(state, "app_state", None)
    if app_state is not None and getattr(
        app_state,
        "has_per_op_concurrency_config",
        False,
    ):
        live: PerOpConcurrencyConfig = app_state.per_op_concurrency_config
        return live
    dict_value: PerOpConcurrencyConfig | None = getattr(
        state,
        STATE_KEY_INFLIGHT_CONFIG,
        None,
    )
    return dict_value


def _resolve_wiring(
    state: Any,
    operation: str,
    config: PerOpConcurrencyConfig | None,
) -> tuple[InflightStore, PerOpConcurrencyConfig]:
    """Fetch the inflight store and validate the config snapshot, or raise 503.

    The store lives in the Litestar state dict (built once at startup,
    never swapped).  The config is passed in by the caller as a
    snapshot captured at request start -- re-reading it here would
    open a window where the settings subscriber swaps the config
    mid-request and the enabled flag observed here disagrees with
    the one the master-switch check already observed.  Missing store
    or config is a deployment error, not a per-user throttle: fail
    loud (ERROR log) and closed (503) so misconfigured deployments do
    not ship silently uncapped.

    Returns:
        Tuple of the declared element types.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    store: InflightStore | None = getattr(
        state,
        STATE_KEY_INFLIGHT_STORE,
        None,
    )
    if store is None or config is None:
        logger.error(
            API_APP_STARTUP,
            guard="per_op_concurrency",
            operation=operation,
            missing_store=store is None,
            missing_config=config is None,
            error=(
                "per-op inflight limiter not wired; refusing request "
                "to avoid silently uncapped endpoints"
            ),
        )
        msg = (
            f"Inflight guard for operation {operation!r} is not wired. "
            "This is a deployment error; see logs for context."
        )
        raise ServiceUnavailableError(msg)
    return store, config


class PerOpConcurrencyMiddleware(ASGIMiddleware):
    """Wrap handlers that declare ``opt[per_op_concurrency] = (...)``.

    No-op for HTTP requests whose route handler lacks the annotation;
    non-HTTP scopes are filtered out by the ``scopes`` class attribute.
    When the annotation is present: read the inflight store + config
    from app state, resolve the subject key, attempt to acquire a
    permit via the store's async context manager, and run the inner
    app under the permit.
    """

    # Only intercept HTTP scopes; websockets and ASGI-raw scopes bypass
    # entirely so a websocket handler's ``opt`` (with a different shape)
    # cannot accidentally match the per_op_concurrency key.
    scopes: tuple[ScopeType, ...] = (ScopeType.HTTP,)

    async def handle(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        next_app: ASGIApp,
    ) -> None:
        """Dispatch: permit-wrap HTTP requests that opted in.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        route_handler = scope.get("route_handler")
        opt = getattr(route_handler, "opt", None) or {}
        policy = opt.get(OPT_KEY)
        if policy is None:
            await next_app(scope, receive, send)
            return

        # Defensive: a route handler may have been annotated with a
        # malformed ``opt[per_op_concurrency]`` via typo or a future
        # refactor.  Without this check a bad annotation would raise a
        # generic ``ValueError`` / ``TypeError`` from tuple unpacking
        # and surface as a 500.  Fail closed (503) with an actionable
        # log so the deployment error is visible instead of the
        # endpoint silently running uncapped after exception handling.
        #
        # Ordering: first confirm ``policy`` is a tuple/list of the
        # expected arity so subsequent ``policy[0..2]`` indexing is
        # safe -- reading before the shape check could raise
        # ``TypeError`` / ``IndexError`` and surface as a 500.
        shape_invalid = (
            not isinstance(policy, (tuple, list)) or len(policy) != _OPT_TUPLE_LEN
        )
        # Each field check is explicit rather than combined into a
        # single conjunction so subtle bugs stay visible:
        #   * ``isinstance(True, int)`` is True in Python, so we
        #     reject ``bool`` before the int check to avoid
        #     ``max_inflight=False`` sneaking through as "0".
        #   * ``policy[0]`` must equal its ``strip()`` -- a padded
        #     operation name is the same regression ``per_op_concurrency``
        #     factory guards against at the opt-factory layer.
        if shape_invalid:
            op_invalid = True
            max_inflight_invalid = True
            key_invalid = True
        else:
            op_invalid = (
                not isinstance(policy[0], str)
                or policy[0] == ""
                or policy[0] != policy[0].strip()
            )
            max_inflight_invalid = (
                isinstance(policy[1], bool)
                or not isinstance(policy[1], int)
                or policy[1] <= 0
            )
            key_invalid = policy[2] not in ("user", "ip", "user_or_ip")
        if shape_invalid or op_invalid or max_inflight_invalid or key_invalid:
            logger.error(
                API_APP_STARTUP,
                guard="per_op_concurrency",
                error="malformed_opt_per_op_concurrency",
                note=(
                    "opt[per_op_concurrency] must be a 3-tuple of "
                    "(operation: non-empty stripped str, "
                    "max_inflight: positive int (not bool), "
                    "key_policy: Literal['user','ip','user_or_ip'])"
                ),
            )
            msg = (
                "Inflight guard annotation is malformed on this route. "
                "This is a deployment error; see logs for context."
            )
            raise ServiceUnavailableError(msg)

        operation, default_max_inflight, key_policy = policy

        app = scope.get("app")
        state = getattr(app, "state", None) if app is not None else None

        # Snapshot the live config once at request start so the
        # master-switch check and ``_resolve_wiring`` observe the
        # same config object even if the settings subscriber swaps
        # it mid-request.  A disabled config short-circuits the
        # middleware without requiring the store to be wired.
        config_snapshot = _read_live_inflight_config(state)
        if config_snapshot is not None and not config_snapshot.enabled:
            await next_app(scope, receive, send)
            return

        store, config = _resolve_wiring(state, operation, config_snapshot)

        override = config.overrides.get(operation)
        max_inflight = override if override is not None else default_max_inflight
        if max_inflight <= 0:
            # Operator disabled this operation via override (0;
            # negative was rejected at config load).  The
            # deliberately-uncapped state is already audit-logged
            # once per operator change by
            # :class:`PerOpRateLimitSettingsSubscriber`
            # (``SETTINGS_SERVICE_SWAPPED`` INFO on every swap);
            # emitting a per-request WARNING here would flood logs
            # on any hot endpoint the operator chose to uncap.
            await next_app(scope, receive, send)
            return

        connection: ASGIConnection[Any, Any, Any, Any] = ASGIConnection(
            scope,
            receive,
            send,
        )
        subject = extract_subject_key(
            connection,
            key_policy,
            guard_name="per_op_concurrency",
        )
        bucket_key = f"{operation}:{subject}"

        async with store.acquire(bucket_key, max_inflight=max_inflight):
            await next_app(scope, receive, send)
