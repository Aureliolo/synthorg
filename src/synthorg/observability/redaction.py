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

This module provides four helpers:

``scrub_secret_tokens(text)``
    Pattern-replace well-known credential shapes (URL-encoded form
    fields, JSON string values, URI userinfo, ``Authorization:``
    headers, bare ``bearer <token>`` text, a credential key named with a
    colon, an issued credential quoted back with no framing at all, and
    Fernet ciphertexts) with ``***`` placeholders.  Idempotent and
    bounded in output length.

``safe_error_description(exc)``
    Return ``f"{type(exc).__name__}: {scrub_secret_tokens(str(exc))}"``,
    truncated to :data:`MAX_SCRUBBED_LENGTH` with an ellipsis marker.
    Suitable as the value of ``error=`` on any ``logger.warning`` /
    ``logger.error`` call on a secret-bearing code path.

``describe_without_input(exc)``
    Describe a pydantic ``ValidationError`` from its structured errors
    (field location plus reason) rather than its message, so the value
    that failed validation is never in the string at all.  Use this,
    never ``safe_error_description``, when the model being validated can
    carry credentials: pydantic quotes the input it rejected, and its own
    truncation strips the framing the scrubber matches on.

``log_exception_redacted(logger, event, exc, **kwargs)``
    Single-call replacement for the manual redacted-error boilerplate
    ``logger.error(EVENT, ..., error_type=type(exc).__name__,
    error=safe_error_description(exc))``.  Use anywhere an ``except``
    branch needs to emit a redacted-error log without attaching the
    traceback (the ``logger.exception`` shape forbidden by the
    secret-log redaction rule enforced in
    ``scripts/check_logger_exception_str_exc.py``).

Callers that need to remove traceback attachment as well as scrub the
message should pair this helper with ``logger.warning`` (which does not
attach ``exc_info``) instead of ``logger.exception``.  The exception
chain is still preserved for callers via ``raise ... from exc``.
"""

import re
from collections.abc import Callable, Mapping
from typing import Any, Final, NamedTuple, get_args

from pydantic import ValidationError
from pydantic_core.core_schema import ErrorType

from synthorg.core.critical_errors import reraise_critical

MAX_SCRUBBED_LENGTH: Final[int] = 512
"""Hard cap on the length of the output of :func:`safe_error_description`.

Prevents a crafted exception message from amplifying log size. The
ellipsis marker ``...[truncated]`` counts against the cap.
"""

_TRUNCATION_MARKER: Final[str] = "...[truncated]"

_AUTHORED_MESSAGE_TYPES: Final[frozenset[str]] = frozenset(
    {"value_error", "assertion_error"}
)
"""Pydantic's own error types whose ``msg`` is a string an author wrote.

Both are declared by pydantic, so :data:`_PYDANTIC_ERROR_TYPES` admits them,
but each carries ``str()`` of the exception the validator raised. That is
rendered at raise time and therefore already holds whatever the author
interpolated into it, which is what makes them the exception to the rule
below rather than instances of it.
"""

_PYDANTIC_ERROR_TYPES: Final[frozenset[str]] = frozenset(get_args(ErrorType))
"""Every error type pydantic itself declares, read from its own ``Literal``.

Derived rather than listed. The safe set is the one pydantic generates the
message for, and a hand-written copy of it would be one release away from
disagreeing with the library it claims to describe, in the direction that
lets an unlisted type through as trusted.

