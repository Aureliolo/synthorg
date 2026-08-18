# module-kind: code
"""Docker resource-limit conversions shared by container launchers.

One place for every subsystem that builds a Docker ``HostConfig`` (the agent
sandbox, the egress sidecar, ephemeral fine-tune stage containers, the MCP
stdio runtime), so the accepted size grammar and the cpu-quota unit cannot
drift between them. Operators write these in the units Docker's own flags
take; the daemon API takes bytes and nano-cpus, and converting in one module
is what keeps a limit meaning the same thing everywhere it is applied.
"""

import math
from typing import Final

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_CPU_LIMIT_INVALID,
    SANDBOX_MEMORY_LIMIT_INVALID,
)

logger = get_logger(__name__)

#: The daemon expresses a cpu quota in billionths of a core.
_NANO_CPUS_PER_CORE: Final[int] = 1_000_000_000


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


def nano_cpus(cores: float) -> int:
    """Convert a cpu quota in cores to the daemon's nano-cpu unit.

    Refuses a non-positive quota, which is the same contract
    :func:`parse_memory_limit` already enforces for sizes and matters more
    here: the daemon reads ``NanoCpus`` of ``0`` as "no limit", so a quota of
    ``"0"`` does not clamp the container to nothing, it removes the ceiling
    entirely. A rounding-to-zero fraction does the same silently.

    Args:
        cores: The quota an operator wrote, in cores (Docker's ``--cpus``).

    Returns:
        The same quota in billionths of a core.

    Raises:
        ValueError: If the quota is not finite, is not positive, or is too
            small to express.
    """
    # Checked before the sign test, because a non-finite value passes it: every
    # comparison against NaN is False, and infinity is genuinely positive. Both
    # then reach the arithmetic below, where NaN raises a bare ValueError with
    # no event logged and infinity raises OverflowError, which is outside the
    # contract this function documents and so is not caught where it is called.
    if not math.isfinite(cores):
        msg = f"Cpu quota must be a finite number, got: {cores!r}"
        logger.warning(
            SANDBOX_CPU_LIMIT_INVALID,
            reason="not_finite",
            error_type=ValueError.__name__,
        )
        raise ValueError(msg)
    if cores <= 0:
        msg = f"Cpu quota must be positive, got: {cores!r}"
        logger.warning(
            SANDBOX_CPU_LIMIT_INVALID,
            reason="non_positive",
            error_type=ValueError.__name__,
        )
        raise ValueError(msg)
    quota = int(cores * _NANO_CPUS_PER_CORE)
    if quota <= 0:
        msg = f"Cpu quota is too small to express in nano-cpus: {cores!r}"
        logger.warning(
            SANDBOX_CPU_LIMIT_INVALID,
            reason="rounds_to_unlimited",
            error_type=ValueError.__name__,
        )
        raise ValueError(msg)
    return quota


__all__ = ["nano_cpus", "parse_memory_limit"]
