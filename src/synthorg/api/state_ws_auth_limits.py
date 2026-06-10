"""WebSocket / auth-revalidation timeout knobs.

Owns the cross-cutting mutable connection-security primitives a frozen
feature slice cannot hold: the WebSocket auth-handshake / per-frame
timeouts and the auth-revalidation sliding-window limits. Composed onto
``AppState`` as ``app_state.ws_auth_limits``; ``_apply_bridge_config``
stages operator-tuned values from the resolver at startup.
"""

import math

from synthorg.observability import get_logger
from synthorg.observability.events.api import API_BRIDGE_CONFIG_REJECTED
from synthorg.settings.bridge_configs import (
    WS_AUTH_TIMEOUT_MAX_SECONDS,
    WS_AUTH_TIMEOUT_MIN_SECONDS,
)

logger = get_logger(__name__)

# Validation bounds for the operator-tunable WS / auth-revalidation
# knobs. These mirror the ``Field(ge=..., le=...)`` bounds on the
# corresponding bridge-config models; centralised here so the check,
# the structured-warning fields, and the error message can't drift.
_FRAME_TIMEOUT_MIN_SECONDS: int = 1
_FRAME_TIMEOUT_MAX_SECONDS: int = 600
_REVALIDATE_WINDOW_MIN_SECONDS: int = 1
_REVALIDATE_WINDOW_MAX_SECONDS: int = 3_600
_REVALIDATE_MAX_FAILURES_MIN: int = 1
_REVALIDATE_MAX_FAILURES_MAX: int = 100

# Built-in defaults applied before ``_apply_bridge_config`` runs, so the
# handlers never reach back through the resolver per connection.
_DEFAULT_AUTH_TIMEOUT_SECONDS: float = 10.0
_DEFAULT_FRAME_TIMEOUT_SECONDS: int = 30
_DEFAULT_REVALIDATE_WINDOW_SECONDS: int = 60
_DEFAULT_REVALIDATE_MAX_FAILURES: int = 5


def _reject_non_int(value: object, *, field: str) -> None:
    """Raise ``TypeError`` (with a structured warning) for non-int settings.

    The WS DoS-prevention setters expect ``int`` values resolved from
    ``ConfigResolver.get_int``; non-int values would otherwise raise
    ``TypeError`` at the bounds comparison without a structured log,
    leaving operators without a clear signal which knob was bad.

    Raises:
        TypeError: Raised on the corresponding failure path.
    """
    # ``isinstance(value, int)`` accepts ``bool`` (since ``bool`` is a
    # subclass of ``int`` in Python); explicitly reject it so flags
    # don't slip through as 0/1.
    if isinstance(value, bool) or not isinstance(value, int):
        logger.warning(
            API_BRIDGE_CONFIG_REJECTED,
            field=field,
            reason="invalid_type",
            provided_type=type(value).__name__,
        )
        msg = f"{field} must be int, got {type(value).__name__}"
        raise TypeError(msg)


