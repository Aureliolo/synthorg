"""Resilience-specific error types."""

from ai_company.providers.errors import ProviderError


class RetryExhaustedError(ProviderError):
    """All retry attempts exhausted for a retryable error.

    Raised by ``RetryHandler`` when ``max_retries`` is reached.
    The engine layer catches this to trigger fallback chains.

    Attributes:
        original_error: The last retryable error that was raised.
    """

    is_retryable = False

    def __init__(
        self,
        original_error: ProviderError,
    ) -> None:
        """Initialize with the original error that exhausted retries.

        Args:
            original_error: The last retryable ``ProviderError``.
        """
        self.original_error = original_error
        super().__init__(
            f"Retry exhausted after error: {original_error.message}",
            context=dict(original_error.context),
        )
