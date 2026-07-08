# module-kind: code
"""Image-generation provider capability.

Image generation is a provider capability layered onto completion drivers
via :class:`ImageGenerationMixin`, kept separate from
:class:`~synthorg.providers.base.BaseCompletionProvider` so the completion
base stays focused (and under its size budget). Drivers that can generate
images (LiteLLM, scripted) mix it in; the mixin routes the call through the
same retry, rate-limit, and cost-recording path as ``complete``.

:class:`ImageGenerationProvider` is the structural interface callers narrow
to before invoking :meth:`generate_image` (the completion protocol stays
image-free).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability.tracing.instrumentation import get_tracer

from ._call_instrumentation import (
    build_call_span_attributes,
    record_call_failure,
    record_call_success,
    record_image_cost_if_in_scope,
)
from ._resilience import rate_limited_call, resilient_execute
from ._validation import validate_model
from .errors import InvalidRequestError, ProviderImageGenerationUnsupportedError
from .image_models import ImageGenerationConfig, ImageGenerationResponse
from .resilience.rate_limiter import RateLimiter
from .resilience.retry import RetryHandler

_tracer = get_tracer(__name__)

_MILLISECONDS_PER_SECOND: float = 1000.0


@runtime_checkable
class ImageGenerationProvider(Protocol):
    """Structural interface for a provider that can generate images."""

    async def generate_image(
        self,
        prompt: str,
        model: str,
        *,
        config: ImageGenerationConfig | None = None,
    ) -> ImageGenerationResponse:
        """Generate one or more images from a text prompt."""
        ...


class ImageGenerationMixin:
    """Adds image generation to a ``BaseCompletionProvider`` subclass.

    Consumes the retry handler, rate limiter, clock, and provider label
    that ``BaseCompletionProvider`` provides (declared below for the type
    checker). Drivers override :meth:`_do_generate_image`; the default
    raises :class:`ProviderImageGenerationUnsupportedError`.
    """

    _retry_handler: RetryHandler | None
    _rate_limiter: RateLimiter | None
    _clock: Clock

    if TYPE_CHECKING:
        # Declared for the type checker only: the real implementation comes
        # from ``BaseCompletionProvider``. A concrete body here would sit
        # ahead of the base in the MRO and shadow it at runtime.
        def _provider_label(self) -> str: ...

    async def generate_image(
        self,
        prompt: str,
        model: str,
        *,
        config: ImageGenerationConfig | None = None,
    ) -> ImageGenerationResponse:
        """Validate inputs, delegate to ``_do_generate_image``.

        Applies rate limiting, retry, and cost recording exactly like
        ``complete``. Drivers that cannot generate images inherit the
        default hook, which raises
        :class:`ProviderImageGenerationUnsupportedError`.

        Args:
            prompt: Text description of the image to generate.
            model: Image-capable model identifier.
            config: Optional image-generation parameters.

        Returns:
            The generated image response.

        Raises:
            InvalidRequestError: If ``prompt`` or ``model`` is blank.
            ProviderImageGenerationUnsupportedError: If the driver/model
                cannot generate images.
            RetryExhaustedError: If all retries are exhausted.
        """
        validate_model(model)
        if not prompt or not prompt.strip():
            msg = "prompt must not be blank"
            raise InvalidRequestError(msg, context={"model": model})

        async def _attempt() -> ImageGenerationResponse:
            """Run one rate-limited ``_do_generate_image`` attempt.

            Returns:
                The driver's ``ImageGenerationResponse`` for this attempt.
            """
            return await rate_limited_call(
                self._rate_limiter,
                self._do_generate_image,
                model,
                prompt,
                model,
                config=config,
            )

        provider_label = self._provider_label()
        span_attributes = build_call_span_attributes(
            provider_label=provider_label,
            model=model,
            message_count=1,
            tool_count=0,
        )
        with _tracer.start_as_current_span(
            "provider.generate_image",
            attributes=span_attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            t_start = self._clock.monotonic()
            try:
                result = await resilient_execute(
                    _attempt, retry_handler=self._retry_handler
                )
            except Exception as exc:
                reraise_critical(exc)
                latency_ms = (
                    self._clock.monotonic() - t_start
                ) * _MILLISECONDS_PER_SECOND
                record_call_failure(
                    span,
                    exc,
                    model=model,
                    provider_label=provider_label,
                    call_type="generate_image",
                    latency_ms=latency_ms,
                )
                raise
            latency_ms = (self._clock.monotonic() - t_start) * _MILLISECONDS_PER_SECOND
            record_call_success(
                span,
                provider_label=provider_label,
                model=model,
                call_type="generate_image",
                latency_ms=latency_ms,
            )

        merged = dict(result.provider_metadata or {})
        merged["_synthorg_latency_ms"] = latency_ms
        result = result.model_copy(update={"provider_metadata": merged})
        await record_image_cost_if_in_scope(
            result.usage, model=model, provider=provider_label
        )
        return result

    async def _do_generate_image(
        self,
        prompt: str,  # noqa: ARG002 -- hook contract, unused in the default
        model: str,
        *,
        config: ImageGenerationConfig | None = None,  # noqa: ARG002 -- hook contract
    ) -> ImageGenerationResponse:
        """Provider-specific image generation (unsupported by default).

        Image-capable drivers override this and **must** re-raise provider
        errors as ``ProviderError`` subclasses.

        Raises:
            ProviderImageGenerationUnsupportedError: Always, in the default.
        """
        raise ProviderImageGenerationUnsupportedError(
            ProviderImageGenerationUnsupportedError.default_message,
            context={"provider": self._provider_label(), "model": model},
        )
