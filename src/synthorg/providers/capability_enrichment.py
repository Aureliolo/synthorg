# module-kind: integration
"""Layered capability enrichment for discovered provider models.

A model from an OpenAI-compatible ``/models`` listing (or an Ollama
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
from typing import NamedTuple

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.normalization import strip_trailing_slash
from synthorg.providers.ollama_usage_tier import (
    OLLAMA_LIBRARY_HOST,
    resolve_usage_tiers,
)
from synthorg.providers.probing import JsonFetch, enrich_models_via_show

_OLLAMA_VERSION_PATH = "/api/version"
_OLLAMA_SHOW_PATH = "/api/show"


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


def _strip_openai_suffix(base_url: str) -> str:
    """Strip a trailing ``/v1`` so a native Ollama path can be derived.

    ``ollama-cloud`` lists via the OpenAI-compatible ``{base}/v1/models`` but
    exposes capabilities through the native ``{base}/api/show``.

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
        )
        enriched.append(model.model_copy(update={"metadata": metadata}))
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
    native_base = _strip_openai_suffix(base_url)
    is_native = await _is_ollama_native(native_base, fetch)
    if is_native:
        models = await enrich_models_via_show(
            _join(native_base, _OLLAMA_SHOW_PATH),
            models,
            headers=fetch.headers,
            trust_url=fetch.trust_url,
            fetch_json=fetch.fetch_json,
        )
    models = await asyncio.to_thread(_enrich_unknown_via_litellm, models, preset_name)
    return await _apply_usage_tiers(
        models, native_base=native_base, is_native=is_native
    )


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
    host = OLLAMA_LIBRARY_HOST if (is_native and "ollama.com" in native_base) else None
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
