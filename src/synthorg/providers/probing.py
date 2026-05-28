"""URL probing for LLM provider preset candidate URLs.

Tries each candidate URL in priority order and returns the first
reachable one with discovered model count (single round-trip per
candidate).  SSRF validation is intentionally skipped because
candidate URLs come from hardcoded preset definitions.
"""

import json
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict, Field

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import strip_trailing_slash
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_PROBE_COMPLETED,
    PROVIDER_PROBE_HIT,
    PROVIDER_PROBE_MISS,
    PROVIDER_PROBE_STARTED,
)
from synthorg.providers.url_utils import redact_url as _redact_url

logger = get_logger(__name__)

_PROBE_TIMEOUT_SECONDS: Final[float] = 5.0


class ProbeResult(BaseModel):
    """Result of probing a preset's candidate URLs.

    Attributes:
        url: The reachable base URL, or ``None`` if all failed.
        model_count: Number of models discovered at the URL.
        candidates_tried: Number of candidate URLs attempted.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    url: NotBlankStr | None = None
    model_count: int = Field(default=0, ge=0)
    candidates_tried: int = Field(default=0, ge=0)


def _log_probe_miss(
    preset_name: str,
    reason: str,
    url: str,
    *,
    status_code: int | None = None,
    exc: Exception | None = None,
) -> None:
    """Log a probe miss at DEBUG, or WARNING for an unexpected error.

    When *exc* is supplied the miss is an unexpected failure on the
    network code path: log at WARNING with a typed, scrubbed
    description (``error_type`` + ``error``) and never a traceback.
    ``exc_info`` would serialise frame locals that can hold OAuth
    tokens on this network path; the scrubbed description preserves
    diagnosability without the leak.

    Args:
        preset_name: Preset name for context.
        reason: Short reason tag.
        url: URL that was probed (will be redacted).
        status_code: HTTP status code, if applicable.
        exc: The unexpected exception, when this miss is an error.
    """
    kwargs: dict[str, Any] = {
        "preset": preset_name,
        "reason": reason,
        "url": _redact_url(url),
    }
    if status_code is not None:
        kwargs["status_code"] = status_code
    if exc is not None:
        kwargs["error_type"] = type(exc).__name__
        kwargs["error"] = safe_error_description(exc)
        logger.warning(PROVIDER_PROBE_MISS, **kwargs)
    else:
        logger.debug(PROVIDER_PROBE_MISS, **kwargs)


async def _probe_and_fetch(
    url: str,
    preset_name: str,
) -> dict[str, Any] | None:
    """Probe a URL and return its JSON body in a single request.

    Uses a short timeout and does not validate SSRF -- the caller
    is responsible for using only preset-defined candidate URLs.
    Candidate URLs must come from the hardcoded preset definitions
    in ``presets.py`` (``PROVIDER_PRESETS``), never from user input.

    Args:
        url: Full URL to probe (model-listing endpoint, e.g.
            ``/api/tags`` for Ollama, ``/models`` for standard API).
        preset_name: Preset name for logging context.

    Returns:
        Parsed JSON dict on 2xx success, ``None`` otherwise.
    """
    try:
        async with httpx.AsyncClient(
            timeout=_PROBE_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = await client.get(url)
            if not response.is_success:
                _log_probe_miss(
                    preset_name,
                    "http_error",
                    url,
                    status_code=response.status_code,
                )
                return None
            data = response.json()
            if not isinstance(data, dict):
                _log_probe_miss(preset_name, "unexpected_json_type", url)
                return None
            return data
    except httpx.ConnectError:
        _log_probe_miss(preset_name, "connection_refused", url)
    except httpx.TimeoutException:
        _log_probe_miss(preset_name, "timeout", url)
    except json.JSONDecodeError:
        _log_probe_miss(preset_name, "invalid_json", url)
    except Exception as exc:
        reraise_critical(exc)
        _log_probe_miss(preset_name, "unexpected_error", url, exc=exc)
    return None


def _build_probe_endpoint(base_url: str, preset_name: str) -> str:
    """Build the model-listing endpoint URL for probing.

    Args:
        base_url: Provider base URL.
        preset_name: Preset name (determines endpoint path).

    Returns:
        Full URL to the model-listing endpoint.
    """
    stripped = strip_trailing_slash(base_url)
    if preset_name == "ollama":
        return f"{stripped}/api/tags"
    return f"{stripped}/models"


def _build_probe_hit(
    data: dict[str, Any],
    url: str,
    idx: int,
    preset_name: str,
) -> ProbeResult | None:
    """Build a probe result from fetched data, or ``None`` on parse failure.

    If the JSON does not match the expected provider schema (e.g. an
    unrelated health-check response), this returns ``None`` so the
    caller continues probing the next candidate URL.

    Args:
        data: Parsed JSON response body.
        url: The reachable base URL.
        idx: 1-based index of this candidate in the list.
        preset_name: Preset name for parser selection and logging.

    Returns:
        Probe result on success, ``None`` if the payload is not a
        recognizable model-listing response.
    """
    if preset_name == "ollama":
        models = _parse_ollama_models(data)
    else:
        models = _parse_standard_models(data)

    if not models:
        _log_probe_miss(preset_name, "unrecognized_schema", url)
        return None

    logger.info(
        PROVIDER_PROBE_HIT,
        preset=preset_name,
        url=_redact_url(url),
    )
    return ProbeResult(
        url=url,
        model_count=len(models),
        candidates_tried=idx,
    )


async def probe_preset_urls(
    preset_name: str,
) -> ProbeResult:
    """Probe candidate URLs for a preset and return the first reachable one.

    Resolves candidate URLs from the preset registry so only hardcoded
    preset URLs are probed (SSRF validation is skipped).  Tries each URL
    sequentially with a short timeout; the first response is parsed for
    models (single round-trip per candidate).

    Args:
        preset_name: Preset name for endpoint selection and logging.

    Returns:
        Probe result with reachable URL and model count, or empty.
    """
    from synthorg.providers.presets import (  # noqa: PLC0415
        LocalPreset,
        get_preset,
    )

    preset = get_preset(preset_name)
    if not isinstance(preset, LocalPreset):
        # Cloud presets have no auto-detect surface; only LocalPreset
        # carries candidate URLs.
        return ProbeResult()
    candidate_urls = preset.candidate_urls
    if not candidate_urls:
        return ProbeResult()

    logger.info(
        PROVIDER_PROBE_STARTED,
        preset=preset_name,
        candidate_count=len(candidate_urls),
    )

    for idx, url in enumerate(candidate_urls, start=1):
        probe_endpoint = _build_probe_endpoint(url, preset_name)

        data = await _probe_and_fetch(probe_endpoint, preset_name)
        if data is None:
            continue

        result = _build_probe_hit(data, url, idx, preset_name)
        if result is not None:
            return result

    logger.info(
        PROVIDER_PROBE_COMPLETED,
        preset=preset_name,
        url=None,
        model_count=0,
        candidates_tried=len(candidate_urls),
    )
    return ProbeResult(candidates_tried=len(candidate_urls))


def _parse_ollama_models(
    data: dict[str, Any],
) -> tuple[ProviderModelConfig, ...] | None:
    """Parse Ollama model list response.

    Args:
        data: Parsed JSON response from ``/api/tags``.

    Returns:
        Tuple of model configs, or ``None`` if the response does not
        contain a ``models`` list (unrecognized schema).
    """
    raw_models = data.get("models")
    if not isinstance(raw_models, list):
        return None
    models: list[ProviderModelConfig] = []
    for entry in raw_models:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        models.append(ProviderModelConfig(id=name))
    return tuple(models)


def _parse_standard_models(
    data: dict[str, Any],
) -> tuple[ProviderModelConfig, ...] | None:
    """Parse standard ``/models`` list response.

    Args:
        data: Parsed JSON response from ``/models``.

    Returns:
        Tuple of model configs, or ``None`` if the response does not
        contain a ``data`` list (unrecognized schema).
    """
    raw_data = data.get("data")
    if not isinstance(raw_data, list):
        return None
    models: list[ProviderModelConfig] = []
    for entry in raw_data:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        models.append(ProviderModelConfig(id=model_id))
    return tuple(models)
