r"""Shared JSON extractors for LLM response strings.

LLM responses arrive as free-form text and may contain a JSON object
(or array) wrapped in markdown code fences, surrounded by chatty
prose, or both. The extractors here handle the common shapes:

  1. Plain JSON: ``{"k": "v"}``
  2. Markdown-fenced JSON: `````json\n{"k": "v"}\n`````
  3. JSON nested inside chatty prose: brace-matching the outermost
     ``{`` ... ``}`` (object) or ``[`` ... ``]`` (array) substring.

Callers pass an optional ``logger_callback`` so the extractor stays
logger-agnostic; each callsite continues to log against its own
module logger using its own domain event constants.
"""

import contextlib
import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

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
    the logger break the extractor's contract.
    """
    if callback is None:
        return
    with contextlib.suppress(Exception):
        callback(detail)


def extract_json_from_llm_response(
    text: str,
    *,
    logger_callback: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Extract a JSON object from an LLM response.

    Strips markdown fences first; on parse failure, falls back to
    matching the outermost ``{`` and ``}`` substring for noisy
    responses that wrap the JSON in prose.

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
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        return parsed

    # Brace-matching fallback for prose-wrapped JSON.
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            fallback = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            _safe_log(logger_callback, "json_decode_error")
            return None
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
    matching the outermost ``[`` and ``]`` substring.

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
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return parsed

    start = stripped.find("[")
    end = stripped.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            fallback = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            _safe_log(logger_callback, "json_decode_error")
            return None
        if isinstance(fallback, list):
            return fallback

    _safe_log(logger_callback, "json_decode_error")
    return None