Anything absent is a ``PydanticCustomError``, whose code AND message
template are both author-written, so its ``msg`` can hold a credential the
same way an authored ``ValueError`` can. Answering "is this string one
pydantic composed" is the only question that separates the two, and this
is the only place the answer is authoritative.
"""

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

# A credential key by its trailing noun, wherever the name puts it.  A
# vendor prefixes and hyphenates freely (``x-api-key``, ``X-Auth-Token``,
# ``app_client_secret``), so anchoring on a fixed list of whole names
# misses the shapes upstream actually emits; the suffix is what makes the
# key a credential.
_CREDENTIAL_KEY_SUFFIX: Final[str] = r"[\w-]*(?:api[_-]?key|token|secret|password)"

# JSON string value: ``"<key>"<sep>:<sep>"<value>"`` where ``<key>`` is a
# credential name.  We keep the key and open/close quotes so the JSON
# stays structurally valid after scrubbing.  The value body accepts any
# ``\\<char>`` escape pair (covering ``\\"``) or any non-quote
# non-backslash character, so secrets containing escaped quotes (e.g.
# ``{"client_secret":"abc\\"def"}``) are masked end-to-end instead of
# being truncated at the first ``\\"``.  The suffix branch runs first and
# subsumes every ``*_token`` / ``*_secret`` / ``*api_key`` / ``password``
# name; the literals after it are the credential keys that end in
# something else.
_JSON_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf'"({_CREDENTIAL_KEY_SUFFIX}'
    r'|code_verifier|authorization|bearer|assertion|code)"'
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

# Bare ``bearer <token>`` anywhere in free text, without the
# ``Authorization:`` header framing or a ``bearer=`` form-field
# ``=``.  Upstream HTTP libraries routinely raise exceptions whose
# message embeds the token this way ("auth failed: bearer eyJ..."),
# which neither the header pattern nor the form pattern catches, so
# the raw token would otherwise survive into the log record. The
# matched keyword is preserved verbatim (case + spelling) so the
# stricter header rule's ``Authorization: Bearer ***`` output is left
# byte-identical and the substitution stays idempotent.
_BARE_BEARER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(bearer)\s+\S+",
    re.IGNORECASE,
)

# Fernet ciphertext prefix.  Every Fernet token starts with the version
# byte ``0x80`` which base64-encodes as ``gAAAAAB``; we require at least
# 16 further URL-safe-base64 characters to avoid false positives on
# unrelated text that happens to begin with ``gAAAAAB``.
_FERNET_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"gAAAAAB[A-Za-z0-9_-]{16,}",
)

# A credential-bearing key named with a colon, beyond ``Authorization:``.
# Upstream 401 bodies routinely echo the offending header by its own name
# ("Invalid x-api-key: 1234..."), which the header rule does not cover
# because it anchors on one keyword. The value class deliberately excludes
# quotes, so an already-JSON-shaped pair is left to ``_JSON_PATTERN`` above
# rather than being rewritten into invalid JSON, and a value this rule has
# already masked re-matches to the identical output.
_KEYED_COLON_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"\b({_CREDENTIAL_KEY_SUFFIX})(\s*:\s*)([^\s\"',}}]+)",
    re.IGNORECASE,
)

# Issued credential shapes that carry no keyword frame at all. Every rule
# above needs one (``key=``, ``"key":``, ``Authorization:``, ``bearer ``), and
# the commonest way a credential reaches a log supplies none: a provider's
# rejection quotes the key back inside a sentence. Matched on the issued
# prefix plus a minimum body length, so ordinary prose cannot collide, and the
# prefix is preserved so the log still says which credential class was
# rejected. Not exhaustive by construction: an unprefixed opaque token in
# prose is indistinguishable from a word, and masking every word that follows
# "token" would cost more than it protects.
_PREFIXED_SECRET_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(sk-|gh[pousr]_|github_pat_|glpat-|xox[baprs]-|AIza|AKIA)"
    r"[A-Za-z0-9_-]{16,}",
)

# Substrings shared by the credential key names several rules recognise.
# Lowercase because the gate below tests a lowercased copy of the subject.
_KEY_SUFFIX_MARKERS: Final[frozenset[str]] = frozenset(
    {"apikey", "api_key", "api-key", "token", "secret", "password"},
)


class _ScrubRule(NamedTuple):
    """One redaction pattern with the substrings that can trigger it.

    ``markers`` is what makes the short-circuit in
    :func:`scrub_secret_tokens` safe: every string ``pattern`` can match
    contains at least one of them, lowercased.  Declaring them beside the
    pattern is deliberate -- a rule added without its triggers is a rule
    the gate silently never runs.

    The second half of the contract belongs to ``replace``: it may not
    introduce a marker the subject did not already carry.  Every
    replacement here either emits ``***`` or re-emits a group the pattern
    matched, so the marker set only ever shrinks as the passes run, which
    is what lets one scan of the original text decide the whole sequence.
    """

    pattern: re.Pattern[str]
    replace: str | Callable[[re.Match[str]], str]
    markers: frozenset[str]


# Applied in order, and the order carries meaning: the JSON rule masks a
# quoted value before the keyed-colon rule can rewrite the pair into
# invalid JSON, and the header rule claims ``Authorization: Bearer x``
# before the bare-bearer rule sees it.  Each pass rewrites what the
# previous one produced, which is how a value framed two ways ends up
# masked by both.
_RULES: Final[tuple[_ScrubRule, ...]] = (
    _ScrubRule(
        _URL_FORM_PATTERN,
        lambda m: f"{m.group(1)}=***",
        _KEY_SUFFIX_MARKERS
        | {"code", "client_id", "assertion", "bearer", "authorization"},
    ),
    _ScrubRule(
        _JSON_PATTERN,
        lambda m: f'"{m.group(1)}"{m.group(2)}"***"',
        _KEY_SUFFIX_MARKERS | {"code", "assertion", "bearer", "authorization"},
    ),
    _ScrubRule(_URL_USERINFO_PATTERN, r"\1***@", frozenset({"://"})),
    _ScrubRule(
        _AUTH_HEADER_PATTERN,
        lambda m: f"{m.group(1)}{m.group(2)} ***",
        frozenset({"authorization"}),
    ),
    _ScrubRule(
        _BARE_BEARER_PATTERN,
        lambda m: f"{m.group(1)} ***",
        frozenset({"bearer"}),
    ),
    _ScrubRule(
        _KEYED_COLON_PATTERN,
        lambda m: f"{m.group(1)}{m.group(2)}***",
        _KEY_SUFFIX_MARKERS,
    ),
    _ScrubRule(
        _PREFIXED_SECRET_PATTERN,
        lambda m: f"{m.group(1)}***",
        frozenset(
            {
                "sk-",
                "ghp_",
                "gho_",
                "ghu_",
                "ghs_",
                "ghr_",
                "github_pat_",
                "glpat-",
                "xox",
                "aiza",
                "akia",
            },
        ),
    ),
    _ScrubRule(
        _FERNET_PATTERN,
        "***FERNET_CIPHERTEXT***",
        frozenset({"gaaaaab"}),
    ),
)

# Every trigger any rule declares. A subject carrying none of them cannot
# match any rule, and a subject carrying some runs only the rules whose
# own triggers are among them. That is the whole optimisation: this
# function runs on every string leaf of every log record, almost none of
# which carry a credential, and the few that do carry one shape rather
# than eight.
_GATE_MARKERS: Final[tuple[str, ...]] = tuple(
    dict.fromkeys(marker for rule in _RULES for marker in rule.markers)
)


def scrub_secret_tokens(text: str) -> str:
    """Return *text* with known credential patterns masked.

    Replacements are:

    - ``client_secret=xxx`` (and other URL-encoded form fields) →
      ``client_secret=***``.  Percent-encoded values are covered too:
      ``client_secret=%2A%26%2A`` is masked wholesale, not truncated at
      the first embedded ``&``.
    - ``"access_token":"xxx"`` (and any other JSON string value whose
      key ends in a credential noun, including prefixed and hyphenated
      names like ``"x-api-key"``) → ``"access_token":"***"``
    - ``postgres://user:hunter2@host/db`` (URI userinfo) →
      ``postgres://user:***@host/db``.  Covers any ``<scheme>://
      <user>:<password>@...`` URL that shows up in exception messages.
    - ``Authorization: Bearer xxx`` / ``Authorization: Basic xxx`` →
      ``Authorization: Bearer ***`` / ``Authorization: Basic ***``
    - bare ``bearer xxx`` in free text (no ``Authorization:`` header,
      no ``=``) → ``bearer ***`` (keyword case preserved)
    - ``x-api-key: xxx`` and other unquoted ``<name>token|secret|
      password|api_key: value`` pairs → ``x-api-key: ***``
    - an issued credential quoted back in prose with no framing at all
      (``Incorrect API key provided: sk-...``) → prefix plus ``***``
    - ``gAAAAAB...`` (Fernet ciphertexts) → ``***FERNET_CIPHERTEXT***``

    Not exhaustive, and cannot be: an opaque token with neither a keyword
    nor an issued prefix is indistinguishable from a word, so callers still
    pass ``safe_error_description`` rather than raw provider text wherever
    the choice exists.

    The function is idempotent: applying it twice is equivalent to
    applying it once.

    One scan for :data:`_GATE_MARKERS` decides which rules run: a rule
    whose own triggers are all absent from the subject cannot match it,
    so it is skipped, and a subject carrying no trigger at all is
    returned untouched.  Worth doing because this function runs on every
    string leaf of every log record, and almost none of them carry a
    credential.

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
    lowered = text.lower()
    present = {marker for marker in _GATE_MARKERS if marker in lowered}
    if not present:
        return text
    try:
        scrubbed = text
        for pattern, replace, markers in _RULES:
            if markers.isdisjoint(present):
                continue
            scrubbed = pattern.sub(replace, scrubbed)
    except re.error:
        # Defensive: regex-level failure (pathological input, engine
        # bug) must not crash the caller's log call.  The
        # processor-level scrubber still sees the event dict and can
        # apply another pass.
        return text
    return scrubbed


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
        # Direct ``str(exc)`` is intentional here: this function IS the
        # redacted wrapper. ``scrub_secret_tokens`` is applied below.
        # Calling ``safe_error_description`` here would infinitely recurse.
        message = str(exc)
    except Exception as stringify_exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(stringify_exc)  # pragma: no cover - defensive
        try:
            message = repr(exc)
        except Exception as repr_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(repr_exc)  # pragma: no cover - defensive
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


