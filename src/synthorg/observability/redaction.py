"""Scrub secret material out of exception strings before logging.

Structured logs have proven to be a secret-exfiltration channel when
callers write ``logger.warning(EVENT, error=str(exc))`` on paths that
touch OAuth token exchange, Fernet decryption, or any HTTP call whose
request body contains credentials.  Two risks combine there:

* ``str(exc)`` on ``httpx.HTTPStatusError`` embeds the URL and, for some
  OAuth providers, the POSTed form body in the exception message, which
  leaks ``client_secret``, ``refresh_token``, and ``code_verifier``.
* ``logger.exception`` attaches the full Python traceback with local
  frame variables, so a request-payload ``dict`` sitting on the stack
  ends up serialized into the log record.

This module provides two helpers:

``scrub_secret_tokens(text)``
    Pattern-replace well-known credential shapes (URL-encoded form
    fields, JSON string values, ``Authorization:`` headers, Fernet
    ciphertexts) with ``***`` placeholders.  Idempotent and bounded in
    output length.

``safe_error_description(exc)``
    Return ``f"{type(exc).__name__}: {scrub_secret_tokens(str(exc))}"``,
    truncated to :data:`MAX_SCRUBBED_LENGTH` with an ellipsis marker.
    Suitable as the value of ``error=`` on any ``logger.warning`` /
    ``logger.error`` call on a secret-bearing code path.

Callers that need to remove traceback attachment as well as scrub the
message should pair this helper with ``logger.warning`` (which does not
attach ``exc_info``) instead of ``logger.exception``.  The exception
chain is still preserved for callers via ``raise ... from exc``.
"""

import re
from typing import Final

MAX_SCRUBBED_LENGTH: Final[int] = 512
"""Hard cap on the length of the output of :func:`safe_error_description`.

Prevents a crafted exception message from amplifying log size. The
ellipsis marker ``...[truncated]`` counts against the cap.
"""

_TRUNCATION_MARKER: Final[str] = "...[truncated]"

# URL-encoded form field: ``<key>=<value>`` where ``<key>`` is one of the
# known credential names.  Stops at unescaped whitespace / ``&`` / quotes
# / closing brackets. Any other character -- including a literal ``%``
# that happens not to be followed by two hex digits -- is part of the
# masked value, so pathological cases like ``api_key=100%raw_secret``
# are redacted wholesale rather than truncating at the stray ``%``.
_URL_FORM_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(client_secret|client_id|refresh_token|access_token|code_verifier"
    r"|api_key|api_secret|id_token|assertion|password|bearer|authorization"
    r"|code)="
    r"[^\s&'\"\]\}]+",
    re.IGNORECASE,
)

# JSON string value: ``"<key>"<sep>:<sep>"<value>"`` where ``<key>`` is a
# known credential name.  We keep the key and open/close quotes so the
# JSON stays structurally valid after scrubbing.  The value body accepts
# any ``\\<char>`` escape pair (covering ``\\"``) or any non-quote
# non-backslash character, so secrets containing escaped quotes (e.g.
# ``{"client_secret":"abc\\"def"}``) are masked end-to-end instead of
# being truncated at the first ``\\"``.
_JSON_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'"(access_token|refresh_token|client_secret|code_verifier|api_key'
    r"|api_secret|authorization|bearer|id_token|assertion|password"
    r'|code)"'
    r'(\s*:\s*)"(?:\\.|[^"\\])*"',
    re.IGNORECASE,
)

# URI userinfo: ``<scheme>://<userinfo>@<host>``.  Connection strings
# routinely surface in exception messages ("connection refused:
# postgres://user:hunter2@host/db") and would otherwise leak the
# password portion.  We mask the *entire* userinfo segment -- not just
# the ``<password>`` half -- so credential-only forms like
# ``https://ghp_xxx@github.com`` and percent-encoded socket URIs like
# ``redis://%2Fsecret.sock@host`` are also redacted.  Keeping scheme +
# host (the non-secret framing) preserves operator triage.
_URL_USERINFO_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"([a-z][a-z0-9+.\-]*://)([^/\s@]+)@",
    re.IGNORECASE,
)

# HTTP Authorization header: ``Authorization: Bearer <token>`` or
# ``Authorization: Basic <base64>``.
_AUTH_HEADER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(authorization\s*:\s*)(bearer|basic)\s+\S+",
    re.IGNORECASE,
)

# Fernet ciphertext prefix.  Every Fernet token starts with the version
# byte ``0x80`` which base64-encodes as ``gAAAAAB``; we require at least
# 16 further URL-safe-base64 characters to avoid false positives on
# unrelated text that happens to begin with ``gAAAAAB``.
_FERNET_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"gAAAAAB[A-Za-z0-9_-]{16,}",
)


