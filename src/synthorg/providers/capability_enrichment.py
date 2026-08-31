# module-kind: integration
"""Layered capability enrichment for discovered provider models.

A model from a ``/v1``-style ``/models`` listing (or an Ollama
``/api/tags``) carries no capability flags, so the matcher cannot tell
whether an agent can call tools on it. This resolves the flags once, at
discovery, from the best available source -- so the matcher, runtime
assignment, and the UI all read one populated ``ModelMetadata`` rather
than re-deriving it:

1. **Native introspection** -- Ollama ``/api/show`` (works for local
   servers and ollama.com cloud alike), detected by a ``/api/version``
   probe so it fires regardless of the configured preset name.
2. **LiteLLM's maintained model database** -- ``get_model_info`` for any
   mainstream cloud model LiteLLM tracks.
3. **Unknown** -- left as-is; the matcher treats unknown optimistically
   (usable, ranked below proven-capable models).
"""

import asyncio
import ipaddress
from typing import NamedTuple
from urllib.parse import urlsplit

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import (
    normalize_ascii_lowercase,
    strip_trailing_slash,
)
from synthorg.core.url_locality import LOCALHOST_ALIASES
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_CAPABILITY_ENRICHMENT_FAILED,
    PROVIDER_NOT_OLLAMA_NATIVE,
    PROVIDER_OLLAMA_USAGE_TIER_APPLY_FAILED,
)
from synthorg.providers.ollama_usage_tier import (
    OLLAMA_LIBRARY_HOST,
    resolve_usage_tiers,
)
from synthorg.providers.probing import JsonFetch, enrich_models_via_show

logger = get_logger(__name__)

_OLLAMA_VERSION_PATH = "/api/version"
_OLLAMA_SHOW_PATH = "/api/show"
_OLLAMA_CLOUD_HOST = "ollama.com"
_OLLAMA_PRESET = "ollama"


def _host_of(base_url: str) -> str:
    """Return the lowercased host of ``base_url`` (empty when unparseable)."""
    candidate = base_url if "://" in base_url else f"//{base_url}"
    return normalize_ascii_lowercase(urlsplit(candidate).hostname or "")


def _is_ollama_cloud_host(base_url: str) -> bool:
    """Whether ``base_url``'s host is ollama.com (the cloud library host).

    Parses the host rather than substring-matching ``"ollama.com"`` so a
    look-alike authority (``ollama.com.evil.test``) or a path segment
    (``evil.test/ollama.com``) cannot be mistaken for the cloud host.

    Returns:
        True when the URL authority is ``ollama.com`` or a subdomain of it.
    """
    host = _host_of(base_url)
    return host == _OLLAMA_CLOUD_HOST or host.endswith(f".{_OLLAMA_CLOUD_HOST}")


def _is_local_or_private_host(base_url: str) -> bool:
    """Whether ``base_url``'s host is a loopback / private-network address.

    Returns:
        True for a localhost alias or a loopback / private IP literal (a
        self-hosted server); False for a public host or a hostname that does
        not resolve to a literal here.
    """
    host = _host_of(base_url)
    if host in LOCALHOST_ALIASES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _should_probe_ollama_native(native_base: str, preset_name: str | None) -> bool:
    """Whether the endpoint could plausibly be a native Ollama server.

    Native ``/api/show`` capability introspection only exists on an Ollama
    server, so the ``/api/version`` probe should run only where the endpoint
    could be one: the local ``ollama`` preset, the ``ollama.com`` cloud host,
    or an unknown-preset provider on a loopback / private host (a self-hosted
    Ollama). A public chat-completion gateway (e.g. an aggregator on a different
    wire protocol) is never an Ollama server, so probing it would only yield a
    spurious 404 discovery failure.

    Returns:
        True when the endpoint is a plausible Ollama server to probe.
    """
    if preset_name == _OLLAMA_PRESET:
        return True
    if _is_ollama_cloud_host(native_base):
        return True
    if preset_name is None:
        return _is_local_or_private_host(native_base)
    return False


