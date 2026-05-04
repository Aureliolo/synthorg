r"""Shared JSON extractors for LLM response strings.

LLM responses arrive as free-form text and may contain a JSON object
(or array) wrapped in markdown code fences, surrounded by chatty
prose, or both. The extractors here handle the common shapes:

  1. Plain JSON: ``{"k": "v"}``
  2. Markdown-fenced JSON: `````json\n{"k": "v"}\n`````
  3. JSON nested inside chatty prose: scan each ``{`` / ``[`` opener
     and try ``JSONDecoder().raw_decode()`` until one parses with the
     expected top-level type. This is robust against responses with
     stray ``{x}``-style placeholders or bracketed examples in prose.

Callers pass an optional ``logger_callback`` so the extractor stays
logger-agnostic; each callsite continues to log against its own
module logger using its own domain event constants.
"""

import json
import re

# Runtime import (not TYPE_CHECKING) so ``typing.get_type_hints()``
# can resolve ``Callable`` in the public function annotations under
# Python 3.14's PEP 649 lazy-evaluation regime.
from collections.abc import Callable  # noqa: TC003
from typing import Any

# Captures the body of a fenced block, optional ``json`` language tag,
# tolerant to leading / trailing newlines around the body. Reused by
# both extractors below.
_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def _strip_markdown_fences(text: str) -> str:
    """Return the body of the first markdown fence, or the input.

    A no-op when no fence is present.
    """
    match = _FENCE_PATTERN.search(text)
    return match.group(1).strip() if match else text


def _safe_log(callback: Callable[[str], None] | None, detail: str) -> None:
    """Invoke ``callback`` if set, swallowing logger-side exceptions.

    A logger callback that raises (e.g. a misconfigured handler)
    would mask the actual extraction failure that the caller cares
    about, so we keep parsing failures observable while never letting
    the logger break the extractor's contract. Process-level resource
    errors (``MemoryError``, ``RecursionError``) propagate; the
    project-wide convention is that those signal an unrecoverable
    state and must not be silently absorbed.
    """
    if callback is None:
        return
    try:
        callback(detail)
    except MemoryError, RecursionError:
        raise
    except Exception:
        return


def _scan_for_json(stripped: str, opener: str) -> dict[str, Any] | list[Any] | None:
    """Scan ``stripped`` for the first parseable JSON value at each ``opener``.

    Walks every ``opener`` position (``{`` for objects, ``[`` for arrays)
    and attempts ``JSONDecoder().raw_decode()`` from there. Returns the
    first decoded value, regardless of top-level type; the caller
    isinstance-filters to its expected shape. Robust against stray
    ``{x}`` placeholders or bracketed examples that would defeat a
    naive ``find``/``rfind`` slice.
    """
    decoder = json.JSONDecoder()
    for idx, char in enumerate(stripped):
        if char != opener:
            continue
        try:
            value, _ = decoder.raw_decode(stripped[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value
    return None


def extract_json_from_llm_response(
    text: str,
    *,
    logger_callback: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Extract a JSON object from an LLM response.

    Strips markdown fences first; on parse failure, falls back to
    scanning each ``{`` opener and using ``raw_decode()`` until a
    valid object is found.

    Args:
        text: The raw LLM response.
        logger_callback: Optional one-arg callable invoked with a
            short detail string (e.g. ``"json_decode_error"``)
            when extraction fails. Caller's responsibility to route
            the message through its own module logger.

    Returns:
        The parsed object on success; ``None`` if no valid JSON
        object could be located.
    """
    stripped = text.strip()
    if not stripped:
        return None

    candidate = _strip_markdown_fences(stripped)
    parsed_ok = False
    try:
        parsed = json.loads(candidate)
        parsed_ok = True
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        return parsed
    # A successful parse with the wrong top-level shape is a hard
    # failure, not a cue to re-parse via brace-matching: re-running
    # the substring fallback on ``[{"k": 1}]`` would happily return
    # the inner dict, which is the opposite of the helper's contract.
    if parsed_ok:
        _safe_log(logger_callback, "json_wrong_top_level_type")
        return None

    fallback = _scan_for_json(stripped, "{")
    if isinstance(fallback, dict):
        return fallback

    _safe_log(logger_callback, "json_decode_error")
    return None


def extract_json_array_from_llm_response(
    text: str,
    *,
    logger_callback: Callable[[str], None] | None = None,
) -> list[Any] | None:
    """Extract a JSON array from an LLM response (mirror of dict variant).

    Strips markdown fences first; on parse failure, falls back to
    scanning each ``[`` opener and using ``raw_decode()`` until a
    valid array is found.

    Args:
        text: The raw LLM response.
        logger_callback: Optional one-arg callable for failure detail.

    Returns:
        The parsed list on success; ``None`` if no valid JSON array
        could be located.
    """
    stripped = text.strip()
    if not stripped:
        return None

    candidate = _strip_markdown_fences(stripped)
    parsed_ok = False
    try:
        parsed = json.loads(candidate)
        parsed_ok = True
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return parsed
    if parsed_ok:
        _safe_log(logger_callback, "json_wrong_top_level_type")
        return None

    fallback = _scan_for_json(stripped, "[")
    if isinstance(fallback, list):
        return fallback

    _safe_log(logger_callback, "json_decode_error")
    return None
