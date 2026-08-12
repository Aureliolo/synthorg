"""Tests for provider error hierarchy."""

import pytest
from pydantic import JsonValue

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.providers.errors import (
    AuthenticationError,
    ContentFilterError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderAlreadyExistsError,
    ProviderConnectionError,
    ProviderError,
    ProviderInternalError,
    ProviderNotFoundError,
    ProviderOverloadedError,
    ProviderPaymentRequiredError,
    ProviderTimeoutError,
    ProviderValidationError,
    RateLimitError,
)


@pytest.mark.unit
class TestProviderError:
    """Tests for the base ProviderError."""

    def test_message_stored(self) -> None:
        err = ProviderError("something broke")
        assert err.message == "something broke"

    def test_context_defaults_to_empty(self) -> None:
        err = ProviderError("oops")
        assert err.context == {}

    def test_context_stored(self) -> None:
        ctx: dict[str, JsonValue] = {"provider": "example-provider", "model": "medium"}
        err = ProviderError("oops", context=ctx)
        assert err.context == ctx

    def test_str_without_context(self) -> None:
        err = ProviderError("broken")
        assert str(err) == "broken"

    def test_str_with_context(self) -> None:
        err = ProviderError("broken", context={"key": "val"})
        assert "broken" in str(err)
        assert "key='val'" in str(err)

    def test_is_exception(self) -> None:
        assert issubclass(ProviderError, Exception)

    def test_base_not_retryable(self) -> None:
        err = ProviderError("base")
        assert err.is_retryable is False


@pytest.mark.unit
class TestErrorHierarchy:
    """Tests for all typed error subclasses."""

    def test_all_subclass_provider_error(self) -> None:
        subclasses = [
            AuthenticationError,
            RateLimitError,
            ModelNotFoundError,
            InvalidRequestError,
            ContentFilterError,
            ProviderTimeoutError,
            ProviderConnectionError,
            ProviderInternalError,
        ]
        for cls in subclasses:
            assert issubclass(cls, ProviderError)

    @pytest.mark.parametrize(
        ("cls", "expected"),
        [
            (AuthenticationError, False),
            (RateLimitError, True),
            (ModelNotFoundError, False),
            (InvalidRequestError, False),
            (ContentFilterError, False),
            (ProviderTimeoutError, True),
            (ProviderConnectionError, True),
            (ProviderInternalError, True),
        ],
    )
    def test_is_retryable(
        self,
        cls: type[ProviderError],
        expected: bool,
    ) -> None:
        err = cls("test error")
        assert err.is_retryable is expected

    def test_retryable_errors_are_catchable_as_provider_error(self) -> None:
        err = RateLimitError("too fast")
        with pytest.raises(ProviderError):
            raise err

    def test_non_retryable_errors_are_catchable_as_provider_error(self) -> None:
        err = AuthenticationError("bad key")
        with pytest.raises(ProviderError):
            raise err


@pytest.mark.unit
class TestServiceabilityErrorClasses:
    """The two conditions a reachability probe cannot see.

    A model that answers ``GET /models`` while returning 503 on every
    completion is reachable and unserviceable; an account whose extra-usage
    balance is empty is reachable, serviceable and refusing to work until an
    operator tops it up. Neither had a distinct type, so neither could be
    counted apart from a generic 5xx.
    """

    def test_overloaded_is_a_server_error(self) -> None:
        assert issubclass(ProviderOverloadedError, ProviderInternalError)

    def test_overloaded_is_retryable(self) -> None:
        assert ProviderOverloadedError("queueing").is_retryable is True

    def test_overloaded_answers_503(self) -> None:
        assert ProviderOverloadedError.status_code == 503

    def test_overloaded_shares_the_internal_wire_code(self) -> None:
        # An inheritance alias: clients branch on one code for "upstream is
        # having a server-side problem"; the distinction lives in the
        # serviceability label, not in the wire contract.
        assert ProviderOverloadedError.error_code is ErrorCode.PROVIDER_INTERNAL

    def test_payment_required_is_not_retryable(self) -> None:
        # Retrying an empty balance cannot fill it. This is an operator
        # action, and treating it as transient burns the retry ladder on
        # every call for as long as the balance stays empty.
        assert ProviderPaymentRequiredError("balance empty").is_retryable is False

    def test_payment_required_answers_402(self) -> None:
        assert ProviderPaymentRequiredError.status_code == 402

    def test_payment_required_has_its_own_code(self) -> None:
        assert (
            ProviderPaymentRequiredError.error_code
            is ErrorCode.PROVIDER_PAYMENT_REQUIRED
        )
        assert (
            ProviderPaymentRequiredError.error_category is ErrorCategory.PROVIDER_ERROR
        )

    def test_payment_required_is_a_provider_error(self) -> None:
        assert issubclass(ProviderPaymentRequiredError, ProviderError)