class FetchContext(NamedTuple):
    """Auth + transport context threaded through discovery fetches."""

    headers: dict[str, str] | None
    trust_url: bool
    fetch_json: JsonFetch


def _join(base_url: str, suffix: str) -> str:
    """Join a base URL and a path suffix, collapsing a trailing slash.

    Returns:
        The concatenated URL.
    """
    return f"{strip_trailing_slash(base_url)}{suffix}"


def _strip_v1_suffix(base_url: str) -> str:
    """Strip a trailing ``/v1`` so a native Ollama path can be derived.

    ``ollama-cloud`` lists via the ``{base}/v1/models`` endpoint but exposes
    capabilities through the native ``{base}/api/show``.

    Args:
        base_url: The provider base URL (possibly ending in ``/v1``).

    Returns:
        The base URL with a trailing ``/v1`` removed.
    """
    return strip_trailing_slash(base_url).removesuffix("/v1")


async def _is_ollama_native(base_url: str, fetch: FetchContext) -> bool:
    """Return True when the endpoint answers Ollama's ``/api/version``.

    A capability-bearing ``/api/show`` is only available on native Ollama
    servers; probing ``/api/version`` detects them by behaviour rather than
    by a preset name (which is ``None`` for a discovered cloud provider).

    Returns:
        True when ``/api/version`` returns a version payload.
    """
    data = await fetch.fetch_json(
        _join(base_url, _OLLAMA_VERSION_PATH),
        None,
        headers=fetch.headers,
        trust_url=fetch.trust_url,
    )
    return bool(data and "version" in data)


def _enrich_unknown_via_litellm(
    models: tuple[ProviderModelConfig, ...],
    litellm_provider: str | None,
) -> tuple[ProviderModelConfig, ...]:
    """Fill still-unknown models from LiteLLM's static model database.

    A model already enriched by native introspection is left untouched. A
    model LiteLLM does not know stays unknown -- an empty info dict would
    otherwise stamp ``source="litellm"`` with all-False flags and be read as
    *known-incapable* rather than unknown.

    Returns:
        The models with LiteLLM-sourced metadata applied where available.
    """
    from synthorg.providers.drivers.litellm_model_info import (  # noqa: PLC0415
        extract_model_metadata,
        extract_model_pricing,
        get_litellm_model_info,
    )
    from synthorg.providers.family_parser import get_family_parser  # noqa: PLC0415

    parser = get_family_parser()
    enriched: list[ProviderModelConfig] = []
    for model in models:
        if model.metadata.metadata_source != "unknown":
            enriched.append(model)
            continue
        info = get_litellm_model_info(model.id)
        if not info:
            enriched.append(model)
            continue
        metadata = extract_model_metadata(
            info,
            litellm_provider=litellm_provider,
            model_id=model.id,
            parser=parser,
            base=model.metadata,
        )
        update: dict[str, object] = {"metadata": metadata}
        # Back-fill pricing from LiteLLM only when the operator has not already
        # priced the model: an explicit operator cost is authoritative and must
        # never be overwritten by the static database.
        if model.cost_per_1k_input == 0.0 and model.cost_per_1k_output == 0.0:
            input_cost, output_cost = extract_model_pricing(info)
            if input_cost > 0.0 or output_cost > 0.0:
                update["cost_per_1k_input"] = input_cost
                update["cost_per_1k_output"] = output_cost
        enriched.append(model.model_copy(update=update))
    return tuple(enriched)


