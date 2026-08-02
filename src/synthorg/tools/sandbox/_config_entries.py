# module-kind: code
"""Shape validation for the sandbox config's colon-delimited entry lists.

``allowed_hosts``, ``extra_hosts`` and ``allowed_paths`` are all lists of
``left:right`` strings that differ only in what each half means, so the
splitting, the rejection message and the log-before-raise live here once
rather than three times in the model.
"""

from typing import Final, NoReturn

from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)

MIN_PORT: Final[int] = 1
MAX_PORT: Final[int] = 65535
_COLON_PAIR_PARTS: Final[int] = 2


def reject_entry(field: str, msg: str) -> NoReturn:
    """Log a rejected config entry and raise.

    Raises:
        ValueError: Always, carrying *msg*.
    """
    logger.warning(CONFIG_VALIDATION_FAILED, field=field, reason=msg)
    raise ValueError(msg)


def colon_pair(entry: str, *, field: str, shape: str) -> tuple[str, str]:
    """Split a ``left:right`` entry, rejecting any other shape.

    Returns:
        The left and right halves.

    Raises:
        ValueError: If the entry is not exactly two non-empty halves.
    """
    parts = entry.split(":")
    if len(parts) != _COLON_PAIR_PARTS or not all(parts):
        reject_entry(
            field,
            f"{field} entry {entry!r} must use {shape} (exactly one ':',"
            " neither side empty); IPv6 addresses are not supported",
        )
    return parts[0], parts[1]


def validate_host_port(entry: str, *, field: str) -> None:
    """Validate a ``host:port`` entry, rejecting wildcards and bad ports.

    Only IPv4 addresses and hostnames are supported; the sidecar's
    transparent proxy cannot express an IPv6 destination.

    Raises:
        ValueError: If the entry is malformed or the port is out of range.
    """
    host, port_str = colon_pair(entry, field=field, shape="'host:port'")
    if host == "*":
        reject_entry(
            field, f"host part of {entry!r} must be a hostname or IP, not a wildcard"
        )
    try:
        port = int(port_str)
    except ValueError as exc:
        msg = f"port {port_str!r} in {entry!r} is not a valid integer"
        logger.warning(CONFIG_VALIDATION_FAILED, field=field, reason=msg)
        raise ValueError(msg) from exc
    if port < MIN_PORT or port > MAX_PORT:
        reject_entry(
            field,
            f"port {port} in {entry!r} must be between {MIN_PORT} and {MAX_PORT}",
        )
