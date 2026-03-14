"""Tests for provider error hierarchy."""

import pytest

from synthorg.providers.errors import (
    AuthenticationError,
    ContentFilterError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderInternalError,
    ProviderTimeoutError,
    RateLimitError,
)

pytestmark = pytest.mark.timeout(30)


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
        ctx = {"provider": "example-provider", "model": "medium"}
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
        ctx = {"provider": "test-provider"}
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
