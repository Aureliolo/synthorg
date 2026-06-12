"""Auto-derived "soft" provider presets sourced from ``litellm.model_cost``.

This module owns the discovery + denylist machinery that surfaces every
chat-capable LiteLLM namespace as a generic ``CloudPreset`` in
SynthOrg's wizard, without requiring a code change per LiteLLM
release.  See :mod:`synthorg.providers.presets` for the static
hand-curated catalog and the merge that combines featured and soft
entries into ``PROVIDER_PRESETS``.

Maintainer note: when bumping the LiteLLM dependency, scan the
upstream changelog for new provider namespaces.  Any new IAM-bound
(AWS sigv4 / GCP ADC / IBM IAM), OAuth-bound, or local-only namespace
MUST be added to :data:`_LITELLM_NAMESPACE_DENYLIST` or
:data:`_LITELLM_NAMESPACE_DENY_PREFIXES` before the upgrade ships --
otherwise the auto-derive layer surfaces it as an API-key paste,
which fails at first call.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

import litellm

from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_LITELLM_CATALOG_INVALID
from synthorg.providers.enums import AuthType
from synthorg.providers.preset_models import CloudPreset, LocalPreset

logger = get_logger(__name__)


_LITELLM_NAMESPACE_DENYLIST: Final[frozenset[str]] = frozenset(
    {
        # Cloud-IAM-bound: need AWS sigv4 / GCP ADC / IBM IAM, not API key paste.
        "bedrock",
        "bedrock_chat",
        "bedrock_converse",
        "sagemaker",
        "sagemaker_chat",
        "vertex_ai",
        "vertex_ai_beta",
        "watsonx",
        "watsonx_text",
        "palm",
        "amazon_nova",  # Bedrock-routed Nova family.
        "oci",  # Oracle Cloud Infrastructure -- IAM-bound.
        "volcengine",  # ByteDance cloud -- complex regional + IAM auth.
        # OAuth-bound: not pasteable as an API key.
        "github_copilot",
        "github_copilot_chat",
        # Local / self-hosted: need base_url, often no auth.
        "ollama",
        "ollama_chat",
        "openai_like",
        "openai-compatible",
        "custom_openai",
        "text-completion-openai",
        "huggingface",
        "lemonade",  # Local self-hosted model server.
        # Niche / deprecated / non-chat / wrong-API-shape.
        "aleph_alpha",
        "anyscale",
        "nlpcloud",
        "nlp_cloud",  # LiteLLM uses the underscored variant.
        "replicate",
        "cloudflare",
        "voyage",
        "petals",
        "codestral",  # Mistral variant; covered by curated mistral preset.
        "cohere",  # bare cohere is the deprecated completions endpoint
        # (cohere_chat covers chat completions and is curated above).
        "azure_ai",  # superseded by curated azure preset.
        "azure_text",  # text-completion variant of Azure OpenAI.
    }
)
"""LiteLLM provider namespaces excluded from auto-derived soft presets.

Reasons a namespace lands here:

* the auth shape is incompatible with API-key paste (cloud IAM such
  as AWS Bedrock or GCP Vertex AI; OAuth-bound such as GitHub
  Copilot);
* the deployment model requires a base URL or self-hosted server
  that does not fit the SaaS preset surface (local Ollama variants,
  generic OpenAI-compatible wrappers, HuggingFace TGI);
* the namespace is deprecated or superseded by a curated featured
  preset (e.g. bare ``cohere`` is the deprecated completions path
  while ``cohere_chat`` is curated above);
* the namespace duplicates a featured preset's
  :func:`litellm_provider`.

The ``mode``-based filter in :func:`_iter_litellm_chat_namespaces`
already drops embedding / audio / image-only providers; this
denylist is the auth-shape and deployment-model layer on top of
that filter.
"""

_LITELLM_NAMESPACE_DENY_PREFIXES: Final[tuple[str, ...]] = (
    "bedrock",  # bedrock_mantle, bedrock_runtime, ...
    "vertex_ai",  # vertex_ai-anthropic_models, vertex_ai-openai_models, ...
    "sagemaker",
    "watsonx",
    "text-completion-",  # text-completion-codestral, text-completion-openai
)
"""Prefixes that mark a LiteLLM namespace as denied.

Used in addition to :data:`_LITELLM_NAMESPACE_DENYLIST` so that
sub-namespaces (for example ``vertex_ai-anthropic_models`` or
``bedrock_mantle``) inherit the parent's auth-shape exclusion without
having to enumerate every variant.
"""


def _is_denied_namespace(namespace: str) -> bool:
    """Return ``True`` when ``namespace`` matches a denylist entry."""
    if namespace in _LITELLM_NAMESPACE_DENYLIST:
        return True
    return any(
        namespace.startswith(prefix) for prefix in _LITELLM_NAMESPACE_DENY_PREFIXES
    )


_DISPLAY_NAME_ACRONYMS: Final[frozenset[str]] = frozenset(
    {
        "AI",
        "AI21",
        "API",
        "AWS",
        "GCP",
        "GPT",
        "IBM",
        "LLM",
        "ML",
        "NIM",
        "NLP",
        "OCI",
        "TTS",
    }
)
"""Words that should keep their fully-uppercased form even after the
title-casing pass in :func:`_humanise_namespace`.

