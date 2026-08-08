"""Failure classification for recovery results.

Answers one question: given what is known about a terminated run (the typed
exception, or only the message the loop recorded), which failure category is
it? The typed cause is the authority; keyword matching is the fallback for
messages that arrive with no exception attached.
"""

from enum import StrEnum
from typing import Final

from synthorg.providers.errors import (
    AuthenticationError,
    ContentFilterError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderInternalError,
    ProviderQuotaExceededError,
    ProviderTimeoutError,
    RateLimitError,
)


class FailureCategory(StrEnum):
    """Machine-readable failure classification for recovery results.

    Used by ``RecoveryResult`` to provide structured failure diagnosis
    that enables smarter checkpoint reconciliation and task reassignment
    routing.  ``UNKNOWN`` is the honest default for error messages that
    cannot be confidently classified -- it is explicit rather than a
    silent ``TOOL_FAILURE`` lie.
    """

    TOOL_FAILURE = "tool_failure"
    STAGNATION = "stagnation"
    BUDGET_EXCEEDED = "budget_exceeded"
    QUALITY_GATE_FAILED = "quality_gate_failed"
    TIMEOUT = "timeout"
    DELEGATION_FAILED = "delegation_failed"
    PROVIDER_REFUSED = "provider_refused"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNKNOWN = "unknown"


# The provider raised a typed error, so the category is a fact rather than an
# inference. Keyed by the exception CLASS, not its name: a rename then fails
# the type check instead of silently falling back to keyword sniffing, which
# is how a `ProviderError` naming the model, the parameter and the provider
# came back `unknown`.
#
# The split is what an operator does next. REFUSED means the provider
# understood the request and rejected it (a bad parameter, an unknown model,
# a content filter, a credential): retrying reproduces it, so the fix is a
# configuration change. UNAVAILABLE means the provider could not answer right
# now (timeout, connection, 5xx, rate limit): the same request may well
# succeed later.
_TYPED_FAILURE_CATEGORIES: Final[
    tuple[tuple[type[ProviderError], FailureCategory], ...]
] = (
    # Subclasses first: ProviderQuotaExceededError inherits RateLimitError but
    # is a depleted allowance, not a transient limit, so retrying is futile.
    (ProviderQuotaExceededError, FailureCategory.PROVIDER_REFUSED),
    (AuthenticationError, FailureCategory.PROVIDER_REFUSED),
    (ModelNotFoundError, FailureCategory.PROVIDER_REFUSED),
    (InvalidRequestError, FailureCategory.PROVIDER_REFUSED),
    (ContentFilterError, FailureCategory.PROVIDER_REFUSED),
    (ProviderTimeoutError, FailureCategory.TIMEOUT),
    (RateLimitError, FailureCategory.PROVIDER_UNAVAILABLE),
    (ProviderConnectionError, FailureCategory.PROVIDER_UNAVAILABLE),
    (ProviderInternalError, FailureCategory.PROVIDER_UNAVAILABLE),
)


# Keyword rules for inferring failure category from error messages.
# Evaluated in order; first match wins.  Order is load-bearing:
# BUDGET_EXCEEDED takes precedence over TIMEOUT/STAGNATION/etc. in
# ambiguous messages because budget exhaustion is the most operationally
# actionable signal.  DELEGATION comes before TOOL_FAILURE so messages
# like "delegation failed: tool unavailable" classify as DELEGATION,
# not TOOL_FAILURE.  Reordering this tuple changes classification for
# ambiguous messages.
_FAILURE_CATEGORY_RULES: tuple[tuple[tuple[str, ...], FailureCategory], ...] = (
    (("budget",), FailureCategory.BUDGET_EXCEEDED),
    (("timeout", "timed out"), FailureCategory.TIMEOUT),
    (("stagnation",), FailureCategory.STAGNATION),
    (("delegation",), FailureCategory.DELEGATION_FAILED),
    (("quality", "criteria"), FailureCategory.QUALITY_GATE_FAILED),
    (
        ("tool invocation", "tool execution", "tool error", "mcp tool"),
        FailureCategory.TOOL_FAILURE,
    ),
)


