"""Rate-limit configuration models.

Domain-layer location for the per-operation rate-limit and
inflight-concurrency config models.  Settings subscribers and the API
rate-limit middleware both consume these from the config layer, keeping
the ``settings/`` subsystem free of imports from ``synthorg.api`` (a
prohibited upward dependency).
"""

from typing import ClassVar, Final, Literal, NoReturn, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
    parse_json_int_dict,
    parse_json_int_pair_dict,
)

logger = get_logger(__name__)

_OVERRIDE_TUPLE_LEN: Final[int] = 2


def _warn_and_raise(msg: str, **ctx: object) -> NoReturn:
    """Log ``API_APP_STARTUP`` warning with ``ctx`` then raise ValueError.

    Centralised so both rate-limit and concurrency override validators
    emit operator-facing context the same way before propagating the
    failure as ``ValueError`` (Pydantic wraps it as ``ValidationError``).

    Raises:
        ValueError: Always, carrying ``msg``; this helper never returns.
    """
    logger.warning(API_APP_STARTUP, **ctx, error=msg)
    raise ValueError(msg)


def _check_operation_key(operation: object, override: object) -> None:
    """Reject non-blank-string operation keys before coercion.

    ``NotBlankStr`` only kicks in after coercion; mode="before" runs
    first so without this guard ``None`` / ``42`` / ``""`` keys would
    slip through with a generic Pydantic error instead of the
    operator-context ``API_APP_STARTUP`` warning.
    """
    if not isinstance(operation, str) or not operation.strip():
        msg = f"overrides key {operation!r} must be a non-blank string operation name"
        _warn_and_raise(
            msg,
            operation=str(operation),
            override=str(override),
        )


def _validate_override_pair(operation: object, pair: object) -> None:
    """Validate one rate-limit override entry.

    Checks the operation key, the ``(max_requests, window_seconds)``
    2-tuple shape, and that both components are non-negative integers.

    Raises:
        ValueError: Via :func:`_warn_and_raise` (logs then raises) when
            the key, shape, or integer values are malformed.
    """
    _check_operation_key(operation, pair)
    if not isinstance(pair, (tuple, list)) or len(pair) != _OVERRIDE_TUPLE_LEN:
        msg = (
            f"overrides[{operation!r}]={pair!r} must be a "
            "(max_requests, window_seconds) 2-tuple"
        )
        _warn_and_raise(msg, operation=operation, override=str(pair))
    max_req, window = pair
    # ``isinstance(True, int)`` is True in Python (``bool`` is a subclass
    # of ``int``), so an explicit ``bool`` reject is required to keep
    # ``True`` / ``False`` out of the override values.
    if (
        not isinstance(max_req, int)
        or isinstance(max_req, bool)
        or not isinstance(window, int)
        or isinstance(window, bool)
        or max_req < 0
        or window < 0
    ):
        msg = (
            f"overrides[{operation!r}]={pair!r} must contain non-negative "
            "integers; use 0 to disable an operation"
        )
        _warn_and_raise(msg, operation=operation, override=str(pair))


