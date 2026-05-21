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
IMAGE_DATA_PLACEHOLDER: Final[str] = "***IMAGE_DATA_ELIDED***"


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
        # Tempered greedy token: match anything that is not the start
        # of the closing marker. Linear-time (no catastrophic
        # backtracking) even on a large input with no ``-----END``.
        re.compile(
            r"-----BEGIN [A-Z ]+-----(?:(?!-----END)[\s\S])*-----END [A-Z ]+-----",
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
        # Value group stops at a quote / comma / brace / backslash but
        # NOT at whitespace, so a multi-word secret (``"password":
        # "my secret"``) is fully scrubbed rather than leaking its tail.
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|token|authorization)\b"
            r"(['\"]?\s*[:=]\s*['\"]?)"
            r"((?:\\.|[^'\",}\\])+)",
        ),
        rf"\g<1>\g<2>{REDACTION_PLACEHOLDER}",
    ),
)

# A dict key whose name itself denotes a secret: the value is replaced
# wholesale (structured JSON puts the secret in the value, which the
# string patterns above never see because only the value string is
# inspected, not its key).
_SECRET_FIELD_NAME: Final[re.Pattern[str]] = re.compile(
    r"(?i)^(api[_-]?key|secret|password|token|authorization)$",
)

# An image payload field on a multimodal message: not a secret, but
# base64 image bytes bloat the human-readable cassette copy with no
# review value (the raw bytes are still hashed for replay fidelity in
# ``keying.py``). Elide them to keep the on-disk cassette diffable.
_IMAGE_DATA_FIELD_NAME: Final[re.Pattern[str]] = re.compile(
    r"(?i)^(base64_data|data_uri)$",
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
        redacted: dict[object, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_FIELD_NAME.fullmatch(key):
                redacted[key] = REDACTION_PLACEHOLDER
            elif isinstance(key, str) and _IMAGE_DATA_FIELD_NAME.fullmatch(key):
                redacted[key] = IMAGE_DATA_PLACEHOLDER
            else:
                redacted[key] = _redact_value(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


class PatternRedactor:
    """Default redactor: scrubs common secret shapes from the stored copy.

    Covers PEM private-key blocks, ``Bearer`` tokens,
    ``sk-``-prefixed opaque API keys, AWS access-key ids, labelled
    ``api_key`` / ``secret`` / ``password`` / ``token`` /
    ``authorization`` assignments in strings, and dict fields whose
    *key* is one of those secret names (structured JSON, where the
    secret is the value of a secret-named key). Non-secret prose and
    non-string scalars pass through unchanged.
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
    "IMAGE_DATA_PLACEHOLDER",
    "REDACTION_PLACEHOLDER",
    "CassetteRedactor",
    "NullRedactor",
    "PatternRedactor",
]
