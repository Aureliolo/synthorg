"""Image generator tool -- generate images via an abstracted provider.

The ``ImageProvider`` protocol defines a vendor-agnostic interface
for image generation.  No concrete implementation is shipped -- users
inject a provider at construction time.
"""

import asyncio
import base64
from typing import ClassVar, Final, Protocol, cast, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.design import (
    DESIGN_IMAGE_GENERATION_FAILED,
    DESIGN_IMAGE_GENERATION_START,
    DESIGN_IMAGE_GENERATION_SUCCESS,
    DESIGN_IMAGE_GENERATION_TIMEOUT,
    DESIGN_PROVIDER_NOT_CONFIGURED,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.design._args import ImageGeneratorArgs
from synthorg.tools.design.base_design_tool import BaseDesignTool
from synthorg.tools.design.config import DesignToolsConfig

logger = get_logger(__name__)
_DEFAULT_WIDTH: Final[int] = 1024
_DEFAULT_HEIGHT: Final[int] = 1024


class ImageResult(BaseModel):
    """Result from an image generation provider.

    Attributes:
        data: Raw image bytes (base64-encoded string).
        content_type: MIME type of the generated image.
        width: Image width in pixels.
        height: Image height in pixels.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    data: str = Field(min_length=1, description="Base64-encoded image data")
    content_type: str = Field(
        default="image/png",
        description="MIME type of the generated image",
    )
    width: int = Field(gt=0, description="Image width in pixels")
    height: int = Field(gt=0, description="Image height in pixels")


@runtime_checkable
class ImageProvider(Protocol):
    """Abstracted image generation provider protocol.

    Implementations must be async and return an ``ImageResult``.
    """

    async def generate(
        self,
        *,
        prompt: str,
        width: int = _DEFAULT_WIDTH,
        height: int = _DEFAULT_HEIGHT,
        style: str = "realistic",
        quality: str = "standard",
    ) -> ImageResult:
        """Generate an image from a text prompt.

        Args:
            prompt: Image description.
            width: Image width in pixels.
            height: Image height in pixels.
            style: Image style preset.
            quality: Image quality preset.

        Returns:
            Generated image result.
        """
        ...


_VALID_STYLES: Final[frozenset[str]] = frozenset(
    {"realistic", "sketch", "diagram", "icon"}
)
_VALID_QUALITIES: Final[frozenset[str]] = frozenset({"draft", "standard", "high"})

_MIN_DIMENSION: Final[int] = 256
_MAX_DIMENSION: Final[int] = 2048


class ImageGeneratorTool(BaseDesignTool):
    """Generate images from text prompts via an abstracted provider.

    Requires an ``ImageProvider`` implementation to be injected at
    construction time.  If no provider is configured, the tool
    returns an error explaining the requirement.

    Examples:
        Generate an image::

            tool = ImageGeneratorTool(provider=my_provider)
            result = await tool.execute(arguments={"prompt": "A sunset over mountains"})
    """

    args_model: ClassVar[type[BaseModel] | None] = ImageGeneratorArgs

    def __init__(
        self,
        *,
        provider: ImageProvider | None = None,
        config: DesignToolsConfig | None = None,
    ) -> None:
        """Initialize the image generator tool.

        Args:
            provider: Image generation backend. ``None`` makes
                ``execute`` return a configuration error.
            config: Design tool configuration with prompt-length and
                size caps. ``None`` falls back to defaults.
        """
        super().__init__(
            name="image_generator",
            description=(
                "Generate images from text prompts. Supports style and quality presets."
            ),
            parameters_schema=ImageGeneratorArgs.model_json_schema(),
            action_type=ActionType.DOCS_WRITE,
            config=config,
        )
        self._provider = provider

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Generate an image from a text prompt.

        Args:
            arguments: Must contain ``prompt``; optionally ``style``,
                ``width``, ``height``, ``quality``.

        Returns:
            A ``ToolExecutionResult`` with image data or error.
        """
        if self._provider is None:
            logger.warning(
                DESIGN_PROVIDER_NOT_CONFIGURED,
                tool="image_generator",
            )
            return ToolExecutionResult(
                content=(
                    "Image generation requires a configured provider. "
                    "No ImageProvider has been injected."
                ),
                is_error=True,
            )

        prompt = cast("str", arguments["prompt"])
        style = cast("str", arguments.get("style", "realistic"))
        width = cast("int", arguments.get("width", 1024))
        height = cast("int", arguments.get("height", 1024))
        quality = cast("str", arguments.get("quality", "standard"))

        if not (_MIN_DIMENSION <= width <= _MAX_DIMENSION) or not (
            _MIN_DIMENSION <= height <= _MAX_DIMENSION
        ):
            logger.warning(
                DESIGN_IMAGE_GENERATION_FAILED,
                error="invalid_dimensions",
                width=width,
                height=height,
            )
            return ToolExecutionResult(
                content=(
                    f"Width and height must be between "
                    f"{_MIN_DIMENSION} and {_MAX_DIMENSION}. "
                    f"Got width={width}, height={height}."
                ),
                is_error=True,
            )

        if style not in _VALID_STYLES:
            logger.warning(
                DESIGN_IMAGE_GENERATION_FAILED,
                error="invalid_style",
                style=style,
            )
            return ToolExecutionResult(
                content=(
                    f"Invalid style: {style!r}. Must be one of: {sorted(_VALID_STYLES)}"
                ),
                is_error=True,
            )

        if quality not in _VALID_QUALITIES:
            logger.warning(
                DESIGN_IMAGE_GENERATION_FAILED,
                error="invalid_quality",
                quality=quality,
            )
            return ToolExecutionResult(
                content=(
                    f"Invalid quality: {quality!r}. "
                    f"Must be one of: {sorted(_VALID_QUALITIES)}"
                ),
                is_error=True,
            )

        logger.info(
            DESIGN_IMAGE_GENERATION_START,
            prompt_length=len(prompt),
            style=style,
            width=width,
            height=height,
            quality=quality,
        )

        try:
            result = await asyncio.wait_for(
                self._provider.generate(
                    prompt=prompt,
                    width=width,
                    height=height,
                    style=style,
                    quality=quality,
                ),
                timeout=self._config.image_timeout,
            )
        except TimeoutError:
            logger.warning(
                DESIGN_IMAGE_GENERATION_TIMEOUT,
                timeout=self._config.image_timeout,
                prompt_length=len(prompt),
            )
            return ToolExecutionResult(
                content=(
                    f"Image generation timed out after {self._config.image_timeout}s"
                ),
                is_error=True,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                DESIGN_IMAGE_GENERATION_FAILED,
                error="provider_error",
                prompt_length=len(prompt),
                style=style,
            )
            return ToolExecutionResult(
                content="Image generation failed.",
                is_error=True,
            )

        try:
            decoded_bytes = base64.b64decode(result.data, validate=True)
        except Exception as decode_exc:
            reraise_critical(decode_exc)
            logger.warning(
                DESIGN_IMAGE_GENERATION_FAILED,
                error="invalid_base64",
                error_type=type(decode_exc).__name__,
                error_detail=safe_error_description(decode_exc),
            )
            return ToolExecutionResult(
                content="Provider returned invalid base64 image data.",
                is_error=True,
            )
        byte_size = len(decoded_bytes)
        if byte_size > self._config.max_image_size_bytes:
            logger.warning(
                DESIGN_IMAGE_GENERATION_FAILED,
                error="image_too_large",
                byte_size=byte_size,
                max_size=self._config.max_image_size_bytes,
            )
            return ToolExecutionResult(
                content=(
                    f"Generated image exceeds size limit: "
                    f"{byte_size} bytes "
                    f"(max {self._config.max_image_size_bytes})"
                ),
                is_error=True,
            )

        logger.info(
            DESIGN_IMAGE_GENERATION_SUCCESS,
            width=result.width,
            height=result.height,
            content_type=result.content_type,
            data_length=len(result.data),
        )

        return ToolExecutionResult(
            content=(
                f"Image generated successfully.\n"
                f"Dimensions: {result.width}x{result.height}\n"
                f"Type: {result.content_type}\n"
                f"Data length: {len(result.data)} chars (base64)"
            ),
            metadata={
                "data": result.data,
                "content_type": result.content_type,
                "width": result.width,
                "height": result.height,
            },
        )
