"""URL probing for LLM provider preset candidate URLs.

Tries each candidate URL in priority order and returns the first
reachable one with discovered model count (single round-trip per
candidate).  SSRF validation is intentionally skipped because
candidate URLs come from hardcoded preset definitions.
"""

import asyncio
import json
from collections.abc import Mapping
from typing import Final, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.config.model_metadata import ModelMetadata
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
from synthorg.providers.ollama_identity import parse_ollama_identity
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
    kwargs: dict[str, str | int | None] = {
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
) -> dict[str, JsonValue] | None:
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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
    data: dict[str, JsonValue],
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
    data: dict[str, JsonValue],
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


def parse_ollama_show(
    model_id: str,
    show: Mapping[str, JsonValue],
) -> ProviderModelConfig:
    """Build an enriched model config from an Ollama ``/api/show`` response.

    Ollama's ``/api/tags`` listing carries no capability data, so a bare
    ``ProviderModelConfig(id=...)`` inherits the all-False capability defaults
    and the model matcher's fail-closed hard filters reject it for every
    tool/vision/reasoning-requiring agent. ``/api/show`` exposes the real
    ``capabilities`` array (``tools`` / ``vision`` / ``thinking``) and the
    architecture's ``context_length``; project them onto the metadata so the
    matcher can classify the model honestly.

    Args:
        model_id: The model name from the ``/api/tags`` listing.
        show: Parsed ``/api/show`` JSON for that model.

    Returns:
        Enriched model config; capability-less (listing defaults) when the
        response omits ``capabilities`` rather than guessing.
    """
    raw_caps = show.get("capabilities")
    caps = (
        {c for c in raw_caps if isinstance(c, str)}
        if isinstance(raw_caps, list)
        else set()
    )
    family, generation = parse_ollama_identity(model_id)
    metadata = ModelMetadata(
        supports_tools="tools" in caps,
        supports_vision="vision" in caps,
        supports_reasoning="thinking" in caps,
        # Parameter count is a coarse size/strength signal the matcher uses to
        # rank quality so a frontier cloud model beats a small local one.
        parameter_count=_ollama_parameter_count(show),
        # Family + generation let the matcher group versions of one model line,
        # pin the newest, and spread agents across distinct families.
        family=family,
        generation=generation,
        # "probe" provenance is load-bearing: the matcher's hard filter
        # fail-closes on "unknown"-source metadata even when a capability flag
        # is True (it cannot trust the source), so leaving the default would
        # discard the very capabilities we just read from /api/show.
        metadata_source="probe",
    )
    context_length = _ollama_context_length(show)
    if context_length is None:
        return ProviderModelConfig(id=model_id, metadata=metadata)
    return ProviderModelConfig(
        id=model_id,
        max_context=context_length,
        metadata=metadata,
    )


class JsonFetch(Protocol):
    """Discovery's SSRF-validated JSON fetcher (GET, or POST when ``body`` set)."""

    async def __call__(
        self,
        url: str,
        preset_name: str | None,
        *,
        headers: dict[str, str] | None = ...,
        trust_url: bool = ...,
        body: dict[str, JsonValue] | None = ...,
    ) -> dict[str, JsonValue] | None:
        """Fetch JSON from *url*, returning ``None`` on any failure."""
        ...


async def enrich_models_via_show(
    show_url: str,
    models: tuple[ProviderModelConfig, ...],
    *,
    headers: dict[str, str] | None,
    trust_url: bool,
    fetch_json: JsonFetch,
) -> tuple[ProviderModelConfig, ...]:
    """Enrich each model with capabilities from an Ollama ``/api/show`` probe.

    The ``/api/tags`` and OpenAI ``/models`` listings carry no capabilities, so
    a bare model fails the matcher's capability hard-filter. ``/api/show``
    returns the real tool/vision/reasoning flags + context window. A per-model
    miss degrades to the un-enriched model, never drops it. ``fetch_json`` is
    injected (discovery owns the SSRF-validated HTTP layer) to avoid an import
    cycle.

    Args:
        show_url: The provider's ``/api/show`` endpoint.
        models: Models parsed from the listing (id only).
        headers: Optional auth headers.
        trust_url: Skip SSRF validation when True (local providers).
        fetch_json: The discovery JSON fetcher (GET, or POST when ``body`` set).

    Returns:
        The models with capability metadata applied where ``/api/show`` answered.
    """

    async def _enrich(model: ProviderModelConfig) -> ProviderModelConfig:
        show = await fetch_json(
            show_url,
            "ollama",
            headers=headers,
            trust_url=trust_url,
            body={"model": model.id},
        )
        return model if show is None else parse_ollama_show(model.id, show)

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(_enrich(model)) for model in models]
    return tuple(task.result() for task in tasks)


def _ollama_context_length(show: Mapping[str, JsonValue]) -> int | None:
    """Extract the context window from an Ollama ``/api/show`` ``model_info``.

    The key is architecture-prefixed (e.g. ``gemma4.context_length``), so
    match on the suffix rather than a fixed key.

    Args:
        show: Parsed ``/api/show`` JSON.

    Returns:
        Context length in tokens, or ``None`` when absent / invalid.
    """
    info = show.get("model_info")
    if not isinstance(info, dict):
        return None
    for key, value in info.items():
        if key.endswith(".context_length") and isinstance(value, int) and value > 0:
            return value
    return None


def _ollama_parameter_count(show: Mapping[str, JsonValue]) -> int | None:
    """Extract total parameter count from an Ollama ``/api/show`` response.

    Reads ``model_info["general.parameter_count"]`` (e.g. ``480000000000``).

    Args:
        show: Parsed ``/api/show`` JSON.

    Returns:
        Parameter count, or ``None`` when absent / invalid.
    """
    info = show.get("model_info")
    if not isinstance(info, dict):
        return None
    value = info.get("general.parameter_count")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _parse_standard_models(
    data: dict[str, JsonValue],
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
