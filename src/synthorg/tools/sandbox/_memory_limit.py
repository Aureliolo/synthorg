# module-kind: code
"""Docker memory-limit string parsing shared by container launchers.

One parser for every subsystem that builds a Docker ``HostConfig``
(sandbox, sidecar, ephemeral fine-tune stage containers), so the
accepted size grammar cannot drift between them.
"""

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import SANDBOX_MEMORY_LIMIT_INVALID

logger = get_logger(__name__)


def parse_memory_limit(limit: str) -> int:
    """Parse a Docker memory limit string to bytes.

    Supports suffixes ``b``, ``k``, ``m``, ``g`` (case-insensitive),
    matching the grammar the memory-limit settings advertise and
    validate (``'512b'``, ``'64k'``, ``'64m'``, ``'8G'``).

    Args:
        limit: Memory limit string (e.g. ``"512m"``).

    Returns:
        Memory limit in bytes.

    Raises:
        ValueError: If the format is invalid.
    """
    limit_lower = normalize_ascii_lowercase(limit)
    if not limit_lower:
        msg = "Memory limit must not be empty"
        logger.warning(
            SANDBOX_MEMORY_LIMIT_INVALID,
            reason="empty",
            error_type=ValueError.__name__,
        )
        raise ValueError(msg)
    multipliers = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
    try:
        if limit_lower[-1] in multipliers:
            result = int(limit_lower[:-1]) * multipliers[limit_lower[-1]]
        else:
            result = int(limit_lower)
    except ValueError as exc:
        logger.warning(
            SANDBOX_MEMORY_LIMIT_INVALID,
            reason="invalid_format",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Memory limit format is invalid: {limit!r}"
        raise ValueError(msg) from exc
    if result <= 0:
        msg = f"Memory limit must be positive, got: {limit!r}"
        logger.warning(
            SANDBOX_MEMORY_LIMIT_INVALID,
            reason="non_positive",
            error_type=ValueError.__name__,
        )
        raise ValueError(msg)
    return result