def _safe_reason(error: Mapping[str, object]) -> str:
    """Return the part of *error* that cannot carry what was validated.

    Allow-listed, not deny-listed: the message is shown only when pydantic
    is known to have composed it. A deny-list has to name every way an
    author-written string can arrive, and misses the one nobody thought
    of, which here means a credential reaching the dashboard.

    Deliberately not typed ``ErrorDetails``: that ``TypedDict`` requires
    an ``input`` key, and the whole point of the caller is to ask for the
    errors without one, so the mapping it hands over does not have it.

    Returns:
        The rendered message for an error pydantic composed itself; the
        type slug for anything else, which is every error carrying a
        string some validator author wrote.
    """
    error_type = str(error["type"])
    composed_by_pydantic = (
        error_type in _PYDANTIC_ERROR_TYPES
        and error_type not in _AUTHORED_MESSAGE_TYPES
    )
    if composed_by_pydantic:
        return str(error["msg"])
    return error_type


def describe_without_input(exc: ValidationError) -> str:
    """Describe a ``ValidationError`` without echoing what it validated.

    :func:`safe_error_description` is the right tool for an arbitrary
    exception, but it is the wrong one for a validation failure over a
    model carrying credentials. Pydantic quotes the offending input in its
    message (``input_value=...``), and it truncates the middle of a large
    value, which removes exactly the surrounding ``"key":`` framing that
    :func:`scrub_secret_tokens` matches on. A pattern scrubber also has to
    recognise a secret to redact it, and this product is deliberately
    vendor-agnostic: a self-hosted gateway key looks like nothing in
    particular.

    So this does not scrub, it never receives the value in the first
    place. Pydantic is asked for the structured errors with the input,
    the context and the docs URL all excluded, leaving the field location
    and the reason, which is what an operator needs and all they need:
    they know what they typed.

    Excluding those three fields is enough only for the errors pydantic
    itself raises, whose ``msg`` is rendered from its own template and so
    is constraint-derived ("Field required", "Extra inputs are not
    permitted"). It is NOT enough for the two types that carry a message
    a validator author wrote: ``msg`` is rendered when the exception is
    raised, so a validator that interpolates the value it rejected has
    already put that value in the string, and no later exclusion can take
    it back out. Those get their stable type slug instead, which pydantic
    generates and no input reaches. The validator's own message is not
    lost, it is logged at the point it is raised; what it must not do is
    cross this boundary, whose whole purpose is that a rejected provider
    entry can be described without describing its credentials.

    Args:
        exc: The validation failure to describe.

    Returns:
        One ``location: reason`` clause per error, bounded in length.
        The type name alone when pydantic reports no structured errors.
    """
    clauses = [
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}:"
        f" {_safe_reason(error)}"
        for error in exc.errors(
            include_url=False,
            include_input=False,
            include_context=False,
        )
    ]
    if not clauses:
        return type(exc).__name__
    candidate = "; ".join(clauses)
    if len(candidate) <= MAX_SCRUBBED_LENGTH:
        return candidate
    keep = MAX_SCRUBBED_LENGTH - len(_TRUNCATION_MARKER)
    return candidate[:keep] + _TRUNCATION_MARKER


