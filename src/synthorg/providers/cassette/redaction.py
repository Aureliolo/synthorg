"""Secret redaction for the human-readable cassette copy.

The replay key is hashed on the **raw** request, never the redacted
copy, so redaction can scrub the on-disk cassette without ever
changing replay-matching behaviour. Redaction is therefore a
defence-in-depth measure for the stored ``request_repr`` / response
payload: provider credentials are never passed to ``complete()`` (they
live in driver config), so the residual exposure is a secret embedded
in a prompt or a tool result, which the default :class:`PatternRedactor`
scrubs.

The redactor is pluggable via the :class:`CassetteRedactor` protocol so
a deployment can supply a stricter policy without touching the seam.
"""

import re
from typing import Final, Protocol, runtime_checkable

REDACTION_PLACEHOLDER: Final[str] = "***REDACTED***"


@runtime_checkable
class CassetteRedactor(Protocol):
    """Scrubs secrets from a JSON-serialisable cassette payload.

    Implementations must return a new structure and must not mutate
    the input.
    """

    def redact(self, payload: object) -> object:
        """Return a redacted copy of ``payload``."""
        ...


# Ordered (pattern, replacement) pairs. PEM first so its inner base64
# is not partially eaten by the opaque-key rule; Bearer before the
# labelled-assignment rule so the token itself is scrubbed rather than
# just the ``Authorization`` label.
_SUBSTITUTIONS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----",
            re.DOTALL,
        ),
        REDACTION_PLACEHOLDER,
    ),
    (
        re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
        f"Bearer {REDACTION_PLACEHOLDER}",
    ),
    (
        re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
        REDACTION_PLACEHOLDER,
    ),
    (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        REDACTION_PLACEHOLDER,
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|token|authorization)\b"
            r"(['\"]?\s*[:=]\s*['\"]?)"
            r"([^'\"\s,}]+)",
        ),
        rf"\g<1>\g<2>{REDACTION_PLACEHOLDER}",
    ),
)


def _redact_str(value: str) -> str:
    """Apply every substitution pattern to a single string."""
    for pattern, replacement in _SUBSTITUTIONS:
        value = pattern.sub(replacement, value)
    return value


def _redact_value(value: object) -> object:
    """Recursively redact a JSON-serialisable value into a new structure."""
    if isinstance(value, str):
        return _redact_str(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


class PatternRedactor:
    """Default redactor: scrubs common secret shapes from the stored copy.

    Covers PEM private-key blocks, ``Bearer`` tokens, OpenAI-style
    ``sk-`` keys, AWS access-key ids, and labelled
    ``api_key`` / ``secret`` / ``password`` / ``token`` /
    ``authorization`` assignments. Non-secret prose and non-string
    scalars pass through unchanged.
    """

    def redact(self, payload: object) -> object:
        """Return a redacted deep copy of ``payload``."""
        return _redact_value(payload)


class NullRedactor:
    """Opt-in faithful capture: returns the payload unchanged.

    Intended for tests that must assert on the exact recorded bytes.
    Never select this for a run whose cassette may be shared.
    """

    def redact(self, payload: object) -> object:
        """Return ``payload`` unchanged."""
        return payload


__all__ = [
    "REDACTION_PLACEHOLDER",
    "CassetteRedactor",
    "NullRedactor",
    "PatternRedactor",
]
