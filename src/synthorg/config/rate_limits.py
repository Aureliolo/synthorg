"""Rate-limit configuration models.

Domain-layer location for the per-operation rate-limit and
inflight-concurrency config models.  Settings subscribers and the API
rate-limit middleware both consume these from the config layer so the
``settings/`` subsystem no longer imports from ``synthorg.api`` (audit-
144 layer violation).
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)

_OVERRIDE_TUPLE_LEN = 2


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

    enabled: bool = True
    backend: Literal["memory"] = "memory"
    overrides: dict[NotBlankStr, tuple[int, int]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _validate_override_tuples(cls, data: Any) -> Any:
        """Reject override tuples with malformed length or negative values.

        Run BEFORE Pydantic coercion so the malformed-length branch is
        actually reachable with operator-facing context: with
        ``mode="after"`` the ``tuple[int, int]`` type would already have
        rejected mis-shaped inputs with a generic ``ValidationError``
        and our log line would never fire.  Zero is allowed and means
        "disable this operation" -- the guard short-circuits when
        either component is ``0``.
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
            logger.warning(API_APP_STARTUP, override=str(overrides), error=msg)
            raise ValueError(msg)  # noqa: TRY004 -- Pydantic wraps ValueError as ValidationError
        for operation, pair in overrides.items():
            if not isinstance(pair, (tuple, list)) or len(pair) != _OVERRIDE_TUPLE_LEN:
                msg = (
                    f"overrides[{operation!r}]={pair!r} must be a "
                    "(max_requests, window_seconds) 2-tuple"
                )
                logger.warning(
                    API_APP_STARTUP,
                    operation=operation,
                    override=str(pair),
                    error=msg,
                )
                raise ValueError(msg)
            max_req, window = pair
            # ``isinstance(True, int)`` is True in Python (``bool`` is a
            # subclass of ``int``), so an explicit ``bool`` reject is
            # required to keep ``True`` / ``False`` out of the override
            # values.  Mirrors PerOpConcurrencyConfig.
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
                logger.warning(
                    API_APP_STARTUP,
                    operation=operation,
                    override=str(pair),
                    error=msg,
                )
                raise ValueError(msg)
        return data


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
    def _validate_override_values(cls, data: Any) -> Any:
        """Reject malformed-shape and negative override values.

        Run BEFORE Pydantic coercion so non-int / mis-typed overrides
        surface through this branch with operator-facing context: with
        ``mode="after"`` the ``dict[NotBlankStr, int]`` type would
        already have rejected mis-shaped inputs with a generic
        ``ValidationError`` and our log line would never fire.  Zero
        is allowed and means "disable this operation".
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
            logger.warning(API_APP_STARTUP, override=str(overrides), error=msg)
            raise ValueError(msg)  # noqa: TRY004 -- Pydantic wraps ValueError as ValidationError
        for operation, value in overrides.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                msg = (
                    f"overrides[{operation!r}]={value!r} must be a "
                    "non-negative integer; use 0 to disable an operation"
                )
                logger.warning(
                    API_APP_STARTUP,
                    operation=operation,
                    override=str(value),
                    error=msg,
                )
                raise ValueError(msg)
        return data
