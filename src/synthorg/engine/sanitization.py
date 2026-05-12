"""Message sanitization helpers for engine subsystems.

Provides pattern-based redaction of file paths, URLs, and prompt
injection markers, plus stripping of non-printable characters and
length limiting for messages before they are injected into LLM
context.
"""

import re
from typing import Final

_PATH_PATTERN = re.compile(
    # Quoted Windows paths (handles spaces in paths like Program Files)
    r'"[A-Za-z]:\\[^"]*"'
    r"|'[A-Za-z]:\\[^']*'"
    # Windows drive-letter paths (unquoted)
    r"|[A-Za-z]:\\[^\s,;)\"']+"
    # UNC backslash paths
    r"|\\\\[^\s,;)\"']+"
    # UNC forward-slash paths
    r"|//[^\s,;)\"']{2,}"
    # Unix absolute paths (not preceded by a word character to avoid
    # false positives on ratios like 100/min or content-type headers)
    r"|(?<!\w)/[^\s,;)\"']{2,}"
    # Relative paths
    r"|\.\.?/[^\s,;)\"']+"
)
_URL_PATTERN = re.compile(
    r"(?:https?|postgresql|postgres|mysql|redis|mongodb|amqp|ftp|sftp|file)"
    r"://[^\s,;)\"']+"
)

# Prompt-injection markers commonly used to escape system/user frames.
# We strip these on best-effort: the goal is to make it harder for
# LLM-authored free text (error messages, acceptance criteria) to
# redirect a resumed agent's context.  Not a security boundary on its
# own -- pair with dedicated prompt-injection defenses at higher layers.
#
# NOTE: ``{{ }}`` / ``{% %}`` templating delimiters are deliberately
# NOT filtered here.  They are legitimate structured content in
# acceptance criteria, assistant snippets, and procedural-memory
# payloads that downstream LLM flows expect to preserve -- redacting
# them would corrupt valid input with no meaningful injection-defense
# benefit.
#
# The ``=== BEGIN/END <FRAME> ===`` family covers delimiters used by
# the classification semantic detectors (``===BEGIN CONVERSATION===``,
# ``===END CONVERSATION===``) and related framings a caller might
# introduce, so that untrusted content cannot spoof its own frame
# terminator and break out of the bounded region in a downstream LLM
# prompt.
_PROMPT_INJECTION_PATTERN = re.compile(
    r"(?i)"
    r"(?:ignore\s+(?:all\s+|any\s+|the\s+|your\s+|previous\s+|above\s+)+"
    r"(?:instructions?|rules?|commands?|directives?))"
    r"|\[/?(?:INST|SYS|SYSTEM|USER|ASSISTANT)\]"
    r"|<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>"
    r"|<\|/?(?:system|user|assistant)\|>"
    r"|={3,}\s*(?:BEGIN|END)\s+[A-Z][A-Z_ ]*\s*={3,}"
)


_DEFAULT_MAX_LENGTH: Final[int] = 200


def sanitize_message(raw: str, *, max_length: int = _DEFAULT_MAX_LENGTH) -> str:
    """Redact paths/URLs/injection markers, strip non-printable chars, cap length.

    Args:
        raw: The raw message to sanitize.
        max_length: Upper bound on raw input length in characters.
            Applied before path/URL/injection-marker redaction and
            non-printable stripping. Redaction tokens may cause the
            output to slightly exceed this limit.

    Returns:
        Sanitized message safe for inclusion in LLM context.
        If the result contains no alphanumeric characters after
        processing, returns ``"details redacted"`` as a safe fallback.

    Raises:
        ValueError: If ``max_length`` is negative.
    """
    if max_length < 0:
        msg = f"max_length must be >= 0, got {max_length}"
        raise ValueError(msg)
    capped = raw[:max_length]
    sanitized = _URL_PATTERN.sub("[REDACTED_URL]", capped)
    sanitized = _PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    sanitized = _PROMPT_INJECTION_PATTERN.sub("[FILTERED]", sanitized)
    sanitized = "".join(c for c in sanitized if c.isprintable())
    if not any(c.isalnum() for c in sanitized):
        return "details redacted"
    return sanitized
