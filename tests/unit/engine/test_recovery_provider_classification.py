"""C15: a precise provider error stops being diagnosed ``unknown``.

A ``ProviderError`` naming the model, the parameter and the provider came
back ``failure_category=unknown, criteria_failed_count=0``, one line after a
snapshot that named all three. The diagnosis read the message for keywords
and had no branch for "the provider refused the request".
"""

import pytest

from synthorg.engine.failure_classification import (
    FailureCategory,
    category_for_error_type,
    category_for_exception,
    infer_failure_category_without_evidence,
)
from synthorg.providers.errors import (
    AuthenticationError,
    ContentFilterError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderInternalError,
    ProviderQuotaExceededError,
    ProviderTimeoutError,
    RateLimitError,
)

pytestmark = pytest.mark.unit


class TestTypedClassification:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
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
        ],
    )
    def test_each_typed_cause_classifies(
        self, exc: Exception, expected: FailureCategory
    ) -> None:
        """What an operator does next differs by category, so the split matters."""
        assert category_for_exception(exc) is expected

    def test_a_quota_error_is_refused_not_merely_unavailable(self) -> None:
        """It subclasses RateLimitError; retrying a spent allowance is futile."""
        assert (
            category_for_exception(ProviderQuotaExceededError("spent"))
            is FailureCategory.PROVIDER_REFUSED
        )

    def test_an_unrelated_exception_classifies_nothing(self) -> None:
        """The table answers only for provider errors; the rest fall through."""
        assert category_for_exception(ValueError("boom")) is None


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


class TestDiagnosis:
    def test_the_typed_cause_beats_the_message(self) -> None:
        """The live message read as nothing; the type reads as a refusal."""
        message = (
            "Provider error on turn 1: InvalidRequestError: model "
            "example-medium-001 does not support parameter reasoning_effort"
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
