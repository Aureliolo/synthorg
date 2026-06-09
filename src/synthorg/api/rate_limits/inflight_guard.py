"""Per-operation inflight guard opt-factory.

``per_op_concurrency`` returns a dict intended to be splatted into a
Litestar route's ``opt={}`` argument.  The companion
:class:`PerOpConcurrencyMiddleware` inspects ``scope["route_handler"].opt``
for this annotation and enforces the concurrency cap.

Factoring the opt-dict construction into a factory mirrors the
declarative feel of ``per_op_rate_limit(...)`` in the ``guards=[...]``
list and centralises validation of ``operation`` / ``max_inflight`` /
``key`` so malformed annotations fail fast at import time rather than
silently becoming no-ops at request time.
"""

from typing import Final, get_args

from synthorg.api.rate_limits._subject import KeyPolicy
from synthorg.api.rate_limits.inflight_middleware import OPT_KEY
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)

# Runtime view of the ``KeyPolicy`` literal alias so a typo like
# ``key="usr"`` fails at import time rather than surviving to request
# time and silently bucketing the caller under ``ip:...``.  Kept in
# lockstep with the alias in ``_subject.py`` via ``typing.get_args``.
_VALID_KEY_POLICIES: Final[tuple[str, ...]] = get_args(KeyPolicy)


def per_op_concurrency(
    operation: str,
    *,
    max_inflight: int,
    key: KeyPolicy = "user",
) -> dict[str, tuple[str, int, KeyPolicy]]:
    """Build the route-handler ``opt`` annotation for inflight capping.

    Args:
        operation: Stable, human-readable operation name (e.g.
            ``"memory.fine_tune"``).  Used as the bucket-key prefix
            and as the override lookup key in
            :class:`PerOpConcurrencyConfig.overrides`.  Two routes
            that pass the SAME ``operation`` share one bucket -- use
            this to make ``memory.fine_tune_resume`` serialise against
            ``memory.fine_tune``.
        max_inflight: Default maximum concurrent requests per subject.
            Must be positive.  Operators override per deployment via
            config.
        key: Subject keying policy.  Defaults to ``"user"`` -- per-user
            caps are the right default for expensive ops.  ``"ip"`` and
            ``"user_or_ip"`` are supported for parity with the
            sliding-window guard.

    Returns:
        A single-key dict shaped for Litestar's ``opt={}`` argument.
        Splat at the decorator site: ``opt=per_op_concurrency(...)``.

    Raises:
        ValueError: If ``max_inflight`` is not positive or
            ``operation`` is empty.
    """
    # Strip the operation name so ``" memory.fine_tune "`` (whitespace
    # typo) and ``"memory.fine_tune"`` resolve to the same bucket
    # rather than silently creating distinct per-op overrides the
    # operator never asked for.
    stripped_op = operation.strip() if isinstance(operation, str) else operation
    if not isinstance(stripped_op, str) or not stripped_op:
        msg = "operation must be a non-empty string"
        logger.warning(
            API_APP_STARTUP,
            guard="per_op_concurrency",
            operation=operation,
            max_inflight=max_inflight,
            error=msg,
        )
        raise ValueError(msg)
    if max_inflight <= 0:
        msg = "max_inflight must be positive"
        logger.warning(
            API_APP_STARTUP,
            guard="per_op_concurrency",
            operation=stripped_op,
            max_inflight=max_inflight,
            error=msg,
        )
        raise ValueError(msg)
    if key not in _VALID_KEY_POLICIES:
        msg = f"key must be one of {_VALID_KEY_POLICIES!r}, got {key!r}"
        # ``key`` is a forbidden telemetry field (matches the privacy
        # scrubber's ``key|token|secret|...`` allowlist).  Rename to
        # ``key_policy`` so the warning actually reaches the sink.
        logger.warning(
            API_APP_STARTUP,
            guard="per_op_concurrency",
            operation=stripped_op,
            max_inflight=max_inflight,
            key_policy=str(key),
            error=msg,
        )
        raise ValueError(msg)
    return {OPT_KEY: (stripped_op, max_inflight, key)}
