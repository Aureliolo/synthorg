"""Tests for ``BaseCompletionProvider.generate_image`` and image cost.

The base method mirrors ``complete``: it rate-limits + retries the driver
hook, records per-image cost through the ambient cost scope, and stamps
latency metadata. Drivers that do not override the hook raise
``ProviderImageGenerationUnsupportedError`` from the concrete default.
"""

from collections.abc import AsyncIterator
from typing import override

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.config import BudgetConfig
from synthorg.budget.tracker import CostTracker
from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.providers._cost import compute_image_cost
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.errors import (
    InvalidRequestError,
    ProviderImageGenerationUnsupportedError,
)
from synthorg.providers.image_generation import ImageGenerationMixin
from synthorg.providers.image_models import (
    GeneratedImage,
    ImageGenerationConfig,
    ImageGenerationResponse,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)

pytestmark = pytest.mark.unit

# 1x1 transparent PNG, base64 (no data: prefix).
_PNG_B64: NotBlankStr = NotBlankStr(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class _NoImageProvider(ImageGenerationMixin, BaseCompletionProvider):
    """Provider that does not override the image hook (uses the default)."""

    @override
    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        return CompletionResponse(
            content="hi",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=0, output_tokens=0, cost=0.0),
            model=model,
        )

    @override
    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        async def _gen() -> AsyncIterator[StreamChunk]:
            return
            yield

        return _gen()

    @override
    async def _do_get_model_capabilities(self, model: str) -> ModelCapabilities:
        msg = "not implemented"
        raise NotImplementedError(msg)


class _ImageProvider(_NoImageProvider):
    """Provider that generates a canned image, priced at ``cost`` per image."""

    def __init__(self, *, cost_per_image: float = 0.04) -> None:
        super().__init__()
        self._cost_per_image = cost_per_image

    @override
    async def _do_generate_image(
        self,
        prompt: str,
        model: str,
        *,
        config: ImageGenerationConfig | None = None,
    ) -> ImageGenerationResponse:
        n = config.n if config is not None else 1
        return ImageGenerationResponse(
            images=tuple(
                GeneratedImage(b64_data=_PNG_B64, revised_prompt=prompt)
                for _ in range(n)
            ),
            usage=compute_image_cost(n, cost_per_image=self._cost_per_image),
            model=NotBlankStr(model),
        )


def test_compute_image_cost_zero_tokens_nonzero_cost() -> None:
    usage = compute_image_cost(2, cost_per_image=0.04)
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cost == pytest.approx(0.08)


@pytest.mark.parametrize(("n", "cost"), [(0, 0.04), (1, -0.01), (1, float("inf"))])
def test_compute_image_cost_rejects_invalid(n: int, cost: float) -> None:
    with pytest.raises(InvalidRequestError):
        compute_image_cost(n, cost_per_image=cost)


def test_image_config_rejects_bad_size() -> None:
    with pytest.raises(ValueError, match="size"):
        ImageGenerationConfig(size="1024")


def test_image_config_rejects_excessive_n() -> None:
    with pytest.raises(ValueError, match="n"):
        ImageGenerationConfig(n=999)


async def test_unsupported_provider_raises() -> None:
    provider = _NoImageProvider()
    with pytest.raises(ProviderImageGenerationUnsupportedError):
        await provider.generate_image("a cat", "test-model")


async def test_generate_image_returns_images_and_latency() -> None:
    provider = _ImageProvider()
    result = await provider.generate_image(
        "a sunset", "img-model", config=ImageGenerationConfig(n=2)
    )
    assert len(result.images) == 2
    assert result.images[0].content_type == "image/png"
    assert result.images[0].revised_prompt == "a sunset"
    assert result.usage.cost == pytest.approx(0.08)
    assert "_synthorg_latency_ms" in result.provider_metadata


async def test_generate_image_blank_prompt_rejected() -> None:
    provider = _ImageProvider()
    with pytest.raises(InvalidRequestError):
        await provider.generate_image("   ", "img-model")


async def test_generate_image_blank_model_rejected() -> None:
    provider = _ImageProvider()
    with pytest.raises(InvalidRequestError):
        await provider.generate_image("a cat", "  ")


async def test_generate_image_records_cost_in_scope() -> None:
    provider = _ImageProvider(cost_per_image=0.05)
    tracker = CostTracker(budget_config=BudgetConfig())
    async with cost_recording_scope(
        cost_tracker=tracker,
        agent_id=NotBlankStr("agent-1"),
        task_id=NotBlankStr("task-1"),
        call_category=LLMCallCategory.IMAGE_GENERATION,
    ):
        await provider.generate_image("a cat", "img-model")
    await tracker.drain_pending_records()
    assert await tracker.get_total_cost() == pytest.approx(0.05)
    records = await tracker.get_records()
    assert len(records) == 1
    assert records[0].call_category is LLMCallCategory.IMAGE_GENERATION
