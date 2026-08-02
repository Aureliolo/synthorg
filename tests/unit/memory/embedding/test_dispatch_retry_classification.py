"""Which embedding failures are worth another attempt.

This classifier is the ``retryable=`` predicate the embedding retry
handler consults for every exception, so it governs the memory
subsystem's hot path: every recall and every write goes through it. It
resolves its exception types by a deferred import, which keeps litellm
off the app's cold-import graph and means a typo in the import list, or
a class renamed upstream, would surface only as retries quietly not
happening during a real provider outage.
"""

import pytest
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    ContentPolicyViolationError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from synthorg.memory.embedding.dispatch import is_retryable_embedding_error

pytestmark = pytest.mark.unit

_MODEL = "example-small-001"
_PROVIDER = "example-provider"


def _transient() -> list[Exception]:
    """Build one instance of every failure the classifier must retry.

    Returns:
        Exceptions a later attempt could plausibly succeed past.
    """
    return [
        RateLimitError(message="slow down", llm_provider=_PROVIDER, model=_MODEL),
        Timeout(message="timed out", llm_provider=_PROVIDER, model=_MODEL),
        ServiceUnavailableError(
            message="unavailable", llm_provider=_PROVIDER, model=_MODEL
        ),
        InternalServerError(message="boom", llm_provider=_PROVIDER, model=_MODEL),
        APIConnectionError(message="reset", llm_provider=_PROVIDER, model=_MODEL),
    ]


def _deterministic() -> list[Exception]:
    """Build failures that repeat identically however often they are retried.

    Returns:
        Exceptions where retrying only burns the backoff budget and hides
        the real cause behind a retry-exhausted error.
    """
    return [
        AuthenticationError(message="bad key", llm_provider=_PROVIDER, model=_MODEL),
        BadRequestError(message="malformed", llm_provider=_PROVIDER, model=_MODEL),
        NotFoundError(message="no such model", llm_provider=_PROVIDER, model=_MODEL),
        ContentPolicyViolationError(
            message="refused", llm_provider=_PROVIDER, model=_MODEL
        ),
        ValueError("not a provider error at all"),
    ]


@pytest.mark.parametrize("exc", _transient(), ids=lambda e: type(e).__name__)
def test_a_transient_failure_is_retried(exc: Exception) -> None:
    assert is_retryable_embedding_error(exc) is True


@pytest.mark.parametrize("exc", _deterministic(), ids=lambda e: type(e).__name__)
def test_a_deterministic_failure_is_not_retried(exc: Exception) -> None:
    assert is_retryable_embedding_error(exc) is False


def test_the_classification_is_stable_across_calls() -> None:
    """The tuple is memoised, so the first call must not be a special case."""
    exc = RateLimitError(message="slow down", llm_provider=_PROVIDER, model=_MODEL)

    assert is_retryable_embedding_error(exc) is True
    assert is_retryable_embedding_error(exc) is True
