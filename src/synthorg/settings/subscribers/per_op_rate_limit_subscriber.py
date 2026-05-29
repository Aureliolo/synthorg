"""Per-operation rate-limit settings subscriber.

Reacts to runtime changes of the ``api.per_op_rate_limit_*`` and
``api.per_op_concurrency_*`` settings.  On any watched change the
subscriber rebuilds the corresponding ``PerOpRateLimitConfig`` /
``PerOpConcurrencyConfig`` from the current database values and swaps
it into :class:`AppState` so the next request sees the new policy
without a process restart.

Only the ``enabled`` and ``overrides`` keys are watched -- the
``backend`` key is ``restart_required=True`` (filtered by the
dispatcher) because the store is constructed once at startup and
switching backends is not a hot-reload concern.
"""

import json
import re
from typing import TYPE_CHECKING, Any, Final

from synthorg.config.rate_limits import (
    PerOpConcurrencyConfig,
    PerOpRateLimitConfig,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_NAMESPACE = "api"
_RATE_LIMIT_ENABLED = "per_op_rate_limit_enabled"
_RATE_LIMIT_OVERRIDES = "per_op_rate_limit_overrides"
_CONCURRENCY_ENABLED = "per_op_concurrency_enabled"
_CONCURRENCY_OVERRIDES = "per_op_concurrency_overrides"

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        (_NAMESPACE, _RATE_LIMIT_ENABLED),
        (_NAMESPACE, _RATE_LIMIT_OVERRIDES),
        (_NAMESPACE, _CONCURRENCY_ENABLED),
        (_NAMESPACE, _CONCURRENCY_OVERRIDES),
    }
)
_RATE_LIMIT_OVERRIDE_TUPLE_LEN: Final[int] = 2
# JSON numbers arrive either as Python ``int`` (preferred) or as
# string digits for operators who wrote ``"5"`` instead of ``5`` in
# the settings payload.  We accept both but reject floats, booleans,
# ``"true"``/``"false"``, and decimal strings -- blind ``int(...)``
# would coerce ``True`` to ``1`` and silently truncate ``1.9`` to
# ``1``, either of which would apply a limit the operator did not
# actually configure.
_INT_STRING_PATTERN = re.compile(r"^[+-]?\d+$")


def _strict_int(value: Any, *, field: str) -> int:
    """Coerce ``value`` to ``int`` without silent bool/float widening.

    Accepts a plain ``int`` (excluding ``bool`` -- ``isinstance(True,
    int) is True`` in Python) or a digit string (``"5"`` / ``"-3"``).
    The caller turns the rejection into a swap-failed log so the
    operator sees the specific malformed value instead of the Pydantic
    downstream's generic failure.

    Returns:
        The coerced integer.

    Raises:
        TypeError: If *value* is a ``bool``, a non-digit string, or any
            type other than ``int`` / digit-string.
    """
    if isinstance(value, bool):
        msg = f"{field} must be an integer, got bool {value!r}"
        raise TypeError(msg)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INT_STRING_PATTERN.fullmatch(value):
        return int(value)
    msg = f"{field} must be an integer, got {type(value).__name__} {value!r}"
    raise TypeError(msg)


