"""Image generator tool -- generate images via an abstracted provider.

The ``ImageProvider`` protocol defines a vendor-agnostic interface for
image generation.  ``ProviderImageProvider`` ships as the default
in-tree implementation, routing generation through the normal provider
+ model-management layer; it is injected automatically at boot when
``design.image_generation_enabled`` selects an image-capable model.
Callers may inject any other ``ImageProvider`` at construction time.
"""

import asyncio
import base64
import hashlib
from typing import ClassVar, Final, Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.design import (
    DESIGN_ASSET_PERSIST_FAILED,
    DESIGN_IMAGE_GENERATION_FAILED,
    DESIGN_IMAGE_GENERATION_START,
    DESIGN_IMAGE_GENERATION_SUCCESS,
    DESIGN_IMAGE_GENERATION_TIMEOUT,
    DESIGN_PROVIDER_NOT_CONFIGURED,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.design._args import ImageGeneratorArgs
from synthorg.tools.design.asset_store import (
    DesignAssetStore,
    InMemoryDesignAssetStore,
)
from synthorg.tools.design.base_design_tool import BaseDesignTool
from synthorg.tools.design.config import DesignToolsConfig

_ASSET_ID_HASH_LEN: Final[int] = 16
# Base64 encodes 3 bytes as 4 characters, so decoded_len ~= b64_len * 3 / 4.
_B64_NUMERATOR: Final[int] = 3
_B64_DENOMINATOR: Final[int] = 4

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

    data: NotBlankStr = Field(description="Base64-encoded image data")
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
    cost_scope_category: ClassVar[LLMCallCategory | None] = (
        LLMCallCategory.IMAGE_GENERATION
    )

    def __init__(
        self,
        *,
        provider: ImageProvider | None = None,
        config: DesignToolsConfig | None = None,
        store: DesignAssetStore | None = None,
    ) -> None:
        """Initialize the image generator tool.

        Args:
            provider: Image generation backend. ``None`` makes
                ``execute`` return a configuration error.
            config: Design tool configuration with prompt-length and
                size caps. ``None`` falls back to defaults.
            store: Asset store the generated image is persisted to.
                ``None`` uses an in-memory store; the factory injects a
                durable filesystem store when ``asset_storage_path`` is set.
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
        self._store: DesignAssetStore = (
            store if store is not None else InMemoryDesignAssetStore()
        )

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

        args = parse_typed("tool.execute", arguments, ImageGeneratorArgs)
        logger.info(
            DESIGN_IMAGE_GENERATION_START,
            prompt_length=len(args.prompt),
            style=args.style,
            width=args.width,
            height=args.height,
            quality=args.quality,
        )

        result = await self._invoke_provider(args)
        if isinstance(result, ToolExecutionResult):
            return result
        decoded = self._decode_and_check(result)
        if isinstance(decoded, ToolExecutionResult):
            return decoded
        return await self._store_and_report(args, result, decoded)

    async def _store_and_report(
        self,
        args: ImageGeneratorArgs,
        result: ImageResult,
        decoded: bytes,
    ) -> ToolExecutionResult:
        """Persist the decoded image and build the success/failure result.

        Returns:
            The success ``ToolExecutionResult``, or the failure result from
            ``_persist_asset`` when storage failed.
        """
        digest = hashlib.sha256(decoded).hexdigest()[:_ASSET_ID_HASH_LEN]
        asset_id = f"img-{digest}"
        byte_size = len(decoded)
        asset_metadata: dict[str, JsonValue] = {
            "type": "image",
            "content_type": result.content_type,
            "width": result.width,
            "height": result.height,
            "size_bytes": byte_size,
            "prompt": args.prompt,
            "style": args.style,
            "tags": ["image", args.style],
        }
        failure = await self._persist_asset(
            asset_id=asset_id,
            decoded=decoded,
            metadata=asset_metadata,
            result=result,
        )
        if failure is not None:
            return failure

        logger.info(
            DESIGN_IMAGE_GENERATION_SUCCESS,
            asset_id=asset_id,
            width=result.width,
            height=result.height,
            content_type=result.content_type,
            data_length=len(result.data),
        )
        return ToolExecutionResult(
            content=(
                f"Image generated successfully.\n"
                f"Asset ID: {asset_id}\n"
                f"Dimensions: {result.width}x{result.height}\n"
                f"Type: {result.content_type}\n"
                f"Size: {byte_size} bytes"
            ),
            metadata={
                "asset_id": asset_id,
                "data": result.data,
                "content_type": result.content_type,
                "width": result.width,
                "height": result.height,
                "size_bytes": byte_size,
            },
        )

    async def _invoke_provider(
        self,
        args: ImageGeneratorArgs,
    ) -> ImageResult | ToolExecutionResult:
        """Call the provider under a timeout, mapping failures to a result.

        Returns:
            The provider's ``ImageResult``, or a ``ToolExecutionResult``
            describing a timeout or provider error.
        """
        assert self._provider is not None  # noqa: S101 -- caller-guarded
        try:
            return await asyncio.wait_for(
                self._provider.generate(
                    prompt=args.prompt,
                    width=args.width,
                    height=args.height,
                    style=args.style,
                    quality=args.quality,
                ),
                timeout=self._config.image_timeout,
            )
        except TimeoutError:
            logger.warning(
                DESIGN_IMAGE_GENERATION_TIMEOUT,
                timeout=self._config.image_timeout,
                prompt_length=len(args.prompt),
            )
            return ToolExecutionResult(
                content=(
                    f"Image generation timed out after {self._config.image_timeout}s"
                ),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DESIGN_IMAGE_GENERATION_FAILED,
                error="provider_error",
                error_type=type(exc).__name__,
                error_detail=safe_error_description(exc),
                prompt_length=len(args.prompt),
                style=args.style,
            )
            return ToolExecutionResult(
                content="Image generation failed.",
                is_error=True,
            )

    def _decode_and_check(
        self,
        result: ImageResult,
    ) -> bytes | ToolExecutionResult:
        """Decode the base64 payload and enforce the size cap.

        The base64 length is checked before decoding so an oversized
        payload is rejected without allocating the full decoded buffer.

        Returns:
            The decoded bytes, or a ``ToolExecutionResult`` on invalid
            base64 or an over-cap image.
        """
        max_bytes = self._config.max_image_size_bytes
        # Cheap upper bound: decoded size is ~3/4 of the base64 length.
        if (len(result.data) * _B64_NUMERATOR) // _B64_DENOMINATOR > max_bytes:
            logger.warning(
                DESIGN_IMAGE_GENERATION_FAILED,
                error="image_too_large",
                base64_length=len(result.data),
                max_size=max_bytes,
            )
            return ToolExecutionResult(
                content=f"Generated image exceeds size limit (max {max_bytes} bytes)",
                is_error=True,
            )
        try:
            decoded = base64.b64decode(result.data, validate=True)
        except Exception as decode_exc:  # noqa: BLE001 -- criticals re-raised
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
        if len(decoded) > max_bytes:
            logger.warning(
                DESIGN_IMAGE_GENERATION_FAILED,
                error="image_too_large",
                byte_size=len(decoded),
                max_size=max_bytes,
            )
            return ToolExecutionResult(
                content=(
                    f"Generated image exceeds size limit: "
                    f"{len(decoded)} bytes (max {max_bytes})"
                ),
                is_error=True,
            )
        return decoded

    async def _persist_asset(
        self,
        *,
        asset_id: str,
        decoded: bytes,
        metadata: dict[str, JsonValue],
        result: ImageResult,
    ) -> ToolExecutionResult | None:
        """Persist content + metadata off-thread; return an error result or None.

        Both writes run off the event loop. On any I/O failure the
        already-generated (already-billed) image is not silently lost: a
        best-effort cleanup drops a half-written asset and the returned
        error result carries the image data inline so the caller can still
        use it.

        Returns:
            ``None`` on success, or an error ``ToolExecutionResult`` when
            persistence failed.
        """
        try:
            await asyncio.to_thread(
                self._store.save_content,
                asset_id,
                decoded,
                content_type=result.content_type,
            )
            await asyncio.to_thread(self._store.register, asset_id, metadata)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.error(
                DESIGN_ASSET_PERSIST_FAILED,
                asset_id=asset_id,
                content_type=result.content_type,
                byte_size=len(decoded),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            try:
                await asyncio.to_thread(self._store.delete, asset_id)
            except Exception as cleanup_exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(cleanup_exc)
                logger.warning(
                    DESIGN_ASSET_PERSIST_FAILED,
                    asset_id=asset_id,
                    reason="cleanup_failed",
                    error_type=type(cleanup_exc).__name__,
                    error=safe_error_description(cleanup_exc),
                )
            return ToolExecutionResult(
                content=(
                    "Image generated but could not be saved to storage. "
                    "The image data is returned inline; it was not persisted."
                ),
                metadata={
                    "data": result.data,
                    "content_type": result.content_type,
                    "width": result.width,
                    "height": result.height,
                    "size_bytes": len(decoded),
                },
                is_error=True,
            )
        return None
