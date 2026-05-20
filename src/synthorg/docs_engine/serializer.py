"""Deterministic JSON serialiser for :class:`LivingDocument`.

The on-disk bytes for a doc are a UTF-8-encoded JSON document with
sorted keys and a fixed two-space indent. Determinism matters because
the project workspace is git-versioned: identical doc state must
produce identical bytes so re-writes that change nothing produce no
git diff (and re-writes that change one block produce a diff localised
to that block).

Bytes are produced via :func:`serialize_doc`; bytes are parsed back to
a model via :func:`deserialize_doc`. The two functions are exact
inverses: ``deserialize_doc(serialize_doc(doc)) == doc``.
"""

import json
from typing import Final

from synthorg.docs_engine.errors import DocValidationError
from synthorg.docs_engine.models import LivingDocument
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docs import DOC_VALIDATION_FAILED

logger = get_logger(__name__)

_JSON_INDENT: Final[int] = 2


def serialize_doc(doc: LivingDocument) -> bytes:
    """Serialise *doc* to deterministic UTF-8 JSON bytes.

    The output uses sorted keys and a fixed two-space indent so the
    git-diff for any single-block edit stays small and localised.

    Args:
        doc: The document to serialise.

    Returns:
        UTF-8 encoded JSON payload terminated by a trailing newline.
        The trailing newline keeps POSIX text-file conventions and
        avoids the "no newline at end of file" git noise.
    """
    payload = doc.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        sort_keys=True,
        indent=_JSON_INDENT,
        ensure_ascii=False,
    )
    return (encoded + "\n").encode("utf-8")


def deserialize_doc(raw: bytes) -> LivingDocument:
    """Parse JSON bytes back into a :class:`LivingDocument`.

    Args:
        raw: UTF-8 encoded JSON payload (typically produced by
            :func:`serialize_doc`, but any spec-conformant JSON works).

    Returns:
        Reconstructed document.

    Raises:
        DocValidationError: If the bytes are not valid UTF-8 JSON or
            do not match the :class:`LivingDocument` schema.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        logger.warning(
            DOC_VALIDATION_FAILED,
            reason="utf8_decode_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "doc bytes are not valid UTF-8"
        raise DocValidationError(msg) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(
            DOC_VALIDATION_FAILED,
            reason="json_parse_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "doc bytes are not valid JSON"
        raise DocValidationError(msg) from exc
    try:
        return LivingDocument.model_validate(payload)
    except ValueError as exc:
        logger.warning(
            DOC_VALIDATION_FAILED,
            reason="schema_validation_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "doc payload does not match LivingDocument schema"
        raise DocValidationError(msg) from exc