def _reject_invalid_auth_timeout(value: float) -> None:
    """Reject a non-finite, non-float, or out-of-range WS auth timeout.

    Bounds mirror the ``ApiBridgeConfig.ws_auth_timeout_seconds`` Pydantic
    field via the shared ``WS_AUTH_TIMEOUT_{MIN,MAX}_SECONDS`` constants.

    Raises:
        TypeError: When *value* is a ``bool`` or any non-numeric type.
        ValueError: When *value* is non-finite or out of range.
    """
    # ``bool`` is an ``int`` subclass, so ``True``/``False`` would
    # otherwise sail through ``math.isfinite`` and the range check;
    # a non-numeric ``value`` (``str``/``None``/...) would raise a raw
    # ``TypeError`` at ``math.isfinite`` below, bypassing the structured
    # warning. Reject both here so every invalid type logs first.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        logger.warning(
            API_BRIDGE_CONFIG_REJECTED,
            field="ws_auth_timeout_seconds",
            reason="invalid_type",
            provided_type=type(value).__name__,
        )
        msg = f"ws_auth_timeout_seconds must be float, got {type(value).__name__}"
        raise TypeError(msg)
    if not math.isfinite(value):
        logger.warning(
            API_BRIDGE_CONFIG_REJECTED,
            field="ws_auth_timeout_seconds",
            reason="non_finite",
            provided_value=repr(value),
        )
        msg = f"ws_auth_timeout_seconds must be finite, got {value!r}"
        raise ValueError(msg)
    if value < WS_AUTH_TIMEOUT_MIN_SECONDS or value > WS_AUTH_TIMEOUT_MAX_SECONDS:
        logger.warning(
            API_BRIDGE_CONFIG_REJECTED,
            field="ws_auth_timeout_seconds",
            reason="out_of_range",
            provided_value=value,
            min_value=WS_AUTH_TIMEOUT_MIN_SECONDS,
            max_value=WS_AUTH_TIMEOUT_MAX_SECONDS,
        )
        msg = (
            "ws_auth_timeout_seconds must be between"
            f" {WS_AUTH_TIMEOUT_MIN_SECONDS} and"
            f" {WS_AUTH_TIMEOUT_MAX_SECONDS} seconds, got {value}"
        )
        raise ValueError(msg)