@pytest.mark.unit
class TestRateLimitError:
    """Tests specific to RateLimitError."""

    def test_retry_after_stored(self) -> None:
        err = RateLimitError("slow down", retry_after=30.0)
        assert err.retry_after == 30.0

    def test_retry_after_defaults_to_none(self) -> None:
        err = RateLimitError("slow down")
        assert err.retry_after is None

    def test_context_passed_through(self) -> None:
        err = RateLimitError(
            "slow down",
            retry_after=5.0,
            context={"provider": "test-provider"},
        )
        assert err.context == {"provider": "test-provider"}
        assert err.retry_after == 5.0


@pytest.mark.unit
class TestErrorFormatting:
    """Tests for __str__ formatting across error types."""

    def test_all_errors_include_message_in_str(self) -> None:
        for cls in (
            AuthenticationError,
            RateLimitError,
            ModelNotFoundError,
            InvalidRequestError,
            ContentFilterError,
            ProviderTimeoutError,
            ProviderConnectionError,
            ProviderInternalError,
        ):
            err = cls("test msg", context={"model": "test-model"})
            result = str(err)
            assert "test msg" in result
            assert "model='test-model'" in result

    def test_sensitive_key_redacted(self) -> None:
        err = ProviderError(
            "auth failed",
            context={"api_key": "sk-secret-123", "model": "test-model"},
        )
        result = str(err)
        assert "sk-secret-123" not in result
        assert "api_key='***'" in result
        assert "model='test-model'" in result

    @pytest.mark.parametrize(
        "key",
        ["api_key", "token", "secret", "password", "authorization"],
    )
    def test_all_redacted_keys(self, key: str) -> None:
        err = ProviderError("err", context={key: "sensitive_value"})
        result = str(err)
        assert "sensitive_value" not in result
        assert f"{key}='***'" in result

    def test_redaction_is_case_insensitive(self) -> None:
        err = ProviderError(
            "err",
            context={"API_KEY": "sk-123", "Authorization": "Bearer tok"},
        )
        result = str(err)
        assert "sk-123" not in result
        assert "Bearer tok" not in result
        assert "API_KEY='***'" in result
        assert "Authorization='***'" in result


@pytest.mark.unit
class TestContextImmutability:
    """Tests for context immutability guarantees."""

    def test_context_is_immutable(self) -> None:
        err = ProviderError("oops", context={"key": "val"})
        with pytest.raises(TypeError):
            err.context["new_key"] = "new_val"  # type: ignore[index]

    def test_original_dict_mutation_does_not_affect_error(self) -> None:
        ctx: dict[str, JsonValue] = {"provider": "test-provider"}
        err = ProviderError("oops", context=ctx)
        ctx["api_key"] = "sk-secret"
        assert "api_key" not in err.context


@pytest.mark.unit
class TestRateLimitValidation:
    """Tests for RateLimitError retry_after validation."""

    def test_negative_retry_after_rejected(self) -> None:
        with pytest.raises(ValueError, match="finite non-negative"):
            RateLimitError("slow down", retry_after=-5.0)

    def test_nan_retry_after_rejected(self) -> None:
        with pytest.raises(ValueError, match="finite non-negative"):
            RateLimitError("slow down", retry_after=float("nan"))

    def test_inf_retry_after_rejected(self) -> None:
        with pytest.raises(ValueError, match="finite non-negative"):
            RateLimitError("slow down", retry_after=float("inf"))

    def test_zero_retry_after_accepted(self) -> None:
        err = RateLimitError("slow down", retry_after=0.0)
        assert err.retry_after == 0.0


@pytest.mark.unit
class TestProviderManagementErrorStatusCodes:
    """Verify provider-management error subclasses override the parent
    ProviderError 502 default with semantically correct HTTP status
    codes so the domain exception handler maps them directly without
    a controller-level catch-and-translate.
    """

    def test_provider_not_found_error_is_404(self) -> None:
        assert ProviderNotFoundError.status_code == 404
        assert ProviderNotFoundError.error_code == ErrorCode.RESOURCE_NOT_FOUND
        assert ProviderNotFoundError.error_category == ErrorCategory.NOT_FOUND

    def test_provider_validation_error_is_422(self) -> None:
        assert ProviderValidationError.status_code == 422
        assert ProviderValidationError.error_code == ErrorCode.VALIDATION_ERROR
        assert ProviderValidationError.error_category == ErrorCategory.VALIDATION

    def test_provider_already_exists_error_is_409(self) -> None:
        assert ProviderAlreadyExistsError.status_code == 409
        assert ProviderAlreadyExistsError.error_code == ErrorCode.RESOURCE_CONFLICT
        assert ProviderAlreadyExistsError.error_category == ErrorCategory.CONFLICT

    def test_status_code_overrides_parent_default(self) -> None:
        # Sanity check: if these inherit 502 from ProviderError, the
        # controllers in src/synthorg/api/controllers/providers.py
        # will continue to need the catch-and-translate boilerplate.
        assert ProviderError.status_code == 502
        assert ProviderNotFoundError.status_code != ProviderError.status_code
        assert ProviderValidationError.status_code != ProviderError.status_code
        assert ProviderAlreadyExistsError.status_code != ProviderError.status_code
