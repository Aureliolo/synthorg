"""Deterministic JSON serialiser for :class:`BrainEntry`.

The on-disk bytes for a brain entry are a UTF-8-encoded JSON document with
sorted keys and a fixed two-space indent. Determinism matters because the
project workspace is git-versioned: identical entry state must produce identical
bytes so a re-write that changes nothing produces no git diff.

Bytes are produced via :func:`serialize_entry`; bytes are parsed back to a model
via :func:`deserialize_entry`. The two functions are exact inverses:
``deserialize_entry(serialize_entry(entry)) == entry``.
"""

import json
from typing import Final

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.project_brain import BRAIN_ENTRY_VALIDATION_FAILED
from synthorg.project_brain.errors import BrainEntryValidationError
from synthorg.project_brain.models import BrainEntry

logger = get_logger(__name__)

_JSON_INDENT: Final[int] = 2


def serialize_entry(entry: BrainEntry) -> bytes:
    """Serialise *entry* to deterministic UTF-8 JSON bytes.

    The output uses sorted keys and a fixed two-space indent so the git diff for
    any single-field edit stays small and localised.

    Args:
        entry: The brain entry to serialise.

    Returns:
        UTF-8 encoded JSON payload terminated by a trailing newline.
    """
    payload = entry.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        sort_keys=True,
        indent=_JSON_INDENT,
        ensure_ascii=False,
    )
    return (encoded + "\n").encode("utf-8")


def deserialize_entry(raw: bytes) -> BrainEntry:
    """Parse JSON bytes back into a :class:`BrainEntry`.

    Args:
        raw: UTF-8 encoded JSON payload (typically produced by
            :func:`serialize_entry`, but any spec-conformant JSON works).

    Returns:
        Reconstructed brain entry.

    Raises:
        BrainEntryValidationError: If the bytes are not valid UTF-8 JSON or do
            not match the :class:`BrainEntry` schema.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        logger.warning(
            BRAIN_ENTRY_VALIDATION_FAILED,
            reason="utf8_decode_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "brain entry bytes are not valid UTF-8"
        raise BrainEntryValidationError(msg) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(
            BRAIN_ENTRY_VALIDATION_FAILED,
            reason="json_parse_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "brain entry bytes are not valid JSON"
        raise BrainEntryValidationError(msg) from exc
    try:
        return BrainEntry.model_validate(payload)
    except ValueError as exc:
        logger.warning(
            BRAIN_ENTRY_VALIDATION_FAILED,
            reason="schema_validation_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "brain entry payload does not match BrainEntry schema"
        raise BrainEntryValidationError(msg) from exc