The bare ``str.title()`` output mangles common AI / cloud acronyms
(``"ai21"`` -> ``"Ai21"``, ``"together_ai"`` -> ``"Together Ai"``);
this set restores the canonical casing for the soft-preset display
labels rendered in the wizard's "More providers" surface.  Featured
presets set ``display_name`` explicitly and do not pass through this
helper, so growing this set is a low-risk operation.
"""

_DISPLAY_NAME_LOWERCASE: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "V0": "v0",  # Vercel's product is officially lowercase.
    }
)
"""Title-cased tokens that should be normalised back to a specific
non-title casing.  Keep small; prefer growing
:data:`_DISPLAY_NAME_ACRONYMS` for the common upper-case path.
"""


def _humanise_namespace(namespace: str) -> str:
    """Turn a LiteLLM namespace into a readable display name.

    Title-cases the string with underscores and hyphens converted to
    spaces, then restores known acronyms (``"AI"``, ``"API"``,
    ``"NIM"``, ...) and a small set of lowercase product names
    (e.g. ``"v0"``).  Featured presets set ``display_name`` explicitly
    via the constructor and never reach this helper.

    Examples:
        ``"perplexity"`` -> ``"Perplexity"``
        ``"ai21"`` -> ``"AI21"``
        ``"lambda_ai"`` -> ``"Lambda AI"``
        ``"v0"`` -> ``"v0"``

    Returns:
        A human-readable display name with separators spaced, title-cased,
        known acronyms restored, and product names normalised.
    """
    titled = namespace.replace("_", " ").replace("-", " ").title()
    parts: list[str] = []
    for word in titled.split(" "):
        upper = word.upper()
        if upper in _DISPLAY_NAME_ACRONYMS:
            parts.append(upper)
        elif word in _DISPLAY_NAME_LOWERCASE:
            parts.append(_DISPLAY_NAME_LOWERCASE[word])
        else:
            parts.append(word)
    return " ".join(parts)


def _make_soft_preset(namespace: str) -> CloudPreset:
    """Build a generic API-key-only ``CloudPreset`` for a LiteLLM namespace.

    The auto-generated ``description`` quotes the namespace via
    ``{namespace!r}`` and the wizard renders it through React's plain
    text path (no ``dangerouslySetInnerHTML``); a future LiteLLM
    upgrade introducing an unusual namespace string cannot inject
    HTML or script content into the picker.

    Returns:
        A soft ``CloudPreset`` (``is_featured=False``, API-key-only) for
        the given LiteLLM namespace.
    """
    return CloudPreset(
        name=namespace,
        display_name=_humanise_namespace(namespace),
        description=f"Models served via LiteLLM provider {namespace!r}",
        driver="litellm",
        litellm_provider=namespace,
        auth_type=AuthType.API_KEY,
        supported_auth_types=(AuthType.API_KEY,),
        default_models=(),
        is_featured=False,
    )


def _iter_litellm_chat_namespaces() -> tuple[str, ...]:
    """Return every chat-capable LiteLLM namespace, sorted, deduped.

    A namespace is included when at least one model in
    ``litellm.model_cost`` declares it via ``litellm_provider`` and has
    ``mode in {"chat", "completion"}``.  Embedding / audio / image
    providers are filtered out.

    The walk is defensive: a non-``Mapping`` ``litellm.model_cost``
    attribute (a future LiteLLM upgrade replacing the dict with a list
    or other shape), non-dict entries, missing or empty
    ``litellm_provider`` strings, and missing ``mode`` fields are all
    silently skipped.  A future LiteLLM upgrade with malformed entries
    cannot crash module load.

    Returns:
        A sorted, deduplicated tuple of LiteLLM provider namespace
        strings with at least one chat/completion-mode model.
    """
    seen: set[str] = set()
    cost_table = getattr(litellm, "model_cost", {}) or {}
    if not isinstance(cost_table, Mapping):
        logger.warning(
            PROVIDER_LITELLM_CATALOG_INVALID,
            catalog_type=type(cost_table).__name__,
            error=(
                "litellm.model_cost is not a Mapping; auto-derived "
                "soft presets disabled. The wizard's More-providers "
                "section will be empty until LiteLLM's catalog shape "
                "is restored or this module is updated."
            ),
        )
        return ()
    for info in cost_table.values():
        if not isinstance(info, dict):
            continue
        if info.get("mode") not in {"chat", "completion"}:
            continue
        provider = info.get("litellm_provider")
        if not isinstance(provider, str) or not provider:
            continue
        seen.add(provider)
    return tuple(sorted(seen))


def build_soft_presets(
    featured: tuple[CloudPreset | LocalPreset, ...],
) -> tuple[CloudPreset, ...]:
    """Auto-derive soft presets for every non-excluded LiteLLM namespace.

    Skips namespaces already covered by a featured preset's
    :attr:`litellm_provider`, any namespace listed in
    :data:`_LITELLM_NAMESPACE_DENYLIST`, and any namespace whose
    prefix matches an entry in
    :data:`_LITELLM_NAMESPACE_DENY_PREFIXES`.  Returned in
    alphabetical order by namespace.

    Returns:
        A tuple of ``CloudPreset`` instances, one per non-denied,
        non-featured LiteLLM chat namespace, sorted alphabetically.
    """
    covered: frozenset[str] = frozenset(p.litellm_provider for p in featured)
    softs: list[CloudPreset] = []
    for namespace in _iter_litellm_chat_namespaces():
        if namespace in covered or _is_denied_namespace(namespace):
            continue
        softs.append(_make_soft_preset(namespace))
    return tuple(softs)