class PerOpRateLimitSettingsSubscriber:
    """Swap per-op rate-limit / concurrency configs on DB change.

    Holds a reference to :class:`AppState` (where the live configs
    are stored) and :class:`SettingsService` (to read new values).
    On a watched change, reads the full current tuple of settings
    for that guard (``enabled`` + ``overrides``), rebuilds the
    ``PerOpRateLimitConfig`` / ``PerOpConcurrencyConfig`` model, and
    calls the matching AppState swap method.  Validation errors are
    logged via ``SETTINGS_SERVICE_SWAP_FAILED`` and re-raised so the
    dispatcher logs with full subscriber context; the previous
    in-memory config stays in place so the guard keeps working.

    Args:
        app_state: Application state that owns the live configs.
        settings_service: Settings service for reading current values.
    """

    def __init__(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        self._app_state = app_state
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "per-op-rate-limit-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Route the change to the matching rebuild path."""
        if namespace != _NAMESPACE:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected namespace",
            )
            return

        if key in (_RATE_LIMIT_ENABLED, _RATE_LIMIT_OVERRIDES):
            await self._rebuild_rate_limit_config(key)
        elif key in (_CONCURRENCY_ENABLED, _CONCURRENCY_OVERRIDES):
            await self._rebuild_concurrency_config(key)
        else:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected key",
            )

    async def _rebuild_rate_limit_config(self, trigger_key: str) -> None:
        """Rebuild ``PerOpRateLimitConfig`` and swap into AppState."""
        try:
            enabled = await self._read_bool(_RATE_LIMIT_ENABLED)
            overrides_raw = await self._read_json(_RATE_LIMIT_OVERRIDES)
            overrides = self._coerce_rate_limit_overrides(overrides_raw)
            existing = (
                self._app_state.per_op_rate_limit_config
                if self._app_state.has_per_op_rate_limit_config
                else None
            )
            backend = existing.backend if existing is not None else "memory"
            new_config = PerOpRateLimitConfig(
                enabled=enabled,
                backend=backend,
                overrides=overrides,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="per_op_rate_limit_config",
                trigger_key=trigger_key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        self._app_state.swap_per_op_rate_limit_config(new_config)

    async def _rebuild_concurrency_config(self, trigger_key: str) -> None:
        """Rebuild ``PerOpConcurrencyConfig`` and swap into AppState."""
        try:
            enabled = await self._read_bool(_CONCURRENCY_ENABLED)
            overrides_raw = await self._read_json(_CONCURRENCY_OVERRIDES)
            overrides = self._coerce_concurrency_overrides(overrides_raw)
            existing = (
                self._app_state.per_op_concurrency_config
                if self._app_state.has_per_op_concurrency_config
                else None
            )
            backend = existing.backend if existing is not None else "memory"
            new_config = PerOpConcurrencyConfig(
                enabled=enabled,
                backend=backend,
                overrides=overrides,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="per_op_concurrency_config",
                trigger_key=trigger_key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        self._app_state.swap_per_op_concurrency_config(new_config)

    async def _read_bool(self, key: str) -> bool:
        """Read a boolean setting, rejecting anything but ``"true"``/``"false"``.

        Silently treating unrecognised text as ``False`` would let a
        typo disable the guard without the operator realising, so the
        subscriber surfaces the malformed value through
        ``SETTINGS_SERVICE_SWAP_FAILED`` and keeps the existing config
        in place instead.

        Returns:
            ``True`` or ``False`` for a recognised ``"true"`` / ``"false"``
            value (case-insensitive).

        Raises:
            ValueError: If the setting value is anything other than
                ``"true"`` or ``"false"`` (empty string, typo, ``None``,
                ``"yes"``, etc.).
        """
        result = await self._settings_service.get(_NAMESPACE, key)
        raw = str(result.value) if result.value is not None else ""
        normalised = normalize_ascii_lowercase(raw)
        if normalised == "true":
            return True
        if normalised == "false":
            return False
        msg = (
            f"setting api.{key}={raw!r} is not a valid boolean "
            "(expected 'true' or 'false')"
        )
        raise ValueError(msg)

    async def _read_json(self, key: str) -> Any:
        """Read a JSON-typed setting and parse into Python objects.

        Returns:
            The parsed JSON value (``dict`` for object settings, ``{}``
            when the stored value is empty).
        """
        result = await self._settings_service.get(_NAMESPACE, key)
        raw = str(result.value) if result.value is not None else "{}"
        return json.loads(raw) if raw else {}

    @staticmethod
    def _coerce_rate_limit_overrides(
        raw: Any,
    ) -> dict[str, tuple[int, int]]:
        """Coerce a JSON dict into ``PerOpRateLimitConfig.overrides`` shape.

        Expected shape: ``{"op.name": [max_requests, window_seconds]}``.
        JSON arrays arrive as lists; the Pydantic model expects tuples
        so the entries are converted here.  Both components must be
        non-negative (``0`` disables the operation; negatives are
        rejected by the Pydantic validator downstream, but we enforce
        it here too so the ``SETTINGS_SERVICE_SWAP_FAILED`` log
        identifies the offending operator/key directly instead of a
        generic Pydantic error).  The caller turns the rejection into a
        swap-failed log without clobbering the existing config.

        Returns:
            A mapping of operation name to its ``(max_requests,
            window_seconds)`` rate-limit tuple.

        Raises:
            TypeError: If *raw* is not a JSON object, or a component is
                not an integer (via :func:`_strict_int`).
            ValueError: If an entry is not a 2-element array or has a
                negative component.
        """
        if not isinstance(raw, dict):
            msg = (
                "per_op_rate_limit_overrides must be a JSON object, "
                f"got {type(raw).__name__}"
            )
            raise TypeError(msg)
        coerced: dict[str, tuple[int, int]] = {}
        for op_name, value in raw.items():
            if (
                not isinstance(value, (list, tuple))
                or len(value) != _RATE_LIMIT_OVERRIDE_TUPLE_LEN
            ):
                msg = (
                    f"overrides[{op_name!r}] must be a 2-element "
                    "[max_requests, window_seconds] array"
                )
                raise ValueError(msg)
            max_req = _strict_int(
                value[0],
                field=f"overrides[{op_name!r}][0] (max_requests)",
            )
            window = _strict_int(
                value[1],
                field=f"overrides[{op_name!r}][1] (window_seconds)",
            )
            if max_req < 0 or window < 0:
                msg = (
                    f"overrides[{op_name!r}]=[{max_req}, {window}] "
                    "has a negative component; use 0 to disable an "
                    "operation"
                )
                raise ValueError(msg)
            coerced[str(op_name)] = (max_req, window)
        return coerced

    @staticmethod
    def _coerce_concurrency_overrides(raw: Any) -> dict[str, int]:
        """Coerce a JSON dict into ``PerOpConcurrencyConfig.overrides``.

        Values must be non-negative (``0`` disables the operation;
        negatives are rejected).  The Pydantic validator downstream
        also enforces this, but surfacing the violation here makes
        the ``SETTINGS_SERVICE_SWAP_FAILED`` log name the offending
        operation instead of emitting a generic Pydantic traceback.

        Returns:
            A mapping of operation name to its concurrency limit.

        Raises:
            TypeError: If *raw* is not a JSON object, or a value is not
                an integer (via :func:`_strict_int`).
            ValueError: If any value is a negative integer.
        """
        if not isinstance(raw, dict):
            msg = (
                "per_op_concurrency_overrides must be a JSON object, "
                f"got {type(raw).__name__}"
            )
            raise TypeError(msg)
        coerced: dict[str, int] = {}
        for op_name, value in raw.items():
            as_int = _strict_int(
                value,
                field=f"overrides[{op_name!r}]",
            )
            if as_int < 0:
                msg = (
                    f"overrides[{op_name!r}]={as_int} is negative; "
                    "use 0 to disable an operation"
                )
                raise ValueError(msg)
            coerced[str(op_name)] = as_int
        return coerced