# Categories that require sidecar data on ``RecoveryResult`` (enforced by
# the cross-field model validator).  Callers that only have an error string
# cannot satisfy those invariants and must use
# ``infer_failure_category_without_evidence`` which clamps to ``UNKNOWN``.
_CATEGORIES_REQUIRING_EVIDENCE: Final[frozenset[FailureCategory]] = frozenset(
    {
        FailureCategory.STAGNATION,
        FailureCategory.QUALITY_GATE_FAILED,
    }
)


def infer_failure_category(error_message: str) -> FailureCategory:
    """Infer a failure category from an error message via keyword matching.

    Simple heuristic for v1 -- matches keywords case-insensitively in
    the declared rule order (first match wins).  Returns
    ``FailureCategory.UNKNOWN`` when nothing matches: honest failure
    classification is better than silently defaulting to
    ``TOOL_FAILURE``, which would masquerade unknown causes as tool
    failures in dashboards, reports, and reconciliation prompts.

    Note:
        Callers that build a ``RecoveryResult`` without sidecar data
        (``stagnation_evidence`` / ``criteria_failed``) must use
        ``infer_failure_category_without_evidence`` instead; this
        function can return ``STAGNATION`` or ``QUALITY_GATE_FAILED``
        which would violate the cross-field invariants at construction
        time.

    Args:
        error_message: The error message to classify.

    Returns:
        The inferred ``FailureCategory`` or ``UNKNOWN`` when no rule
        matches.
    """
    lower = error_message.lower()
    for keywords, category in _FAILURE_CATEGORY_RULES:
        if any(kw in lower for kw in keywords):
            return category
    return FailureCategory.UNKNOWN


def category_for_exception(exc: BaseException) -> FailureCategory | None:
    """Classify a failure from the exception the provider raised.

    The typed cause is the authority: a provider that refused a request said
    so in its own exception class, and the prose it wrapped it in belongs to
    the provider, not to us. Keyword matching only gets a turn when there is
    no typed cause to read.

    Args:
        exc: The exception that terminated the run.

    Returns:
        The category the exception type maps to, or ``None`` when it is not
        a provider error this table classifies.
    """
    for error_type, category in _TYPED_FAILURE_CATEGORIES:
        if isinstance(exc, error_type):
            return category
    return None


def category_for_error_type(error_type: str | None) -> FailureCategory | None:
    """Classify a failure from the recorded exception class name.

    The execution loop records the class name because a frozen
    ``ExecutionResult`` cannot carry a live exception across the boundary.
    Resolved against the same table, so the two entry points cannot disagree.

    Args:
        error_type: ``type(exc).__name__`` as the loop recorded it.

    Returns:
        The category the named type maps to, or ``None`` when it names
        nothing this table classifies.
    """
    if not error_type:
        return None
    for known, category in _TYPED_FAILURE_CATEGORIES:
        if known.__name__ == error_type:
            return category
    return None


def infer_failure_category_without_evidence(
    error_message: str,
    *,
    error_type: str | None = None,
) -> FailureCategory:
    """Infer a failure category, clamping evidence-required categories to UNKNOWN.

    The typed cause wins when there is one: a ``ProviderError`` naming the
    model, the parameter and the provider is a classification, and reading
    its prose for keywords instead is how it came back ``unknown``. Neither
    provider category needs sidecar evidence, so both survive the clamp.

    Callers that build a ``RecoveryResult`` without ``stagnation_evidence``
    or ``criteria_failed`` cannot emit ``STAGNATION`` or
    ``QUALITY_GATE_FAILED`` because the cross-field validator rejects
    those categories when the required sidecar data is absent.  This
    helper preserves the honest ``UNKNOWN`` default while keeping the
    categories that stand on their own (``BUDGET_EXCEEDED``,
    ``TIMEOUT``, ``DELEGATION_FAILED``).

    Args:
        error_message: The error message to classify.
        error_type: Class name of the exception that terminated the run,
            when the loop recorded one.

    Returns:
        A ``FailureCategory`` safe to use without accompanying evidence.
    """
    typed = category_for_error_type(error_type)
    if typed is not None:
        return typed
    category = infer_failure_category(error_message)
    if category in _CATEGORIES_REQUIRING_EVIDENCE:
        return FailureCategory.UNKNOWN
    return category
