# module-kind: code
"""LiteLLM image-generation response mapping.

Isolates the mapping from LiteLLM's ``ImageResponse`` to the domain
``ImageGenerationResponse`` (and the per-image cost computation) so
``litellm_driver`` stays within its size budget and focused on dispatch.
"""

import base64
import binascii
from collections.abc import Callable
from typing import Final

import litellm as _litellm

from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.providers import errors
from synthorg.providers._cost import compute_image_cost
from synthorg.providers.drivers.litellm_auth import AuthContext, apply_auth_kwargs
from synthorg.providers.drivers.litellm_kwargs import _AcompletionKwargs
from synthorg.providers.image_models import (
    GeneratedImage,
    ImageGenerationConfig,
    ImageGenerationResponse,
)

_RESPONSE_FORMAT_B64: str = "b64_json"
_DEFAULT_IMAGE_MIME: Final[str] = "image/png"
# Enough base64 chars to decode the 12 bytes an image signature needs
# (WEBP checks bytes 8-11); a multiple of 4 so the prefix decodes cleanly.
_MAGIC_PREFIX_B64_CHARS: Final[int] = 16


def _sniff_content_type(b64: str) -> str:
    """Detect the image MIME type from a base64 payload's magic bytes.

    Decodes only a short prefix (enough for the signature), not the whole
    image, and falls back to PNG for an unrecognised signature so a
    future JPEG/WEBP provider is not written with the wrong extension.

    Returns:
        The detected MIME type, or ``"image/png"`` when unrecognised.
    """
    try:
        head = base64.b64decode(b64[:_MAGIC_PREFIX_B64_CHARS], validate=True)
    except ValueError, binascii.Error:
        return _DEFAULT_IMAGE_MIME
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return _DEFAULT_IMAGE_MIME


async def generate_image_via_litellm(  # noqa: PLR0913 -- keyword-only driver state
    *,
    map_exception: Callable[[Exception, str], errors.ProviderError],
    provider_config: ProviderConfig,
    resolved_credentials: dict[str, str] | None,
    catalog_present: bool,
    provider_name: str,
    routing_key: str,
    model: str,
    model_config: ProviderModelConfig,
    prompt: str,
    config: ImageGenerationConfig | None,
) -> ImageGenerationResponse:
    """Dispatch a LiteLLM image call and map the response.

    Owns kwargs building, the call, provider-error mapping, and response
    mapping so the driver method stays thin resolve-then-delegate glue.

    Returns:
        The decoded ``ImageGenerationResponse``.

    Raises:
        ProviderError: Re-raised directly, or mapped via ``map_exception``.
    """
    cfg = config if config is not None else ImageGenerationConfig()
    litellm_model = f"{routing_key}/{model_config.id}"
    kwargs = build_image_kwargs(
        provider_config=provider_config,
        resolved_credentials=resolved_credentials,
        catalog_present=catalog_present,
        provider_name=provider_name,
        prompt=prompt,
        litellm_model=litellm_model,
        image_config=cfg,
    )
    try:
        response = await _litellm.aimage_generation(**kwargs)
    except errors.ProviderError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        raise map_exception(exc, model) from exc
    return map_image_response(
        response,
        model_id=model_config.id,
        cost_per_image=model_config.cost_per_image or 0.0,
    )


def build_image_kwargs(  # noqa: PLR0913 -- keyword-only driver state
    *,
    provider_config: ProviderConfig,
    resolved_credentials: dict[str, str] | None,
    catalog_present: bool,
    provider_name: str,
    prompt: str,
    litellm_model: str,
    image_config: ImageGenerationConfig,
) -> dict[str, object]:
    """Build keyword arguments for ``litellm.aimage_generation``.

    Requests ``response_format="b64_json"`` so images arrive inline; auth
    and base URL are applied the same way as completion calls.

    Returns:
        A kwargs dict for ``litellm.aimage_generation``.
    """
    kwargs: dict[str, object] = {
        "model": litellm_model,
        "prompt": prompt,
        "size": image_config.size,
        "n": image_config.n,
        "response_format": _RESPONSE_FORMAT_B64,
    }
    if image_config.quality is not None:
        kwargs["quality"] = image_config.quality
    if image_config.style is not None:
        kwargs["style"] = image_config.style
    if image_config.timeout is not None:
        kwargs["timeout"] = image_config.timeout
    # ``apply_auth_kwargs`` is typed against the completion TypedDict, so
    # resolve auth onto a minimal valid holder and copy the two keys it may
    # set (``api_key`` / ``extra_headers``) onto the image request.
    auth_holder: _AcompletionKwargs = {"model": litellm_model, "messages": []}
    apply_auth_kwargs(
        auth_holder,
        AuthContext(
            config=provider_config,
            resolved=resolved_credentials,
            catalog_present=catalog_present,
            provider_name=provider_name,
            litellm_model=litellm_model,
        ),
    )
    if "api_key" in auth_holder:
        kwargs["api_key"] = auth_holder["api_key"]
    if "extra_headers" in auth_holder:
        kwargs["extra_headers"] = auth_holder["extra_headers"]
    if provider_config.base_url is not None:
        kwargs["api_base"] = provider_config.base_url
    return kwargs


def map_image_response(
    response: object,
    *,
    model_id: str,
    cost_per_image: float,
) -> ImageGenerationResponse:
    """Map a LiteLLM ``ImageResponse`` to ``ImageGenerationResponse``.

    Reads the base64 payload from each ``data[i].b64_json`` (the driver
    requests ``response_format="b64_json"`` so images arrive inline rather
    than as URLs). Cost is per-image, computed from ``cost_per_image``.

    Args:
        response: The LiteLLM ``ImageResponse`` (duck-typed).
        model_id: The bare model id that served the request.
        cost_per_image: Flat per-image cost in the configured currency.

    Returns:
        A populated ``ImageGenerationResponse``.

    Raises:
        ProviderInternalError: If the response carries no inline base64
            image data (e.g. a URL-only provider response).
    """
    data = getattr(response, "data", None) or []
    images: list[GeneratedImage] = []
    for obj in data:
        b64 = getattr(obj, "b64_json", None)
        if not b64:
            continue
        revised = getattr(obj, "revised_prompt", None) or None
        images.append(
            GeneratedImage(
                b64_data=NotBlankStr(b64),
                content_type=_sniff_content_type(b64),
                revised_prompt=revised,
            )
        )
    if not images:
        msg = "Provider returned no inline base64 image data"
        raise errors.ProviderInternalError(msg, context={"model": model_id})
    usage = compute_image_cost(len(images), cost_per_image=cost_per_image)
    return ImageGenerationResponse(
        images=tuple(images),
        usage=usage,
        model=NotBlankStr(model_id),
    )
