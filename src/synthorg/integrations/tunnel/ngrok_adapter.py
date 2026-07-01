# module-kind: adapter
"""ngrok tunnel adapter.

Wraps the ``pyngrok`` library to expose the local API server on a
public URL for receiving webhooks.

``pyngrok`` is a required runtime dependency (declared in
``pyproject.toml`` ``[project.dependencies]``); a missing import
here would be a build / install bug, not a runtime configuration
issue, so the import is unconditional.

The auth token is resolved fresh at every ``start()``: the dashboard-
managed credential (stored in the encrypted connection catalog and
supplied via :meth:`NgrokAdapter.bind_credential_source`) wins, and
the ``NGROK_AUTHTOKEN`` env var is the headless fallback. ngrok
refuses every session without a token (ERR_NGROK_4018), so a missing
token fails fast with an actionable message instead of spawning the
agent binary.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Final

from pyngrok import conf, ngrok  # type: ignore[import-untyped]

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.errors import TunnelError
from synthorg.integrations.tunnel.protocol import TunnelCredentialKind
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    NGROK_TUNNEL_STARTED,
    TUNNEL_ALREADY_ACTIVE,
    TUNNEL_ERROR,
    TUNNEL_STOPPED,
)

logger = get_logger(__name__)

type TunnelCredentialSource = Callable[[], Awaitable[str | None]]

MISSING_AUTH_MESSAGE: Final[str] = (
    "ngrok requires a (free) account auth token and none is configured;"
    " paste your token on the tunnel card (dashboard.ngrok.com ->"
    " Your Authtoken)."
)


class NgrokAdapter:
    """ngrok tunnel provider.

    All ngrok calls are blocking, so they are offloaded to a worker
    thread via ``asyncio.to_thread`` to keep the event loop responsive.

    Args:
        auth_token_env: Environment variable holding the headless-
            fallback ngrok auth token.
        port: Local port to tunnel.
    """

    def __init__(
        self,
        *,
        auth_token_env: str = "NGROK_AUTHTOKEN",  # noqa: S107
        port: int,
    ) -> None:
        self._port = port
        # The env fallback is a bootstrap secret read from the process
        # environment at construction time (the sanctioned init-time
        # exception in the configuration-precedence policy: bootstrap
        # secrets are env-only with no settings registry entry). The
        # primary source is the dashboard-managed catalog credential
        # bound below, resolved fresh per start().
        self._env_token: str = os.environ.get(auth_token_env, "").strip()
        self._credential_source: TunnelCredentialSource | None = None
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
    def provider_id(self) -> str:
        """Stable machine id (settings enum value)."""
        return "ngrok"

    @property
    def display_name(self) -> str:
        """Human-readable provider name."""
        return "ngrok"

    @property
    def credential_kind(self) -> TunnelCredentialKind:
        """Authenticates with a pasted auth token."""
        return TunnelCredentialKind.TOKEN

    def bind_credential_source(self, source: TunnelCredentialSource) -> None:
        """Bind the dashboard-managed token lookup (catalog-backed)."""
        self._credential_source = source

    async def availability(self) -> tuple[bool, str | None]:
        """Pyngrok is a required dependency, so ngrok is always runnable.

        Returns:
            ``(True, None)``; the missing-token case is a credential
            state, not an availability one.
        """
        return True, None

    async def credential_configured(self) -> bool:
        """Whether a token is resolvable (catalog first, env fallback).

        Returns:
            ``True`` when a non-empty token would be used at start.
        """
        return await self._resolve_token() is not None

    async def _resolve_token(self) -> str | None:
        """Resolve the auth token: dashboard credential, then env.

        Returns:
            The token, or ``None`` when neither source has one.
        """
        if self._credential_source is not None:
            try:
                stored = await self._credential_source()
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    TUNNEL_ERROR,
                    phase="credential_lookup",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                stored = None
            if stored:
                return stored
        return self._env_token or None

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
            TunnelError: If no auth token is configured, or the tunnel
                fails to start (auth rejected, ngrok service down,
                etc.).
        """
        async with self._lifecycle_lock:
            # ``_public_url`` is the active-tunnel sentinel; it is set
            # in lock-step with ``_tunnel`` below and cleared together
            # in ``stop()``, so a non-None URL is the canonical
            # "tunnel is up" check.
            if self._public_url is not None:
                # Idempotent reconnect path -- a legitimate caller
                # observing an already-active tunnel is not a failure.
                logger.info(
                    TUNNEL_ALREADY_ACTIVE,
                    phase="start",
                    port=self._port,
                )
                return self._public_url
            # Fail fast on the guaranteed-doomed case: ngrok refuses
            # every session without an auth token (ERR_NGROK_4018), so
            # spawning the agent would only download the binary, storm
            # the log with critical-level ngrok errors, and return the
            # same failure.
            auth_token = await self._resolve_token()
            if auth_token is None:
                raise TunnelError(MISSING_AUTH_MESSAGE)
            # Build a per-call ``PyngrokConfig`` instead of mutating
            # ``conf.get_default().auth_token``. The default config is
            # process-global; mutating it from one adapter would
            # silently overwrite the auth token any other adapter or
            # caller had previously set. Per-call config keeps the
            # token instance-local.
            pyngrok_config = conf.PyngrokConfig(auth_token=auth_token)

            # Initialise ``tunnel`` to ``None`` BEFORE the try block
            # so the cleanup branch below can reference it
            # unconditionally without ``UnboundLocalError`` if
            # ``ngrok.connect`` itself raises. ``connected`` keeps the
            # untyped ``pyngrok`` handle (the library ships no stubs)
            # just long enough to read ``public_url``.
            tunnel: object = None
            try:
                connected = await asyncio.to_thread(
                    ngrok.connect,
                    str(self._port),
                    "http",
                    pyngrok_config=pyngrok_config,
                )
                # Capture the handle immediately so the cleanup branch
                # can disconnect it even if the ``public_url`` read
                # below raises -- otherwise a converter / attribute
                # failure would orphan an open tunnel on the ngrok side.
                tunnel = connected
                # Materialise the public URL BEFORE assigning the
                # tunnel handle to ``self._tunnel`` so a failure here
                # cannot leave the adapter in a half-started state
                # where ``_tunnel`` exists but ``_public_url`` is still
                # ``None``.
                public_url = str(connected.public_url)
            except Exception as exc:
                reraise_critical(exc)
                # ngrok auth token may be echoed in exception
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
                orphaned_url = getattr(tunnel, "public_url", None)
                if isinstance(orphaned_url, str):
                    try:
                        await asyncio.to_thread(ngrok.disconnect, orphaned_url)
                    except Exception as cleanup_exc:  # noqa: BLE001 -- criticals re-raised
                        reraise_critical(cleanup_exc)
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
            if self._tunnel is None or self._public_url is None:
                return
            try:
                await asyncio.to_thread(ngrok.disconnect, self._public_url)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
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