async def enrich_discovered_models(
    base_url: str,
    models: tuple[ProviderModelConfig, ...],
    *,
    preset_name: str | None,
    fetch: FetchContext,
) -> tuple[ProviderModelConfig, ...]:
    """Resolve capability metadata for freshly discovered models.

    Applies native Ollama introspection (when the endpoint speaks it) then
    LiteLLM's database for the remainder. Best-effort throughout: a probe
    miss leaves a model unknown rather than dropping it.

    Args:
        base_url: The provider base URL the models were listed from.
        models: Models parsed from the listing (id only).
        preset_name: Provider preset, used as the LiteLLM provider hint.
        fetch: Auth + transport context for the capability probes.

    Returns:
        The models with capability metadata resolved where possible.
    """
    if not models:
        return models
    native_base = _strip_v1_suffix(base_url)
    if _should_probe_ollama_native(native_base, preset_name):
        is_native = await _is_ollama_native(native_base, fetch)
    else:
        # A public non-Ollama gateway (e.g. a cloud aggregator) is not a native
        # Ollama server; skip the /api/version probe entirely so a discovered
        # provider does not log a spurious discovery failure for a 404.
        logger.debug(
            PROVIDER_NOT_OLLAMA_NATIVE,
            preset=preset_name,
            reason="not_ollama_candidate",
        )
        is_native = False
    if is_native:
        models = await enrich_models_via_show(
            _join(native_base, _OLLAMA_SHOW_PATH),
            models,
            headers=fetch.headers,
            trust_url=fetch.trust_url,
            fetch_json=fetch.fetch_json,
        )
    models = await _enrich_via_litellm_safe(models, preset_name)
    return await _apply_usage_tiers_safe(
        models, native_base=native_base, is_native=is_native
    )


async def _enrich_via_litellm_safe(
    models: tuple[ProviderModelConfig, ...],
    preset_name: str | None,
) -> tuple[ProviderModelConfig, ...]:
    """Run the LiteLLM enrichment off-thread, degrading to the input on failure.

    A LiteLLM import error or unexpected lookup failure must leave the models
    un-enriched rather than abort the whole discovery run.

    Returns:
        The enriched models, or the input unchanged when enrichment failed.
    """
    try:
        return await asyncio.to_thread(_enrich_unknown_via_litellm, models, preset_name)
    except Exception as exc:  # noqa: BLE001 -- best-effort: criticals re-raised, any other failure leaves models unknown
        reraise_critical(exc)
        logger.warning(
            PROVIDER_CAPABILITY_ENRICHMENT_FAILED,
            stage="litellm",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return models


async def _apply_usage_tiers_safe(
    models: tuple[ProviderModelConfig, ...],
    *,
    native_base: str,
    is_native: bool,
) -> tuple[ProviderModelConfig, ...]:
    """Apply usage tiers, degrading to the input on any scrape/resolve failure.

    Returns:
        The tier-stamped models, or the input unchanged when tiering failed.
    """
    try:
        return await _apply_usage_tiers(
            models, native_base=native_base, is_native=is_native
        )
    except Exception as exc:  # noqa: BLE001 -- best-effort: criticals re-raised, any other failure leaves cost_tier unset
        reraise_critical(exc)
        logger.warning(
            PROVIDER_OLLAMA_USAGE_TIER_APPLY_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return models


async def _apply_usage_tiers(
    models: tuple[ProviderModelConfig, ...],
    *,
    native_base: str,
    is_native: bool,
) -> tuple[ProviderModelConfig, ...]:
    """Stamp each model's ``cost_tier`` (real ollama level, else approximated).

    The real per-model usage level is scraped from the model page only for
    ollama.com cloud (a local server has no library page); otherwise the tier
    is approximated from parameter count. See
    :mod:`synthorg.providers.ollama_usage_tier`.

    Returns:
        The models with ``cost_tier`` set where resolvable.
    """
    is_cloud = is_native and _is_ollama_cloud_host(native_base)
    host = OLLAMA_LIBRARY_HOST if is_cloud else None
    tiers = await resolve_usage_tiers(
        {model.id: model.metadata.parameter_count for model in models},
        host=host,
    )
    enriched: list[ProviderModelConfig] = []
    for model in models:
        tier = tiers.get(model.id)
        if tier is None or model.metadata.cost_tier == tier:
            enriched.append(model)
            continue
        new_metadata = model.metadata.model_copy(update={"cost_tier": tier})
        enriched.append(model.model_copy(update={"metadata": new_metadata}))
    return tuple(enriched)
