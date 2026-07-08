# module-kind: adapter
"""Adapt a completion provider's image capability to the ``ImageProvider`` seam.

Bridges the provider layer (``CompletionProvider.generate_image``) to the
design-tool ``ImageProvider`` protocol so a running agent's
``image_generator`` tool routes image generation through the normal
provider + model-management layer (a hosted image-capable provider, or
the offline scripted provider).

Kept out of ``tools.design.__init__`` and imported lazily at boot so the
design package's eager import chain never pulls the providers layer.
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.types import require_not_blank
from synthorg.observability import get_logger
from synthorg.observability.events.design import DESIGN_IMAGE_PROVIDER_BOUND
from synthorg.providers.image_models import ImageGenerationConfig
from synthorg.tools.design.image_generator import ImageResult

if TYPE_CHECKING:
    from synthorg.providers.image_generation import ImageGenerationProvider

logger = get_logger(__name__)

_DEFAULT_WIDTH: Final[int] = 1024
_DEFAULT_HEIGHT: Final[int] = 1024


class ProviderImageProvider:
    """Expose a completion provider's image model as an ``ImageProvider``.

    Satisfies the design-tool ``ImageProvider`` protocol structurally.
    ``style`` / ``quality`` are intentionally not forwarded: the design-tool
    vocabulary differs from each provider's presets, so forwarding an
    unknown value would make hosted providers reject the request; the size
    is the portable parameter. A provider whose model cannot generate images
    raises ``ProviderImageGenerationUnsupportedError`` from ``generate_image``.
    """

    def __init__(self, *, provider: ImageGenerationProvider, model: str) -> None:
        """Bind the adapter to a provider and an image-capable model id.

        Args:
            provider: The image-capable provider serving the model.
            model: The image-capable model identifier (non-blank).

        Raises:
            ValueError: If ``model`` is blank.
        """
        self._provider = provider
        self._model = require_not_blank(model, "model")
        logger.debug(DESIGN_IMAGE_PROVIDER_BOUND, model=self._model)

    async def generate(
        self,
        *,
        prompt: str,
        width: int = _DEFAULT_WIDTH,
        height: int = _DEFAULT_HEIGHT,
        style: str = "realistic",
        quality: str = "standard",
    ) -> ImageResult:
        """Generate an image via the provider and map it to an ``ImageResult``.

        Args:
            prompt: Image description.
            width: Requested width in pixels.
            height: Requested height in pixels.
            style: Design-tool style preset (not forwarded; see class docs).
            quality: Design-tool quality preset (not forwarded).

        Returns:
            The first generated image as an ``ImageResult``.
        """
        del style, quality
        response = await self._provider.generate_image(
            prompt,
            self._model,
            config=ImageGenerationConfig(size=f"{width}x{height}"),
        )
        image = response.images[0]
        return ImageResult(
            data=image.b64_data,
            content_type=image.content_type,
            width=width,
            height=height,
        )