class WsAuthLimits:
    """WebSocket auth-handshake / per-frame + revalidation timeout knobs.

    Composed onto ``AppState``. Every knob ships a sane built-in default
    so a handler never reaches back through the resolver per connection;
    ``_apply_bridge_config`` stages operator-tuned values at startup.
    """

    __slots__ = (
        "_auth_revalidate_max_failures",
        "_auth_revalidate_window_seconds",
        "_auth_timeout_seconds",
        "_frame_timeout_seconds",
    )

    def __init__(self) -> None:
        """Initialise the timeout knobs to their built-in defaults."""
        self._auth_timeout_seconds: float = _DEFAULT_AUTH_TIMEOUT_SECONDS
        self._frame_timeout_seconds: int = _DEFAULT_FRAME_TIMEOUT_SECONDS
        self._auth_revalidate_window_seconds: int = _DEFAULT_REVALIDATE_WINDOW_SECONDS
        self._auth_revalidate_max_failures: int = _DEFAULT_REVALIDATE_MAX_FAILURES

    @property
    def auth_timeout_seconds(self) -> float:
        """Return the WebSocket first-message auth-handshake timeout.

        Populated by ``_apply_bridge_config`` from
        ``api.ws_auth_timeout_seconds`` (``restart_required=True``, so the
        operator-visible contract is "takes effect at the next restart");
        always has a sane built-in default (10.0 s) so the handler
        never reaches back through the resolver per-connection.  The
        setter validates and accepts repeated calls (no single-shot
        contract) -- tests and subsystems may stage a different value at
        runtime -- so the effective value is whichever validated
        ``set_auth_timeout_seconds`` call ran most recently.

        Returns:
            Resulting numeric value.
        """
        return self._auth_timeout_seconds

    def set_auth_timeout_seconds(self, value: float) -> None:
        """Store a validated WebSocket auth timeout.

        Mirrors the ``set_max_pending_per_user`` pattern used by the
        ticket store: ``_apply_bridge_config`` resolves the setting
        and calls this setter with the validated value at startup,
        which is then read by the ``/ws`` handler.  Validated repeated
        calls are allowed and the latest value wins -- tests monkeypatch
        this freely and no state enforces a single-shot contract.

        Raises:
            TypeError: Raised on the corresponding failure path.
            ValueError: Raised on the corresponding failure path.
        """
        _reject_invalid_auth_timeout(value)
        self._auth_timeout_seconds = value

    @property
    def frame_timeout_seconds(self) -> int:
        """Per-frame WebSocket receive timeout in seconds.

        Bounded by ``[1, 600]``; defaults to 30. Read once at controller
        construction (read_only_post_init), so the value can be staged
        in tests via ``set_frame_timeout_seconds`` without spinning
        the lifecycle.

        Returns:
            Resulting integer.
        """
        return self._frame_timeout_seconds

    def set_frame_timeout_seconds(self, value: int) -> None:
        """Validate + cache the per-frame WebSocket idle timeout.

        Raises:
            TypeError: If ``value`` is not an ``int`` (via ``_reject_non_int``).
            ValueError: Raised on the corresponding failure path.
        """
        _reject_non_int(value, field="ws_frame_timeout_seconds")
        if not (_FRAME_TIMEOUT_MIN_SECONDS <= value <= _FRAME_TIMEOUT_MAX_SECONDS):
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_frame_timeout_seconds",
                reason="out_of_range",
                provided_value=value,
                min_value=_FRAME_TIMEOUT_MIN_SECONDS,
                max_value=_FRAME_TIMEOUT_MAX_SECONDS,
            )
            msg = (
                "ws_frame_timeout_seconds must be between"
                f" {_FRAME_TIMEOUT_MIN_SECONDS} and"
                f" {_FRAME_TIMEOUT_MAX_SECONDS} seconds, got {value}"
            )
            raise ValueError(msg)
        self._frame_timeout_seconds = value

    @property
    def auth_revalidate_window_seconds(self) -> int:
        """Sliding-window length for WS+SSE revalidation failures.

        Returns:
            Resulting integer.
        """
        return self._auth_revalidate_window_seconds

    def set_auth_revalidate_window_seconds(self, value: int) -> None:
        """Validate + cache the revalidation sliding-window length.

        Raises:
            TypeError: If ``value`` is not an ``int`` (via ``_reject_non_int``).
            ValueError: Raised on the corresponding failure path.
        """
        _reject_non_int(value, field="auth_revalidate_window_seconds")
        if not (
            _REVALIDATE_WINDOW_MIN_SECONDS <= value <= _REVALIDATE_WINDOW_MAX_SECONDS
        ):
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="auth_revalidate_window_seconds",
                reason="out_of_range",
                provided_value=value,
                min_value=_REVALIDATE_WINDOW_MIN_SECONDS,
                max_value=_REVALIDATE_WINDOW_MAX_SECONDS,
            )
            msg = (
                "auth_revalidate_window_seconds must be between"
                f" {_REVALIDATE_WINDOW_MIN_SECONDS} and"
                f" {_REVALIDATE_WINDOW_MAX_SECONDS} seconds,"
                f" got {value}"
            )
            raise ValueError(msg)
        self._auth_revalidate_window_seconds = value

    @property
    def auth_revalidate_max_failures(self) -> int:
        """Max WS+SSE revalidation failures admitted in the window.

        Returns:
            Resulting integer.
        """
        return self._auth_revalidate_max_failures

    def set_auth_revalidate_max_failures(self, value: int) -> None:
        """Validate + cache the revalidation max-failures cap.

        Raises:
            TypeError: If ``value`` is not an ``int`` (via ``_reject_non_int``).
            ValueError: Raised on the corresponding failure path.
        """
        _reject_non_int(value, field="auth_revalidate_max_failures")
        if not (_REVALIDATE_MAX_FAILURES_MIN <= value <= _REVALIDATE_MAX_FAILURES_MAX):
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="auth_revalidate_max_failures",
                reason="out_of_range",
                provided_value=value,
                min_value=_REVALIDATE_MAX_FAILURES_MIN,
                max_value=_REVALIDATE_MAX_FAILURES_MAX,
            )
            msg = (
                "auth_revalidate_max_failures must be between"
                f" {_REVALIDATE_MAX_FAILURES_MIN} and"
                f" {_REVALIDATE_MAX_FAILURES_MAX}, got {value}"
            )
            raise ValueError(msg)
        self._auth_revalidate_max_failures = value
