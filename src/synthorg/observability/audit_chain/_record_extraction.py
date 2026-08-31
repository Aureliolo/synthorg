"""Pure extraction helpers for turning a LogRecord into chain payload fields.

Carved out of ``sink.py`` to keep that module under the project's
500-line code-tier ceiling. Nothing here touches the chain, a signer, or
any I/O -- every function is a pure transform over a stdlib
``logging.LogRecord``.
"""

import hashlib
import logging


def build_binding_payload(
    *,
    tail_hash: str,
    event_data: bytes,
    signature: bytes,
) -> bytes:
    """Return the bytes a TSA should timestamp for an append.

    Including the current tail hash, a digest of the event data, and
    the signature produces a per-append payload that an attacker
    cannot precompute. The resulting TSA token is cryptographically
    bound to both the prior chain state and the specific event being
    appended, so replaying the token on a different append (or a
    different chain) fails hash binding at verification.

    Returns:
        The SHA-256 digest binding the tail hash, event data, and signature.
    """
    hasher = hashlib.sha256()
    hasher.update(tail_hash.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(event_data)
    hasher.update(b"\x00")
    hasher.update(signature)
    return hasher.digest()


def extract_event_dict(record: logging.LogRecord) -> dict[str, object] | None:
    """Return the structlog event_dict bridged onto the record, if any.

    ``structlog.stdlib.ProcessorFormatter.wrap_for_formatter`` sets
    ``record.msg`` to the event_dict (or a ``(event_dict, ...)`` tuple),
    so the structured kwargs a caller passed (``principal``, ``resource``,
    ...) live INSIDE the dict, not as ``record`` attributes. Plain stdlib
    emissions (``logger.info("security.x")``) keep ``msg`` a string and
    carry their structured fields as record attributes via ``extra=`` --
    that path is handled by the ``getattr`` fallback in
    :func:`optional_field`.

    Returns:
        The bridged event_dict, or ``None`` for a plain-string record.
    """
    msg = record.msg
    if isinstance(msg, dict):
        return msg
    if isinstance(msg, tuple) and msg and isinstance(msg[0], dict):
        return msg[0]
    return None


def optional_field(
    record: logging.LogRecord,
    event_dict: dict[str, object] | None,
    name: str,
) -> str | None:
    """Resolve an optional forensic field from the event_dict or record.

    Prefers the structlog event_dict (where ``logger.info(event, **kw)``
    kwargs land) and falls back to a stdlib ``record`` attribute for
    plain ``extra=``-style emissions. Coerces non-string values to ``str``
    to match the ``default=str`` JSON serialisation the chain hashes.

    Returns:
        The field value as a string, or ``None`` when absent on both.
    """
    raw: object = None
    if event_dict is not None:
        raw = event_dict.get(name)
    if raw is None:
        raw = getattr(record, name, None)
    if raw is None:
        return None
    return raw if isinstance(raw, str) else str(raw)


def extract_event_name(record: logging.LogRecord) -> str | None:
    """Return the canonical event name from a stdlib LogRecord.

    Handles three shapes of ``record.msg`` produced by the codebase:

    * ``str`` -- a plain ``logger.info("security.x.y")`` call. The
      string IS the event name.
    * ``dict`` -- a structlog event_dict bridged via
      :func:`structlog.stdlib.LoggerFactory` BEFORE
      ``wrap_for_formatter`` has been applied. The event name lives at
      ``msg["event"]``.
    * ``tuple`` of ``(event_dict, foreign_pre_chain)`` -- a structlog
      record post ``wrap_for_formatter``. The first tuple element is
      the event_dict.

    Returns ``None`` when the shape doesn't match any known pattern; the
    caller treats that as "not a security event" AND logs a warning so
    a future logging-bridge change is visible to operators rather than
    silently dropping security events on the floor.

    Returns:
        The canonical event name, or ``None`` when the shape is unknown.
    """
    msg = record.msg
    if isinstance(msg, str):
        return record.getMessage()
    if isinstance(msg, dict):
        event = msg.get("event")
        return event if isinstance(event, str) else None
    if isinstance(msg, tuple) and msg and isinstance(msg[0], dict):
        event = msg[0].get("event")
        return event if isinstance(event, str) else None
    return None
