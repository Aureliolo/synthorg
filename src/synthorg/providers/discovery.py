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

import functools
import json
from collections.abc import Awaitable, Callable
from typing import Final

import httpx
from pydantic import JsonValue

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import strip_trailing_slash
from synthorg.core.resilience import (
    GeneralRetryHandler,
    coerce_finite_nonneg_seconds,
    parse_retry_after_seconds,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_DISCOVERY_FAILED,
    PROVIDER_DISCOVERY_RETRY,
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
from synthorg.providers.errors import (
    AuthenticationError,
    InvalidRequestError,
    ProviderConnectionError,
    ProviderError,
    ProviderInternalError,
    ProviderTimeoutError,
    RateLimitError,
)
from synthorg.providers.probing import (
    _parse_ollama_models,
    _parse_standard_models,
)
from synthorg.providers.url_utils import redact_url as _redact_url

logger = get_logger(__name__)

_HTTP_UNAUTHORIZED: Final[int] = 401
_HTTP_FORBIDDEN: Final[int] = 403
_HTTP_TOO_MANY_REQUESTS: Final[int] = 429
_HTTP_SERVER_ERROR_FLOOR: Final[int] = 500

_DISCOVERY_TIMEOUT_SECONDS: Final[float] = 10.0

# Bounded retry for an authoritative (strict) discovery round-trip: a
# live-discovery gateway save or a manual re-sync should absorb a transient
# 429 / 5xx / timeout before surfacing a failure, but must not retry a
# terminal error (bad key, 4xx) or loop indefinitely.
_DISCOVERY_RETRY_MAX_ATTEMPTS: Final[int] = 3
_DISCOVERY_RETRY_BASE_SECONDS: Final[float] = 0.5
_DISCOVERY_RETRY_CAP_SECONDS: Final[float] = 8.0


def _discovery_http_error(
    status_code: int,
    safe_url: str,
    *,
    retry_after: float | None = None,
) -> ProviderError:
    """Map a discovery HTTP status onto a typed provider error.

    Args:
        status_code: The HTTP status returned by the provider listing.
        safe_url: Redacted URL for the error context (never the raw URL).
        retry_after: Server-supplied cool-down seconds for a 429, when the
            response carried a parseable ``Retry-After`` header.

    Returns:
        The provider error a strict discovery should raise for *status_code*.
    """
    if status_code in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
        return AuthenticationError(
            f"Provider rejected the discovery credentials ({status_code})",
            context={"url": safe_url},
        )
    if status_code == _HTTP_TOO_MANY_REQUESTS:
        return RateLimitError(
            "Provider rate-limited model discovery (429)",
            retry_after=retry_after,
            context={"url": safe_url},
        )
    if status_code >= _HTTP_SERVER_ERROR_FLOOR:
        return ProviderInternalError(
            f"Provider returned {status_code} during discovery",
            context={"url": safe_url},
        )
    return InvalidRequestError(
        f"Provider returned {status_code} during discovery",
        context={"url": safe_url},
    )


def _discovery_transport_error(reason: str, safe_url: str) -> ProviderError:
    """Build the typed error a strict discovery raises for a transport failure.

    Constructing the message here (rather than at the ``raise`` site) keeps the
    long human-readable strings out of the caller, mirroring
    :func:`_discovery_http_error`.

    Args:
        reason: Transport failure kind (``ssrf`` / ``connection`` / ``timeout``
            / ``non_json`` / ``malformed`` / anything else -> unexpected).
        safe_url: Redacted URL for the error context.

    Returns:
        The typed provider error for *reason*.
    """
    match reason:
        case "ssrf":
            return InvalidRequestError(
                "Discovery URL failed SSRF validation",
                context={"url": safe_url},
            )
        case "connection":
            return ProviderConnectionError(
                "Could not connect to the provider during discovery",
                context={"url": safe_url},
            )
        case "timeout":
            return ProviderTimeoutError(
                "Timed out contacting the provider during discovery",
                context={"url": safe_url},
            )
        case "non_json":
            return ProviderInternalError(
                "Provider returned a non-JSON discovery response",
                context={"url": safe_url},
            )
        case "malformed":
            return ProviderInternalError(
                "Provider returned an unexpected discovery response structure",
                context={"url": safe_url},
            )
        case _:
            return ProviderInternalError(
                "Model discovery failed unexpectedly",
                context={"url": safe_url},
            )


def _malformed_or_empty(url: str, *, strict: bool) -> tuple[ProviderModelConfig, ...]:
    """Resolve an unusable 200 discovery body: raise when strict, else empty.

    Shared by the discovery paths that reach a 200 response whose body cannot
    yield a catalogue (a non-dict body, or a parse that produced nothing): a
    strict discovery surfaces it as a typed :class:`ProviderError`, a non-strict
    one degrades to an empty catalogue.

    Args:
        url: The fetched URL (redacted for the error context).
        strict: When True, raise instead of degrading to empty.

    Returns:
        Empty tuple when not ``strict``.

    Raises:
        ProviderError: When ``strict`` is True.
    """
    if strict:
        error = _discovery_transport_error("malformed", _redact_url(url))
        raise error
    return ()


def _http_retry_after_seconds(response: httpx.Response) -> float | None:
    """Extract a parseable ``Retry-After`` cool-down from a 429 response.

    Args:
        response: The httpx response carrying the (case-insensitive) headers.

    Returns:
        The non-negative finite cool-down seconds, or ``None`` when the header
        is absent or unparseable.
    """
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    return coerce_finite_nonneg_seconds(parse_retry_after_seconds(raw))


def _is_retryable_discovery_error(exc: Exception) -> bool:
    """Whether a strict-discovery failure warrants a retry (transient only).

    Returns:
        True for a retryable provider error (429 / 5xx / timeout / connection).
    """
    return isinstance(exc, ProviderError) and exc.is_retryable


def _discovery_retry_after(exc: Exception) -> float | None:
    """Honour a provider-supplied ``Retry-After`` on a rate-limit retry.

    Returns:
        The error's ``retry_after`` cool-down seconds, or ``None`` when absent.
    """
    return getattr(exc, "retry_after", None)


async def discover_models_strict(
    base_url: str,
    preset_name: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
    clock: Clock | None = None,
) -> tuple[ProviderModelConfig, ...]:
    """Run an authoritative discovery with bounded retry, raising on failure.

    The entry point for callers for whom discovery IS the source of truth (a
    seedless live-discovery gateway save, or a manual re-sync): transient
    provider errors (429 / 5xx / timeout / connection) are retried with
    exponential backoff honouring a server ``Retry-After``, and a terminal
    error (bad key, 4xx) or an exhausted retry budget propagates so the caller
    surfaces the specific reason rather than masking it as "no models".

    Args:
        base_url: Provider base URL.
        preset_name: Preset identifier hint for endpoint selection.
        headers: Optional auth headers to include in the request.
        trust_url: When True, skip SSRF validation (trusted-origin URL).
        clock: Clock seam for the retry backoff (tests inject a fake).

    Returns:
        Tuple of discovered model configs (may be empty when the provider
        legitimately lists no models).

    Raises:
        ProviderError: On a terminal failure or after the retry budget is spent.
    """
    handler = GeneralRetryHandler(
        retryable=_is_retryable_discovery_error,
        max_attempts=_DISCOVERY_RETRY_MAX_ATTEMPTS,
        base=_DISCOVERY_RETRY_BASE_SECONDS,
        cap=_DISCOVERY_RETRY_CAP_SECONDS,
        event=PROVIDER_DISCOVERY_RETRY,
        clock=clock,
        delay_override=_discovery_retry_after,
    )
    op = functools.partial(
        discover_models,
        base_url,
        preset_name,
        headers=headers,
        trust_url=trust_url,
        strict=True,
    )
    return await handler.execute(op, provider=preset_name)


async def discover_models(
    base_url: str,
    preset_name: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
    strict: bool = False,
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
        strict: When True, a failed listing round-trip raises a typed
            :class:`ProviderError` instead of returning an empty tuple, so
            an authoritative discovery (a live-discovery gateway save)
            surfaces the real reason rather than masking it as "no models".

    Returns:
        Tuple of discovered model configs, or empty tuple on failure when
        not ``strict``.

    Raises:
        ProviderError: On a failed listing round-trip when ``strict`` is True.
    """
    # Local ``ollama`` speaks the native listing protocol
    # (``GET /api/tags``). ``ollama-cloud`` is reached through Ollama's
    # OpenAI-compatible endpoint (``https://ollama.com/v1``), so it lists
    # via the standard ``GET {base}/models`` path below, NOT ``/api/tags``.
    if preset_name == "ollama":
        return await _discover_ollama(
            base_url,
            headers=headers,
            trust_url=trust_url,
            strict=strict,
        )
    return await _discover_standard_api(
        base_url,
        preset_name,
        headers=headers,
        trust_url=trust_url,
        strict=strict,
    )


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
    strict: bool = False,
) -> tuple[ProviderModelConfig, ...]:
    """Discover models from Ollama's ``/api/tags`` endpoint.

    Args:
        base_url: Ollama server URL.
        headers: Optional auth headers.
        trust_url: Skip SSRF validation when True.
        strict: Raise a typed :class:`ProviderError` on a failed round-trip.

    Returns:
        Discovered models, or empty tuple on failure when not ``strict``.

    Raises:
        ProviderError: On a failed round-trip when ``strict`` is True.
    """
    url = _discovery_url(base_url, "/api/tags")
    data = await _fetch_json(
        url, "ollama", headers=headers, trust_url=trust_url, strict=strict
    )
    if data is None:
        # A strict fetch already raised on transport failures; a non-raising
        # ``None`` here means a 200 with a non-dict body, which strict must
        # surface rather than mask as "no models".
        return _malformed_or_empty(url, strict=strict)
    base_models = _parse_and_log(
        "ollama", url, data, _parse_ollama_models, strict=strict
    )
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
    strict: bool = False,
) -> tuple[ProviderModelConfig, ...]:
    """Discover models from a standard ``/models`` endpoint.

    Used for LM Studio, vLLM, and unknown providers that expose
    an ``/models`` listing endpoint.

    Args:
        base_url: Provider base URL.
        preset_name: Preset name for logging context.
        headers: Optional auth headers.
        trust_url: Skip SSRF validation when True.
        strict: Raise a typed :class:`ProviderError` on a failed round-trip.

    Returns:
        Discovered models, or empty tuple on failure when not ``strict``.

    Raises:
        ProviderError: On a failed round-trip when ``strict`` is True.
    """
    url = _discovery_url(base_url, "/models")
    data = await _fetch_json(
        url,
        preset_name,
        headers=headers,
        trust_url=trust_url,
        strict=strict,
    )
    if data is None:
        # A strict fetch already raised on transport failures; a non-raising
        # ``None`` here means a 200 with a non-dict body, which strict must
        # surface rather than mask as "no models".
        return _malformed_or_empty(url, strict=strict)
    models = _parse_and_log(
        preset_name, url, data, _parse_standard_models, strict=strict
    )
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
    *,
    strict: bool = False,
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
        strict: When True, an unexpected response structure (``parse_fn``
            returns ``None``) raises a typed :class:`ProviderError` instead of
            degrading to an empty tuple, so an authoritative discovery surfaces
            a malformed catalogue rather than masking it as "no models".

    Returns:
        Tuple of discovered model configs, or empty tuple.

    Raises:
        ProviderError: On an unexpected response structure when ``strict``.
    """
    models = parse_fn(data)
    if models is None:
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="unexpected_response_structure",
            url=_redact_url(url),
        )
        return _malformed_or_empty(url, strict=strict)

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
    strict: bool = False,
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
        strict: When True, a fetch failure raises a typed
            :class:`ProviderError` instead of returning ``None``.

    Returns:
        Parsed JSON dict, or ``None`` on failure when not ``strict``.

    Raises:
        ProviderError: On any fetch failure when ``strict`` is True.
    """
    safe_url = _redact_url(url)
    # A trusted discovery URL is allowlisted -- a preset ``candidate_url`` or an
    # admin-entered provider base URL auto-allowlisted on registration -- so
    # fetching it is a legitimate call, not a security event, and logs at DEBUG.
    # The genuine dev-mode private-IP master-switch bypass is warned where that
    # switch is honoured, so it is not double-warned here.
    logger.debug(
        PROVIDER_DISCOVERY_SSRF_BYPASSED,
        preset=preset_name,
        url=safe_url,
    )
    return await _safe_fetch(
        _do_fetch_json(url, headers, preset_name=preset_name, body=body),
        preset_name,
        safe_url,
        strict=strict,
    )


async def _fetch_json(
    url: str,
    preset_name: str | None,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
    body: dict[str, JsonValue] | None = None,
    strict: bool = False,
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
        strict: When True, a fetch failure (including SSRF rejection) raises a
            typed :class:`ProviderError` instead of returning ``None``.

    Returns:
        Parsed JSON dict, or ``None`` on failure when not ``strict``.

    Raises:
        ProviderError: On any fetch failure when ``strict`` is True.
    """
    if trust_url:
        return await _fetch_json_trusted(
            url,
            preset_name,
            headers=headers,
            body=body,
            strict=strict,
        )

    safe_url = _redact_url(url)
    pinned_url, original_host = await _validate_and_pin(
        url,
        preset_name,
        safe_url,
    )
    if pinned_url is None:
        if strict:
            error = _discovery_transport_error("ssrf", safe_url)
            raise error
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
        strict=strict,
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
    *,
    strict: bool = False,
) -> dict[str, JsonValue] | None:
    """Await a fetch coroutine with unified exception handling.

    Wraps the common try/except pattern shared by both trusted and
    SSRF-validated fetch paths.

    Args:
        coro: Awaitable returning a JSON dict or None.
        preset_name: Preset name for logging context.
        safe_url: Redacted URL for log messages.
        strict: When True, a fetch failure raises a typed
            :class:`ProviderError` instead of returning ``None``. Callers
            for whom discovery is authoritative (a provider save on a
            live-discovery gateway) pass this so a failed round-trip
            surfaces the real reason rather than being masked as "no
            models found".

    Returns:
        Parsed JSON dict, or ``None`` on failure when not ``strict``.

    Raises:
        ProviderError: On any fetch failure when ``strict`` is True.
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
        if strict:
            error = _discovery_http_error(
                exc.response.status_code,
                safe_url,
                retry_after=_http_retry_after_seconds(exc.response),
            )
            raise error from exc
    except httpx.ConnectError as exc:
        _log_fetch_failure(preset_name, "connection_refused", safe_url)
        if strict:
            error = _discovery_transport_error("connection", safe_url)
            raise error from exc
    except httpx.TimeoutException as exc:
        _log_fetch_failure(preset_name, "timeout", safe_url)
        if strict:
            error = _discovery_transport_error("timeout", safe_url)
            raise error from exc
    except json.JSONDecodeError as exc:
        _log_fetch_failure(preset_name, "invalid_json_response", safe_url)
        if strict:
            error = _discovery_transport_error("non_json", safe_url)
            raise error from exc
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="unexpected_error",
            url=safe_url,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        if strict:
            error = _discovery_transport_error("unexpected", safe_url)
            raise error from exc
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
