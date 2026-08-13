"""C15: a precise provider error stops being diagnosed ``unknown``.

A ``ProviderError`` naming the model, the parameter and the provider came
back ``failure_category=unknown, criteria_failed_count=0``, one line after a
snapshot that named all three. The diagnosis read the message for keywords
and had no branch for "the provider refused the request".
"""

import pytest

from synthorg.engine.failure_classification import (
    _TYPED_FAILURE_CATEGORIES,
    FailureCategory,
    category_for_error_type,
    effective_cause,
    infer_failure_category_without_evidence,
    recorded_error_type,
)
from synthorg.providers.errors import (
    AuthenticationError,
    ContentFilterError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderImageGenerationUnsupportedError,
    ProviderInternalError,
    ProviderModelNotFoundError,
    ProviderQuotaExceededError,
    ProviderTimeoutError,
    RateLimitError,
)
from synthorg.providers.resilience.errors import RetryExhaustedError

pytestmark = pytest.mark.unit

_TYPED_CASES = [
    (
        InvalidRequestError("model does not support reasoning_effort"),
        FailureCategory.PROVIDER_REFUSED,
    ),
    (AuthenticationError("missing key"), FailureCategory.PROVIDER_REFUSED),
    (ModelNotFoundError("no such model"), FailureCategory.PROVIDER_REFUSED),
    (ContentFilterError("blocked"), FailureCategory.PROVIDER_REFUSED),
    (
        ProviderQuotaExceededError("weekly allowance spent"),
        FailureCategory.PROVIDER_REFUSED,
    ),
    (ProviderTimeoutError("too slow"), FailureCategory.TIMEOUT),
    (RateLimitError("slow down"), FailureCategory.PROVIDER_UNAVAILABLE),
    (
        ProviderConnectionError("connection reset"),
        FailureCategory.PROVIDER_UNAVAILABLE,
    ),
    (
        ProviderInternalError("upstream 500"),
        FailureCategory.PROVIDER_UNAVAILABLE,
    ),
]


def _classify(exc: BaseException) -> FailureCategory | None:
    """Run the production path: the loop records a name, the diagnosis reads it."""
    return category_for_error_type(recorded_error_type(exc))


class TestTypedClassification:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        _TYPED_CASES,
        ids=[type(exc).__name__ for exc, _ in _TYPED_CASES],
    )
    def test_each_typed_cause_classifies(
        self, exc: Exception, expected: FailureCategory
    ) -> None:
        """What an operator does next differs by category, so the split matters."""
        assert _classify(exc) is expected

    def test_a_quota_error_is_refused_not_merely_unavailable(self) -> None:
        """It subclasses RateLimitError; retrying a spent allowance is futile."""
        assert (
            _classify(ProviderQuotaExceededError("spent"))
            is FailureCategory.PROVIDER_REFUSED
        )

    def test_an_unrelated_exception_classifies_nothing(self) -> None:
        """The table answers only for provider errors; the rest fall through."""
        assert _classify(ValueError("boom")) is None

    def test_every_table_entry_precedes_its_own_base_class(self) -> None:
        """First match wins, so a base listed early swallows its subclasses.

        Pinned across the whole table rather than for the one pair that has
        bitten: a new entry appended in the wrong place would otherwise be
        caught only by whichever category it silently stole.
        """
        classes = [entry for entry, _ in _TYPED_FAILURE_CATEGORIES]
        for index, error_class in enumerate(classes):
            shadowing = [
                base.__name__
                for base in classes[:index]
                if issubclass(error_class, base)
            ]
            assert not shadowing, (
                f"{error_class.__name__} is unreachable behind {shadowing}"
            )


class TestRetryWrapping:
    """The shipped retry handler re-raises every retryable error wrapped."""

    def test_the_wrapper_does_not_hide_the_cause(self) -> None:
        """Without unwrapping, TIMEOUT is unreachable in production."""
        wrapped = RetryExhaustedError(ProviderTimeoutError("too slow"))

        assert _classify(wrapped) is FailureCategory.TIMEOUT

    def test_the_recorded_name_is_the_cause_not_the_wrapper(self) -> None:
        """A frozen result carries a name; recording the wrapper loses the fact."""
        wrapped = RetryExhaustedError(ProviderConnectionError("reset"))

        assert recorded_error_type(wrapped) == "ProviderConnectionError"

    def test_an_unwrapped_error_records_its_own_name(self) -> None:
        assert recorded_error_type(InvalidRequestError("bad")) == "InvalidRequestError"

    def test_nesting_is_unwrapped_to_the_innermost_cause(self) -> None:
        inner = ProviderTimeoutError("too slow")
        nested = RetryExhaustedError(RetryExhaustedError(inner))

        assert effective_cause(nested) is inner


class TestRecordedTypeName:
    def test_the_recorded_class_name_resolves_to_the_same_category(self) -> None:
        """The frozen result carries a name, not a live exception."""
        assert (
            category_for_error_type("InvalidRequestError")
            is FailureCategory.PROVIDER_REFUSED
        )

    def test_an_unrecorded_type_resolves_to_nothing(self) -> None:
        assert category_for_error_type(None) is None
        assert category_for_error_type("ValueError") is None

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (
                ProviderImageGenerationUnsupportedError("no images here"),
                FailureCategory.PROVIDER_REFUSED,
            ),
            (
                ProviderModelNotFoundError("unknown model"),
                FailureCategory.PROVIDER_REFUSED,
            ),
        ],
        ids=["image-generation-unsupported", "provider-model-not-found"],
    )
    def test_a_subclass_resolves_by_name_too(
        self, exc: Exception, expected: FailureCategory
    ) -> None:
        """Name equality alone made every subclass a stranger.

        ``ProviderImageGenerationUnsupportedError`` is an
        ``InvalidRequestError`` and diagnoses as one, but exact-name equality
        found no row and answered ``unknown``.
        """
        assert _classify(exc) is expected
        assert category_for_error_type(type(exc).__name__) is expected

    def test_the_retry_wrapper_names_no_category_of_its_own(self) -> None:
        """It is a ProviderError, but "we retried" is not a diagnosis."""
        assert category_for_error_type("RetryExhaustedError") is None


class TestDiagnosis:
    def test_the_typed_cause_beats_the_message(self) -> None:
        """The live message read as nothing; the type reads as a refusal."""
        message = (
            "Provider error on turn 1: InvalidRequestError: model "
            "example-capable-001 does not support parameter reasoning_effort"
        )

        assert (
            infer_failure_category_without_evidence(message) is FailureCategory.UNKNOWN
        )
        assert (
            infer_failure_category_without_evidence(
                message, error_type="InvalidRequestError"
            )
            is FailureCategory.PROVIDER_REFUSED
        )

    def test_the_keyword_rules_still_answer_without_a_typed_cause(self) -> None:
        """Not every failure comes from a provider; the fallback stays."""
        assert (
            infer_failure_category_without_evidence("budget exhausted")
            is FailureCategory.BUDGET_EXCEEDED
        )
