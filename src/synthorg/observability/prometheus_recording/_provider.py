# module-kind: code
"""Provider token/cost/error recording."""

from synthorg.observability import get_logger
from synthorg.observability.prometheus_labels import (
    VALID_PROVIDER_CALL_TYPES,
    VALID_PROVIDER_ERROR_CLASSES,
    normalize_model_label,
    normalize_provider_label,
    require_label,
    require_non_negative,
)
from synthorg.observability.prometheus_recording._base import (
    _RecordingMetricsBase,
)

logger = get_logger(__name__)


class _ProviderRecordingMixin(_RecordingMetricsBase):
    """Provider token/cost/error recording."""

    def record_provider_usage(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        """Record an LLM provider call's token and cost usage.

        Called from ``integration/provider_caller.py`` after a
        completion resolves (after retry/rate-limit). Tokens and cost
        are monotonically increasing counters -- never reset at
        runtime.

        Args:
            provider: Provider id (e.g. ``"example-provider"``).
            model: Model name (e.g. ``"large"``).
            input_tokens: Tokens in the request prompt.
            output_tokens: Tokens in the response completion.
            cost: Computed cost in the configured currency for this call.
        """
        require_non_negative("record_provider_usage: input_tokens", input_tokens)
        require_non_negative("record_provider_usage: output_tokens", output_tokens)
        require_non_negative("record_provider_usage: cost", cost)
        provider_label = normalize_provider_label(provider)
        model_label = normalize_model_label(model)
        self._provider_tokens.labels(
            provider=provider_label,
            model=model_label,
            direction="input",
        ).inc(input_tokens)
        self._provider_tokens.labels(
            provider=provider_label,
            model=model_label,
            direction="output",
        ).inc(output_tokens)
        self._provider_cost.labels(
            provider=provider_label,
            model=model_label,
        ).inc(cost)

    def record_provider_call_duration(
        self,
        *,
        provider: str,
        model: str,
        call_type: str,
        duration_sec: float,
    ) -> None:
        """Observe a provider call's wall-clock duration.

        Wired from :meth:`BaseCompletionProvider.complete` and ``stream``
        so latency is captured for both call types (``complete`` recorded
        only a span attribute before, and ``stream`` recorded nothing).
        ``call_type`` is bounded to :data:`VALID_PROVIDER_CALL_TYPES`;
        provider/model fold against the snapshot allowlist so a renamed
        provider or garbage model id cannot inflate cardinality.
        """
        require_label("call_type", call_type, VALID_PROVIDER_CALL_TYPES)
        require_non_negative(
            "record_provider_call_duration: duration_sec", duration_sec
        )
        self._provider_call_duration.labels(
            provider=normalize_provider_label(provider),
            model=normalize_model_label(model),
            call_type=call_type,
        ).observe(duration_sec)

    def record_provider_error(
        self,
        *,
        provider: str,
        model: str,
        error_class: str,
    ) -> None:
        """Increment the provider-error counter for a failed completion.

        Wired from :meth:`BaseCompletionProvider.complete`/``stream``;
        the caller classifies the exception via
        :func:`synthorg.providers.errors.classify_provider_error` so
        ``error_class`` stays bounded.
        """
        require_label("error_class", error_class, VALID_PROVIDER_ERROR_CLASSES)
        self._provider_errors.labels(
            provider=normalize_provider_label(provider),
            model=normalize_model_label(model),
            error_class=error_class,
        ).inc()