def log_exception_redacted(  # type: ignore[explicit-any]  # structlog proxy; see docstring
    logger: Any,
    event: str,
    exc: BaseException,
    /,
    **kwargs: object,
) -> None:
    """Emit an ERROR log for *exc* with the redacted-error kwargs applied.

    Single-call replacement for the canonical secret-log-safe boilerplate::

        logger.error(
            EVENT,
            ...,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    Use anywhere an ``except`` branch needs a redacted-error log
    without attaching the traceback (the ``logger.exception`` shape
    forbidden by ``scripts/check_logger_exception_str_exc.py`` because
    structlog's ``format_exc_info`` processor serialises frame-locals
    into the log record).  Caller-supplied ``error_type`` / ``error``
    kwargs are rejected at runtime to keep the redaction pair
    authoritative.

    Args:
        logger: A structlog (or compatible) logger with an ``error()``
            method. Typed as ``Any`` because structlog's
            ``BoundLoggerLazyProxy`` (returned by ``get_logger``)
            forwards attribute access through ``__getattr__`` until the
            proxy is bound, so a nominal ``Protocol`` annotation cannot
            describe its surface without false negatives at call sites.
        event: The event-name constant (from
            ``synthorg.observability.events.<domain>``).
        exc: The exception instance being logged.
        **kwargs: Additional structured fields. ``error_type`` and
            ``error`` are reserved and cannot be supplied here.

    Raises:
        TypeError: If the caller supplies a reserved ``error_type``,
            ``error``, or ``exc_info`` key in ``**kwargs``.
    """
    if "error_type" in kwargs or "error" in kwargs:
        msg = (
            "log_exception_redacted owns 'error_type' and 'error'; "
            "remove them from the kwargs."
        )
        raise TypeError(msg)
    if "exc_info" in kwargs:
        # structlog's ``format_exc_info`` processor walks the live
        # traceback's frame-locals into the event record. Allowing
        # ``exc_info=True`` (or any truthy value) through here would
        # serialise any in-scope credential the moment the helper is
        # invoked, defeating the whole point of routing through it.
        # Reject at runtime so a stray ``exc_info=...`` cannot silently
        # downgrade the redaction guarantee.
        msg = (
            "log_exception_redacted forbids 'exc_info'; remove it from kwargs. "
            "The helper deliberately suppresses traceback attachment to keep "
            "frame-locals out of the structured log record."
        )
        raise TypeError(msg)
    logger.error(
        event,
        **kwargs,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )
