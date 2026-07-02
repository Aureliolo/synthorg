# module-kind: integration
"""Model auto-discovery for LLM providers.

Two capabilities:

1. Auto-discovery when a preset is created with no explicit model list
   (e.g. Ollama, LM Studio, vLLM).
2. On-demand discovery for existing providers via the
   ``POST /{name}/discover-models`` endpoint.

URL probing (candidate URL probing for presets) lives in
:mod:`synthorg.providers.probing`.
"""

import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Final
from urllib.parse import urlparse

import httpx
from pydantic import JsonValue

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import strip_trailing_slash
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_DISCOVERY_FAILED,
    PROVIDER_DISCOVERY_SSRF_BYPASSED,
    PROVIDER_MODELS_DISCOVERED,
)
from synthorg.providers._discovery_ssrf import (
    build_pinned_url,
    validate_discovery_url,
)
from synthorg.providers.capability_enrichment import (
    FetchContext,
    enrich_discovered_models,
)
from synthorg.providers.probing import (
    _parse_ollama_models,
    _parse_standard_models,
)
from synthorg.providers.url_utils import redact_url as _redact_url

logger = get_logger(__name__)

# Per-discovery-pass dedup for the SSRF-bypass audit warning. One
# Ollama pass probes ``/api/show`` once per model, which used to emit
# dozens of identical warnings; the warning now fires once per origin
# per pass (keyed on origin alone -- enrichment probes hit the same
# host under ``preset_name=None``) and later hits demote to debug. A
# fresh set per pass keeps the audit intent: a recurring bypass warns
# again on every pass instead of going dark after the first hit.
_BYPASS_WARNED_ORIGINS: ContextVar[set[str] | None] = ContextVar(
    "discovery_bypass_warned_origins",
    default=None,
)

_DISCOVERY_TIMEOUT_SECONDS: Final[float] = 10.0


async def discover_models(
    base_url: str,
    preset_name: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
) -> tuple[ProviderModelConfig, ...]:
    """Discover available models from a provider endpoint.

    For Ollama presets, queries ``GET {base_url}/api/tags``.
    For standard-API providers (LM Studio, vLLM, or unknown),
    queries ``GET {base_url}/models``.

    Args:
        base_url: Provider base URL (e.g. ``http://localhost:11434``
            for Ollama, ``http://localhost:1234/v1`` for LM Studio).
        preset_name: Preset identifier hint for endpoint selection.
        headers: Optional auth headers to include in the request.
        trust_url: When True, skip SSRF validation. Use only when
            the URL originates from a trusted source (e.g. a preset's
            ``candidate_urls`` or admin-entered during setup).

    Returns:
        Tuple of discovered model configs, or empty tuple on failure.
    """
    # One bypass-warning dedup scope per pass; reset so sequential
    # passes on the same task each warn afresh.
    token = _BYPASS_WARNED_ORIGINS.set(set())
    try:
        # Local ``ollama`` speaks the native listing protocol
        # (``GET /api/tags``). ``ollama-cloud`` is reached through Ollama's
        # OpenAI-compatible endpoint (``https://ollama.com/v1``), so it lists
        # via the standard ``GET {base}/models`` path below, NOT ``/api/tags``.
        if preset_name == "ollama":
            return await _discover_ollama(
                base_url,
                headers=headers,
                trust_url=trust_url,
            )
        return await _discover_standard_api(
            base_url,
            preset_name,
            headers=headers,
            trust_url=trust_url,
        )
    finally:
        _BYPASS_WARNED_ORIGINS.reset(token)


def _discovery_url(base_url: str, suffix: str) -> str:
    """Join a discovery endpoint *suffix* onto a provider base URL.

    Args:
        base_url: Provider base URL (trailing slashes are stripped).
        suffix: Endpoint path beginning with ``/`` (e.g. ``/models``).

    Returns:
        The composed discovery URL.
    """
    return f"{strip_trailing_slash(base_url)}{suffix}"


async def _discover_ollama(
    base_url: str,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
) -> tuple[ProviderModelConfig, ...]:
    """Discover models from Ollama's ``/api/tags`` endpoint.

    Args:
        base_url: Ollama server URL.
        headers: Optional auth headers.
        trust_url: Skip SSRF validation when True.

    Returns:
        Discovered models, or empty tuple on failure.
    """
    url = _discovery_url(base_url, "/api/tags")
    data = await _fetch_json(url, "ollama", headers=headers, trust_url=trust_url)
    if data is None:
        return ()
    base_models = _parse_and_log("ollama", url, data, _parse_ollama_models)
    return await enrich_discovered_models(
        base_url,
        base_models,
        preset_name="ollama",
        fetch=FetchContext(headers, trust_url, _fetch_json),
    )


async def _discover_standard_api(
    base_url: str,
    preset_name: str | None,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
) -> tuple[ProviderModelConfig, ...]:
    """Discover models from a standard ``/models`` endpoint.

    Used for LM Studio, vLLM, and unknown providers that expose
    an ``/models`` listing endpoint.

    Args:
        base_url: Provider base URL.
        preset_name: Preset name for logging context.
        headers: Optional auth headers.
        trust_url: Skip SSRF validation when True.

    Returns:
        Discovered models, or empty tuple on failure.
    """
    url = _discovery_url(base_url, "/models")
    data = await _fetch_json(
        url,
        preset_name,
        headers=headers,
        trust_url=trust_url,
    )
    if data is None:
        return ()
    models = _parse_and_log(preset_name, url, data, _parse_standard_models)
    return await enrich_discovered_models(
        base_url,
        models,
        preset_name=preset_name,
        fetch=FetchContext(headers, trust_url, _fetch_json),
    )


def _parse_and_log(
    preset_name: str | None,
    url: str,
    data: dict[str, JsonValue],
    parse_fn: Callable[[dict[str, JsonValue]], tuple[ProviderModelConfig, ...] | None],
) -> tuple[ProviderModelConfig, ...]:
    """Parse a model-listing response and log skip counts.

    Delegates to the provided ``parse_fn`` (from probing.py) and
    adds skip-counting and structured logging around the result.

    Args:
        preset_name: Preset name for logging context.
        url: URL that was fetched (for logging).
        data: Parsed JSON response body.
        parse_fn: Parser function returning a tuple of
            ProviderModelConfig or None.

    Returns:
        Tuple of discovered model configs, or empty tuple.
    """
    models = parse_fn(data)
    if models is None:
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="unexpected_response_structure",
            url=_redact_url(url),
        )
        return ()

    # Determine skip count from the raw list.
    raw_key = "models" if parse_fn is _parse_ollama_models else "data"
    raw_value = data.get(raw_key, [])
    raw_entries: list[JsonValue] = raw_value if isinstance(raw_value, list) else []
    skipped = len(raw_entries) - len(models)
    _log_skip_counts(preset_name, raw_entries, skipped, len(models))

    # Only log success when at least some models were parsed. If all
    # entries were malformed, _log_skip_counts already logged a warning.
    if models:
        logger.info(
            PROVIDER_MODELS_DISCOVERED,
            preset=preset_name,
            model_count=len(models),
        )
    return models


def _log_skip_counts(
    preset_name: str | None,
    raw_entries: list[JsonValue],
    skipped: int,
    model_count: int,
) -> None:
    """Log diagnostic info about malformed entries.

    Args:
        preset_name: Preset name for logging context.
        raw_entries: Raw list of model entries.
        skipped: Number of entries that were skipped.
        model_count: Number of valid models parsed.
    """
    if skipped and not model_count:
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="all_entries_malformed",
            total_entries=len(raw_entries),
            skipped=skipped,
        )
    elif skipped:
        logger.debug(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="some_entries_malformed",
            skipped=skipped,
        )


async def _fetch_json_trusted(
    url: str,
    preset_name: str | None,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue] | None:
    """Fetch JSON from a trusted URL without SSRF validation.

    Used for URLs that originate from preset ``candidate_urls`` or
    were admin-entered during setup.  Local providers like Ollama
    use localhost/private IPs by design, which SSRF validation would
    block.  No IP pinning or Host-header rewriting is performed
    because the URL is used verbatim.

    Args:
        url: Full URL to fetch.
        preset_name: Preset name for logging context.
        headers: Optional auth headers to include.
        body: JSON request body; when set, the request is a POST.

    Returns:
        Parsed JSON dict, or ``None`` on any failure.
    """
    safe_url = _redact_url(url)
    # The audit trail must show a bypass that keeps happening, not go
    # dark after the first hit: every discovery pass warns once per
    # origin, and repeat URLs within the pass demote to debug. Outside
    # a pass scope (no ContextVar set) every occurrence still warns.
    parsed = urlparse(url)
    origin_key = f"{parsed.scheme}://{parsed.netloc}"
    warned = _BYPASS_WARNED_ORIGINS.get()
    if warned is None or origin_key not in warned:
        if warned is not None:
            warned.add(origin_key)
        logger.warning(
            PROVIDER_DISCOVERY_SSRF_BYPASSED,
            preset=preset_name,
            url=safe_url,
        )
    else:
        logger.debug(
            PROVIDER_DISCOVERY_SSRF_BYPASSED,
            preset=preset_name,
            url=safe_url,
        )
    return await _safe_fetch(
        _do_fetch_json(url, headers, preset_name=preset_name, body=body),
        preset_name,
        safe_url,
    )


async def _fetch_json(
    url: str,
    preset_name: str | None,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
    body: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue] | None:
    """Fetch JSON from a URL with timeout and error handling.

    Validates the URL for SSRF safety before making the request
    unless ``trust_url`` is True (delegates to
    :func:`_fetch_json_trusted` for preset-originated URLs).

    Uses the resolved IP from validation to pin the connection,
    preventing DNS rebinding between validation and the HTTP request.

    Args:
        url: Full URL to fetch.
        preset_name: Preset name for logging context.
        headers: Optional auth headers to include.
        trust_url: When True, skip SSRF validation and IP pinning.
        body: JSON request body; when set, the request is a POST.

    Returns:
        Parsed JSON dict, or ``None`` on any failure.
    """
    if trust_url:
        return await _fetch_json_trusted(
            url,
            preset_name,
            headers=headers,
            body=body,
        )

    safe_url = _redact_url(url)
    pinned_url, original_host = await _validate_and_pin(
        url,
        preset_name,
        safe_url,
    )
    if pinned_url is None:
        return None

    return await _safe_fetch(
        _do_fetch_json(
            pinned_url,
            headers,
            host_header=original_host,
            preset_name=preset_name,
            body=body,
        ),
        preset_name,
        safe_url,
    )


async def _validate_and_pin(
    url: str,
    preset_name: str | None,
    safe_url: str,
) -> tuple[str | None, str]:
    """Validate a URL for SSRF and build a pinned URL.

    Args:
        url: Original URL to validate.
        preset_name: Preset name for logging context.
        safe_url: Redacted URL for log messages.

    Returns:
        Tuple of (pinned_url, original_host).  pinned_url is None
        if validation fails.
    """
    check = await validate_discovery_url(url)
    if check.error is not None:
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="blocked_url",
            url=safe_url,
            detail=check.error,
        )
        return None, ""

    pinned_ip = check.pinned_ip
    if pinned_ip is None:
        # Defensive: should not happen when error is None.
        logger.error(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="ssrf_check_inconsistency",
            url=safe_url,
            detail="SSRF check passed but returned no pinned IP",
        )
        return None, ""

    pinned_url, original_host = build_pinned_url(url, pinned_ip)
    return pinned_url, original_host


async def _safe_fetch(
    coro: Awaitable[dict[str, JsonValue] | None],
    preset_name: str | None,
    safe_url: str,
) -> dict[str, JsonValue] | None:
    """Await a fetch coroutine with unified exception handling.

    Wraps the common try/except pattern shared by both trusted and
    SSRF-validated fetch paths.

    Args:
        coro: Awaitable returning a JSON dict or None.
        preset_name: Preset name for logging context.
        safe_url: Redacted URL for log messages.

    Returns:
        Parsed JSON dict, or ``None`` on any failure.
    """
    try:
        return await coro
    except httpx.HTTPStatusError as exc:
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="http_error",
            url=safe_url,
            status_code=exc.response.status_code,
        )
    except httpx.ConnectError:
        _log_fetch_failure(preset_name, "connection_refused", safe_url)
    except httpx.TimeoutException:
        _log_fetch_failure(preset_name, "timeout", safe_url)
    except json.JSONDecodeError:
        _log_fetch_failure(preset_name, "invalid_json_response", safe_url)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="unexpected_error",
            url=safe_url,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    return None


async def _do_fetch_json(
    url: str,
    headers: dict[str, str] | None,
    *,
    host_header: str = "",
    preset_name: str | None = None,
    body: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue] | None:
    """Execute the HTTP request and parse the JSON response.

    Issues a GET by default, or a JSON POST when ``body`` is supplied
    (e.g. Ollama's ``/api/show`` capability lookup).

    Args:
        url: URL to fetch (may be IP-pinned).
        headers: Optional request headers.
        host_header: Original hostname for the Host header (when
            the URL has been rewritten with a pinned IP).
        preset_name: Preset name for logging context.
        body: JSON request body; when set, the request is a POST.

    Returns:
        Parsed JSON dict, or ``None`` for non-dict responses.
    """
    merged_headers: dict[str, str] = {**(headers or {})}
    if host_header:
        merged_headers["Host"] = host_header
    async with httpx.AsyncClient(
        timeout=_DISCOVERY_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        response = (
            await client.post(url, headers=merged_headers, json=body)
            if body is not None
            else await client.get(url, headers=merged_headers)
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            logger.warning(
                PROVIDER_DISCOVERY_FAILED,
                preset=preset_name,
                reason="unexpected_json_type",
                url=_redact_url(url),
            )
            return None
        return result


def _log_fetch_failure(
    preset_name: str | None,
    reason: str,
    safe_url: str,
) -> None:
    """Log a discovery fetch failure with a standard structure."""
    logger.warning(
        PROVIDER_DISCOVERY_FAILED,
        preset=preset_name,
        reason=reason,
        url=safe_url,
    )
