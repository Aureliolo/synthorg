"""ngrok tunnel adapter for local webhook development.

Wraps the ``pyngrok`` library to expose the local API server on a
public URL for receiving webhooks.

``pyngrok`` is a required runtime dependency (declared in
``pyproject.toml`` ``[project.dependencies]``); a missing import
here would be a build / install bug, not a runtime configuration
issue, so the import is unconditional. Operators who do not need
the tunnel feature simply do not call the start endpoint.
"""

import asyncio
import os
from typing import Any, Final

from pyngrok import conf, ngrok  # type: ignore[import-untyped]

from synthorg.integrations.errors import TunnelError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    NGROK_TUNNEL_STARTED,
    TUNNEL_ALREADY_ACTIVE,
    TUNNEL_ERROR,
    TUNNEL_STOPPED,
)

logger = get_logger(__name__)
_DEFAULT_PORT: Final[int] = 8000


class NgrokAdapter:
    """ngrok tunnel provider.

    Exposes the local API port on a public ngrok URL. ``pyngrok``
    is a required runtime dependency; the import is unconditional
    at module level.

    All ngrok calls are blocking, so they are offloaded to a
    worker thread via ``asyncio.to_thread`` to keep the event
    loop responsive.

    Args:
        auth_token_env: Environment variable holding the ngrok auth
            token (optional; free tier works without a token for
            limited use).
        port: Local port to tunnel (default 8000).
    """

    def __init__(
        self,
        *,
        auth_token_env: str = "NGROK_AUTHTOKEN",  # noqa: S107
        port: int = _DEFAULT_PORT,
    ) -> None:
        self._port = port
        # The ngrok auth token is a bootstrap secret read from the
        # process environment at construction time (the sanctioned
        # init-time exception in the configuration-precedence policy:
        # bootstrap secrets are env-only with no settings registry
        # entry, since they have to be available before
        # ``SettingsService`` itself can come up). Reading at
        # ``__init__`` keeps the runtime ``start()`` path off the
        # ``os.environ`` API and means rotating the token requires a
        # fresh adapter instance, which matches how every other
        # bootstrap-credential surface in this codebase behaves.
        self._auth_token: str = os.environ.get(auth_token_env, "").strip()
        self._public_url: str | None = None
        self._tunnel: object | None = None
        # Per ``docs/reference/lifecycle-sync.md``: a dedicated
        # lifecycle lock serialises ``start`` / ``stop``.  No drain
        # timeout / unrestartable flag here because the adapter does
        # not own a background task; it forwards to pyngrok in a
        # worker thread and the lock is sufficient to prevent two
        # ``start()`` calls from racing on the single-tunnel
        # invariant. Eager init: stop() must be safe before start().
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.

    @property
    def has_auth_token(self) -> bool:
        """Whether NGROK_AUTHTOKEN (or the configured env var) is set.

        Surfaced through the tunnel status endpoint so the dashboard
        can tell the operator whether they are on the free tier
        (random URLs, low rate cap) or paid tier.
        """
        return bool(self._auth_token)

    async def start(self) -> str:
        """Start the ngrok tunnel.

        Idempotent: if a tunnel is already active on this adapter the
        existing public URL is returned and the call is logged as a
        no-op. Callers (``mcp_service.connect`` and the tunnel facade)
        treat ``start()`` as a reconnect-safe entry point, so raising
        here would force every caller to wrap the call in a
        try/except just to ignore the already-active case.

        Returns:
            The public URL of the active tunnel.

        Raises:
            TunnelError: If the tunnel fails to start (auth rejected,
                ngrok service down, etc.). ``pyngrok`` itself is a
                required runtime dependency so an ImportError here is
                a build / install bug rather than a runtime concern.
        """
        async with self._lifecycle_lock:
            # ``_public_url`` is the active-tunnel sentinel; it is set
            # in lock-step with ``_tunnel`` below and cleared together
            # in ``stop()``, so a non-None URL is the canonical
            # "tunnel is up" check and avoids a second ``cast``/assert
            # to satisfy the type narrowing.
            if self._public_url is not None:
                # Idempotent reconnect path -- a legitimate caller
                # observing an already-active tunnel is not a failure.
                # Logging at WARNING with ``TUNNEL_ERROR`` would
                # trigger tunnel-failure alerting on every retry.
                logger.info(
                    TUNNEL_ALREADY_ACTIVE,
                    phase="start",
                    port=self._port,
                )
                return self._public_url
            # Build a per-call ``PyngrokConfig`` instead of mutating
            # ``conf.get_default().auth_token``. The default config is
            # process-global; mutating it from one adapter would
            # silently overwrite the auth token any other adapter or
            # caller had previously set, and leaving a blank token in
            # place would cause subsequent unauthenticated calls to
            # silently reuse stale credentials. Per-call config keeps
            # the token instance-local.
            pyngrok_config = (
                conf.PyngrokConfig(auth_token=self._auth_token)
                if self._auth_token
                else conf.PyngrokConfig()
            )

            # Initialise ``tunnel`` to ``None`` BEFORE the try block
            # so the cleanup branch below can reference it
            # unconditionally without ``locals()`` introspection or
            # ``UnboundLocalError`` if ``ngrok.connect`` itself raises.
            # Typed ``Any`` because ``pyngrok`` ships untyped stubs and
            # the returned object exposes ``public_url``.
            tunnel: Any = None
            try:
                tunnel = await asyncio.to_thread(
                    ngrok.connect,
                    self._port,
                    "http",
                    pyngrok_config=pyngrok_config,
                )
                # Materialise the public URL BEFORE assigning the
                # tunnel handle so a converter / attribute-access
                # failure on ``tunnel.public_url`` cannot leave the
                # adapter in a half-started state where ``_tunnel``
                # exists but ``_public_url`` is still ``None``. Such a
                # half-state would later cause ``stop()`` to call
                # ``ngrok.disconnect(None)`` on a tunnel the adapter
                # never fully owned.
                public_url = str(tunnel.public_url)
            except Exception as exc:
                # ngrok auth token env var may be echoed in exception
                # messages; scrub + drop traceback.
                safe_desc = safe_error_description(exc)
                logger.warning(
                    TUNNEL_ERROR,
                    error_type=type(exc).__name__,
                    error=safe_desc,
                )
                # If ``tunnel`` was created but the URL conversion
                # failed afterwards, best-effort disconnect upstream
                # so we don't orphan an open tunnel on the ngrok
                # side. Failures here are logged but not raised --
                # the caller already gets ``TunnelError``.
                if tunnel is not None:
                    try:
                        await asyncio.to_thread(
                            ngrok.disconnect,
                            getattr(tunnel, "public_url", None),
                        )
                    except Exception as cleanup_exc:
                        logger.warning(
                            TUNNEL_ERROR,
                            phase="cleanup",
                            error_type=type(cleanup_exc).__name__,
                            error=safe_error_description(cleanup_exc),
                        )
                msg = f"Failed to start ngrok tunnel: {safe_desc}"
                raise TunnelError(msg) from exc

            self._public_url = public_url
            self._tunnel = tunnel

            # Provider-scoped log only -- the tunnel controller emits
            # the global ``TUNNEL_STARTED`` after this method returns.
            # Emitting both events here would double-count metrics
            # keyed on the global event.
            logger.info(
                NGROK_TUNNEL_STARTED,
                public_url=self._public_url,
                port=self._port,
                note="tunnel exposes localhost publicly",
            )
            return self._public_url

    async def stop(self) -> None:
        """Stop the ngrok tunnel (best-effort cleanup).

        ``stop()`` is a shutdown hook: callers expect it to run during
        teardown without forcing them to catch an exception. If the
        remote disconnect fails we log the scrubbed error but still
        clear the local tunnel handles so the adapter does not hold on
        to stale state -- the ngrok process lifetime is owned upstream
        anyway, and retaining the handle would block subsequent
        ``start()`` calls on this adapter instance.
        """
        async with self._lifecycle_lock:
            if self._tunnel is None:
                return
            try:
                await asyncio.to_thread(ngrok.disconnect, self._public_url)
            except Exception as exc:
                logger.warning(
                    TUNNEL_ERROR,
                    phase="disconnect",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            self._tunnel = None
            self._public_url = None
            logger.info(TUNNEL_STOPPED)

    async def get_url(self) -> str | None:
        """Return the current public URL, or ``None`` if stopped."""
        return self._public_url