class PerOpRateLimitConfig(BaseModel):
    """Configuration for the per-operation rate limiter.

    Attributes:
        enabled: Master switch.  When ``False`` the guard becomes a
            no-op and ``acquire`` is never called.
        backend: Discriminator selecting the concrete
            :class:`SlidingWindowStore` strategy.
        overrides: Operator tuning knob.  Maps operation name to
            ``(max_requests, window_seconds)`` tuples that supersede
            the decorator defaults.  Use ``0`` in either position to
            explicitly disable an operation (the guard short-circuits).
            Negative values are invalid and rejected at startup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="enabled",
            namespace=SettingNamespace.API,
            key="per_op_rate_limit_enabled",
            parse=parse_bool,
        ),
        MirrorField(
            field="overrides",
            namespace=SettingNamespace.API,
            key="per_op_rate_limit_overrides",
            parse=parse_json_int_pair_dict,
        ),
    )

    enabled: bool = True
    backend: Literal["memory"] = "memory"
    overrides: dict[NotBlankStr, tuple[int, int]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _validate_override_tuples(cls, data: object) -> object:
        """Reject override tuples with malformed length or negative values.

        Run BEFORE Pydantic coercion so the malformed-length branch is
        actually reachable with operator-facing context: with
        ``mode="after"`` the ``tuple[int, int]`` type would already have
        rejected mis-shaped inputs with a generic ``ValidationError``
        and our log line would never fire.  Zero is allowed and means
        "disable this operation" -- the guard short-circuits when
        either component is ``0``.

        Declared BEFORE ``_apply_mirrors`` in source order. Pydantic v2
        runs ``mode="before"`` validators in REVERSE declaration order,
        so this runs LAST -- after the mirror has populated ``overrides``
        from env -- which is what we want.

        Returns:
            The input ``data`` unchanged once every override passes its
            shape and non-negative-integer checks.
        """
        if not isinstance(data, dict):
            return data
        # Only an absent key bypasses validation (default-factory dict
        # is empty and falls through fine).  An explicit
        # ``"overrides": None`` is operator misconfiguration -- it
        # silently disabled all overrides under the previous early-
        # return logic -- and now gets the same warning + raise
        # treatment as a non-mapping shape.
        if "overrides" not in data:
            return data
        overrides = data["overrides"]
        if not isinstance(overrides, dict):
            msg = (
                f"overrides must be a mapping of operation -> "
                "(max_requests, window_seconds), got "
                f"{type(overrides).__name__}"
            )

            _warn_and_raise(msg, override=str(overrides))
        for operation, pair in overrides.items():
            _validate_override_pair(operation, pair)
        return data

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        return cast("object", apply_settings_mirrors(data, cls._MIRROR_FIELDS))


class PerOpConcurrencyConfig(BaseModel):
    """Configuration for the per-operation inflight limiter.

    Attributes:
        enabled: Master switch.  When ``False`` the middleware becomes
            a no-op and never attempts to acquire permits.
        backend: Discriminator selecting the concrete
            :class:`InflightStore` strategy.
        overrides: Operator tuning knob.  Maps operation name to
            ``max_inflight`` (positive integer) that supersedes the
            decorator defaults.  Use ``0`` to explicitly disable an
            operation (the middleware short-circuits and lets every
            request through).  Negative values are invalid and rejected
            at startup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="enabled",
            namespace=SettingNamespace.API,
            key="per_op_concurrency_enabled",
            parse=parse_bool,
        ),
        MirrorField(
            field="overrides",
            namespace=SettingNamespace.API,
            key="per_op_concurrency_overrides",
            parse=parse_json_int_dict,
        ),
    )

    enabled: bool = True
    # Tighten to the only shipped backend.  A Redis adapter for
    # cross-worker fairness is planned; adding it here must land with
    # both the factory branch and the corresponding settings-enum
    # entry in lockstep so an operator never picks a selectable value
    # the factory raises ``NotImplementedError`` on at app construction.
    backend: Literal["memory"] = "memory"
    overrides: dict[NotBlankStr, int] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _validate_override_values(cls, data: object) -> object:
        """Reject malformed-shape and negative override values.

        Run BEFORE Pydantic coercion so non-int / mis-typed overrides
        surface through this branch with operator-facing context: with
        ``mode="after"`` the ``dict[NotBlankStr, int]`` type would
        already have rejected mis-shaped inputs with a generic
        ``ValidationError`` and our log line would never fire.  Zero
        is allowed and means "disable this operation".

        Declared BEFORE ``_apply_mirrors`` in source order. Pydantic v2
        runs ``mode="before"`` validators in REVERSE declaration order,
        so this runs LAST -- after the mirror has populated ``overrides``
        from env -- which is what we want.

        Returns:
            The input ``data`` unchanged once every override passes its
            shape and non-negative-integer checks.
        """
        if not isinstance(data, dict):
            return data
        # See the matching comment on ``PerOpRateLimitConfig`` above.
        if "overrides" not in data:
            return data
        overrides = data["overrides"]
        if not isinstance(overrides, dict):
            msg = (
                f"overrides must be a mapping of operation -> max_inflight, "
                f"got {type(overrides).__name__}"
            )

            _warn_and_raise(msg, override=str(overrides))
        for operation, value in overrides.items():
            _check_operation_key(operation, value)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                msg = (
                    f"overrides[{operation!r}]={value!r} must be a "
                    "non-negative integer; use 0 to disable an operation"
                )
                _warn_and_raise(msg, operation=operation, override=str(value))
        return data

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        return cast("object", apply_settings_mirrors(data, cls._MIRROR_FIELDS))