def scrub_secret_tokens(text: str) -> str:
    """Return *text* with known credential patterns masked.

    Replacements are:

    - ``client_secret=xxx`` (and other URL-encoded form fields) →
      ``client_secret=***``.  Percent-encoded values are covered too:
      ``client_secret=%2A%26%2A`` is masked wholesale, not truncated at
      the first embedded ``&``.
    - ``"access_token":"xxx"`` (and other JSON string values) →
      ``"access_token":"***"``
    - ``postgres://user:hunter2@host/db`` (URI userinfo) →
      ``postgres://user:***@host/db``.  Covers any ``<scheme>://
      <user>:<password>@...`` URL that shows up in exception messages.
    - ``Authorization: Bearer xxx`` / ``Authorization: Basic xxx`` →
      ``Authorization: Bearer ***`` / ``Authorization: Basic ***``
    - ``gAAAAAB...`` (Fernet ciphertexts) → ``***FERNET_CIPHERTEXT***``

    The function is idempotent: applying it twice is equivalent to
    applying it once.

    **Robustness contract**: any exception raised by the regex engine
    (for example, from catastrophic backtracking on a pathological
    input) is swallowed; the original *text* is returned unchanged so
    the caller's logging pipeline stays alive.  In that rare case, the
    processor-level scrubber (`scrub_event_fields`) still gets a
    chance to mask credentials downstream -- better a defensive
    passthrough than a dropped log event.

    Args:
        text: Arbitrary string (exception message, response body, etc.).

    Returns:
        A new string with all matched substrings replaced, or the
        original string if the scrub itself failed.
    """
    try:
        scrubbed = _URL_FORM_PATTERN.sub(
            lambda m: f"{m.group(1)}=***",
            text,
        )
        scrubbed = _JSON_PATTERN.sub(
            lambda m: f'"{m.group(1)}"{m.group(2)}"***"',
            scrubbed,
        )
        scrubbed = _URL_USERINFO_PATTERN.sub(r"\1***@", scrubbed)
        scrubbed = _AUTH_HEADER_PATTERN.sub(
            lambda m: f"{m.group(1)}{m.group(2)} ***",
            scrubbed,
        )
        return _FERNET_PATTERN.sub("***FERNET_CIPHERTEXT***", scrubbed)
    except MemoryError, RecursionError:
        # Catastrophic interpreter state -- propagate so the process
        # can surface the failure rather than silently proceeding with
        # a half-scrubbed or original string.
        raise
    except re.error:
        # Defensive: regex-level failure (pathological input, engine
        # bug) must not crash the caller's log call.  The
        # processor-level scrubber still sees the event dict and can
        # apply another pass.
        return text


def safe_error_description(exc: BaseException) -> str:
    """Return a scrubbed ``{ExcType}: {message}`` description of *exc*.

    The message portion is passed through :func:`scrub_secret_tokens`
    to strip credential patterns, then the full result is truncated to
    :data:`MAX_SCRUBBED_LENGTH` characters with a trailing
    ``...[truncated]`` marker if the scrub left the string too long.

    This is the shape every ``error=`` log field on a secret-bearing
    code path should use.  It preserves the exception-type taxonomy
    (``HTTPStatusError`` vs ``ConnectError`` vs ``InvalidToken``) that
    operators need for triage, without letting credential values into
    the log record.

    Args:
        exc: The exception instance whose description should be logged.

    Returns:
        ``"{type(exc).__name__}: {scrubbed_message}"``, bounded in
        length.  When ``str(exc)`` is empty, returns just the type
        name.
    """
    type_name = type(exc).__name__
    # ``str(exc)`` can raise if the exception has a broken ``__str__``
    # (e.g., custom exceptions that recurse or call a method that
    # itself raises). Fall back to ``repr(exc)`` and, if that also
    # fails, to the type name alone. We never let the log helper
    # crash the caller -- except for catastrophic interpreter state
    # (``MemoryError`` / ``RecursionError``), which must propagate per
    # project convention so the process can surface the failure.
    try:
        message = str(exc)
    except MemoryError, RecursionError:
        raise
    except Exception:  # pragma: no cover - defensive
        try:
            message = repr(exc)
        except MemoryError, RecursionError:
            raise
        except Exception:  # pragma: no cover - defensive
            return type_name
    if not message:
        return type_name
    scrubbed = scrub_secret_tokens(message)
    candidate = f"{type_name}: {scrubbed}"
    if len(candidate) <= MAX_SCRUBBED_LENGTH:
        return candidate
    # Truncate to leave room for the marker without exceeding the cap.
    keep = MAX_SCRUBBED_LENGTH - len(_TRUNCATION_MARKER)
    return candidate[:keep] + _TRUNCATION_MARKER
