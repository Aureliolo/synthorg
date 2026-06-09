"""Pre-defined provider presets for common LLM backends.

Presets provide sensible defaults for popular providers so users
can add them with minimal configuration (e.g. just an API key).

Two preset kinds, expressed as a discriminated union:

* :class:`CloudPreset` -- hosted LLM APIs (Anthropic, OpenAI, Azure, ...).
  Carries cloud-specific metadata (default model list, supported auth
  types) and never has ``candidate_urls``.
* :class:`LocalPreset` -- self-hosted LLM servers (Ollama, LM Studio,
  vLLM).  Carries auto-detect candidate URLs and local model-management
  capability flags.

Two preset tiers, distinguished by :attr:`_BasePreset.is_featured`:

* **Featured** -- hand-curated entries with brand logo, vetted
  description, and (where useful) ``default_models`` fallback.  Listed
  in :data:`_FEATURED_PRESETS`.
* **Soft** -- auto-derived from ``litellm.model_cost`` by
  :mod:`synthorg.providers.preset_softlist` for every chat namespace
  not already covered by a featured preset and not denied by the
  soft-list module's denylist + deny-prefix table.  Soft presets
  render with the wizard's generic fallback icon and a generic
  description; they exist so SynthOrg surfaces every chat-capable
  LiteLLM provider out of the box.

Consumers iterating across all presets should use the helpers
:func:`default_models_for`, :func:`candidate_urls_for`,
:func:`list_local_presets`, :func:`list_featured_presets`, and
:func:`list_soft_presets` instead of conditional ``isinstance``
or attribute checks.
"""

import re
from types import MappingProxyType
from typing import Final

from synthorg.config.schema import ProviderModelConfig
from synthorg.providers._preset_audit import audit_presets
from synthorg.providers.enums import AuthType
from synthorg.providers.preset_models import (
    CloudPreset,
    LocalPreset,
    ProviderPreset,
)
from synthorg.providers.preset_softlist import build_soft_presets

__all__ = [
    "MODEL_VERSION_FILTERS",
    "PROVIDER_PRESETS",
    "CloudPreset",
    "LocalPreset",
    "ProviderPreset",
    "candidate_urls_for",
    "default_models_for",
    "get_preset",
    "list_featured_presets",
    "list_local_presets",
    "list_presets",
    "list_probable_presets",
    "list_soft_presets",
]


# ── Cloud providers ────────────────────────────────────────────

_ANTHROPIC = CloudPreset(
    name="anthropic",
    display_name="Anthropic",
    description="Claude models (Opus, Sonnet, Haiku)",
    driver="litellm",
    litellm_provider="anthropic",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY, AuthType.SUBSCRIPTION),
    default_models=(
        ProviderModelConfig(
            id="claude-sonnet-4-6-20250514",
            alias="sonnet",
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            max_context=200_000,
        ),
        ProviderModelConfig(
            id="claude-haiku-4-5-20251001",
            alias="haiku",
            cost_per_1k_input=0.0008,
            cost_per_1k_output=0.004,
            max_context=200_000,
        ),
    ),
)

_OPENAI = CloudPreset(
    name="openai",
    display_name="OpenAI",
    description="GPT and o-series models",
    driver="litellm",
    litellm_provider="openai",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(
        ProviderModelConfig(
            id="gpt-4.1",
            alias="gpt4",
            cost_per_1k_input=0.002,
            cost_per_1k_output=0.008,
            max_context=1_047_576,
        ),
        ProviderModelConfig(
            id="gpt-4.1-mini",
            alias="gpt4-mini",
            cost_per_1k_input=0.0004,
            cost_per_1k_output=0.0016,
            max_context=1_047_576,
        ),
        ProviderModelConfig(
            id="o3",
            alias="o3",
            cost_per_1k_input=0.002,
            cost_per_1k_output=0.008,
            max_context=200_000,
        ),
    ),
)

_GEMINI = CloudPreset(
    name="gemini",
    display_name="Google AI Studio",
    description="Gemini models via Google AI",
    driver="litellm",
    litellm_provider="gemini",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(
        ProviderModelConfig(
            id="gemini-2.5-pro",
            alias="gemini-pro",
            cost_per_1k_input=0.00125,
            cost_per_1k_output=0.01,
            max_context=1_048_576,
        ),
        ProviderModelConfig(
            id="gemini-2.5-flash",
            alias="gemini-flash",
            cost_per_1k_input=0.00015,
            cost_per_1k_output=0.0006,
            max_context=1_048_576,
        ),
    ),
)

_MISTRAL = CloudPreset(
    name="mistral",
    display_name="Mistral AI",
    description="Mistral and Codestral models",
    driver="litellm",
    litellm_provider="mistral",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)

_MOONSHOT = CloudPreset(
    name="moonshot",
    display_name="Moonshot AI (Kimi)",
    description="Kimi long-context models from Moonshot AI",
    driver="litellm",
    litellm_provider="moonshot",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)

_NVIDIA_NIM = CloudPreset(
    name="nvidia_nim",
    display_name="NVIDIA NIM",
    description="NVIDIA-hosted inference for Llama, Qwen, and others",
    driver="litellm",
    litellm_provider="nvidia_nim",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)

_GROQ = CloudPreset(
    name="groq",
    display_name="Groq",
    description="Ultra-fast inference (LPU)",
    driver="litellm",
    litellm_provider="groq",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)

_DEEPSEEK = CloudPreset(
    name="deepseek",
    display_name="DeepSeek",
    description="DeepSeek reasoning and chat models",
    driver="litellm",
    litellm_provider="deepseek",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)

_FIREWORKS = CloudPreset(
    name="fireworks_ai",
    display_name="Fireworks AI",
    description="Fast open-model inference (Llama, DeepSeek, Mixtral)",
    driver="litellm",
    litellm_provider="fireworks_ai",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)

_AZURE_OPENAI = CloudPreset(
    name="azure",
    display_name="Azure OpenAI",
    description="OpenAI models via Azure",
    driver="litellm",
    litellm_provider="azure",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    # Azure requires a per-deployment base_url
    default_base_url=None,
    requires_base_url=True,
    default_models=(),
)

_CEREBRAS = CloudPreset(
    name="cerebras",
    display_name="Cerebras",
    description="Wafer-scale inference (Llama, Qwen, GPT-OSS)",
    driver="litellm",
    litellm_provider="cerebras",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)

_COHERE = CloudPreset(
    name="cohere",
    display_name="Cohere",
    description="Command and Command-R models for chat and RAG",
    driver="litellm",
    # cohere/ is the legacy completions endpoint; chat-completions
    # routes via cohere_chat/ in LiteLLM.
    litellm_provider="cohere_chat",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)

_OLLAMA_CLOUD = CloudPreset(
    name="ollama-cloud",
    display_name="Ollama Cloud",
    description="Hosted Ollama models (managed inference) at ollama.com.",
    driver="litellm",
    litellm_provider="ollama",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    # ollama.com is the canonical hosted Ollama endpoint that LiteLLM's
    # ``ollama`` provider targets when a base URL is supplied. Users
    # with a private deployment may override the field; the default
    # matches the public service so the form is submit-ready after
    # entering an API key alone.
    default_base_url="https://ollama.com",
    requires_base_url=False,
    default_models=(),
)

# ── Self-hosted / local ────────────────────────────────────────

_OLLAMA = LocalPreset(
    name="ollama",
    display_name="Ollama",
    description="Local Ollama inference server",
    driver="litellm",
    litellm_provider="ollama",
    auth_type=AuthType.NONE,
    default_base_url="http://localhost:11434",
    requires_base_url=True,
    candidate_urls=(
        "http://host.docker.internal:11434",
        "http://172.17.0.1:11434",
        "http://localhost:11434",
    ),
    supports_model_pull=True,
    supports_model_delete=True,
    supports_model_config=True,
)

_LM_STUDIO = LocalPreset(
    name="lm-studio",
    display_name="LM Studio",
    description="Local LLM development environment",
    driver="litellm",
    litellm_provider="openai",
    auth_type=AuthType.NONE,
    default_base_url="http://localhost:1234/v1",
    requires_base_url=True,
    candidate_urls=(
        "http://host.docker.internal:1234/v1",
        "http://172.17.0.1:1234/v1",
        "http://localhost:1234/v1",
    ),
)

_VLLM = LocalPreset(
    name="vllm",
    display_name="vLLM",
    description="High-throughput local inference engine",
    driver="litellm",
    litellm_provider="openai",
    auth_type=AuthType.NONE,
    default_base_url="http://localhost:8000/v1",
    requires_base_url=True,
    # candidate_urls intentionally empty: vLLM's default port (8000)
    # is a common collision risk (the SynthOrg backend formerly used
    # 8000).  Users must specify the vLLM URL explicitly or remap
    # vLLM to a non-colliding port.
    candidate_urls=(),
)

# ── Gateways ───────────────────────────────────────────────────

_OPENROUTER = CloudPreset(
    name="openrouter",
    display_name="OpenRouter",
    description="Multi-provider API gateway",
    driver="litellm",
    litellm_provider="openrouter",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_base_url="https://openrouter.ai/api/v1",
    default_models=(),
)

_SAMBANOVA = CloudPreset(
    name="sambanova",
    display_name="SambaNova",
    description="High-throughput Llama inference",
    driver="litellm",
    litellm_provider="sambanova",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)

_TOGETHER = CloudPreset(
    name="together_ai",
    display_name="Together AI",
    description="Open-model gateway (Llama, Qwen, DeepSeek, Mixtral)",
    driver="litellm",
    litellm_provider="together_ai",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)

_XAI = CloudPreset(
    name="xai",
    display_name="xAI (Grok)",
    description="xAI Grok reasoning and chat models",
    driver="litellm",
    litellm_provider="xai",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)


_FEATURED_PRESETS: tuple[CloudPreset | LocalPreset, ...] = (
    # Cloud (alphabetical by preset name)
    _ANTHROPIC,
    _AZURE_OPENAI,
    _CEREBRAS,
    _COHERE,
    _DEEPSEEK,
    _FIREWORKS,
    _GEMINI,
    _GROQ,
    _MISTRAL,
    _MOONSHOT,
    _NVIDIA_NIM,
    _OLLAMA_CLOUD,
    _OPENAI,
    _OPENROUTER,
    _SAMBANOVA,
    _TOGETHER,
    _XAI,
    # Self-hosted providers, alphabetical
    _LM_STUDIO,
    _OLLAMA,
    _VLLM,
)
"""Hand-curated presets with branding (logo, description, default
models).  Featured presets render in the wizard's primary grid."""


# ── Auto-derived "soft" presets ────────────────────────────────
#
# The discovery + denylist machinery lives in
# :mod:`synthorg.providers.preset_softlist` to keep this module
# under the project's 800-line file ceiling.  See that module's
# docstring for the maintainer note about LiteLLM dependency bumps.

_SOFT_PRESETS: tuple[CloudPreset, ...] = build_soft_presets(_FEATURED_PRESETS)
"""Auto-derived soft presets, one per LiteLLM chat namespace not
already covered by :data:`_FEATURED_PRESETS` or denied by the
soft-list module's denylist.  Computed once at module load because
``litellm.model_cost`` is itself a static module-level table.
"""


PROVIDER_PRESETS: tuple[CloudPreset | LocalPreset, ...] = (
    *_FEATURED_PRESETS,
    *_SOFT_PRESETS,
)
"""All available presets.  Featured (hand-curated, branded) entries
land first, in the order declared in :data:`_FEATURED_PRESETS`; soft
(auto-derived from ``litellm.model_cost``) entries follow,
alphabetical by namespace."""

audit_presets(PROVIDER_PRESETS)

_PRESET_LOOKUP: MappingProxyType[str, CloudPreset | LocalPreset] = MappingProxyType(
    {p.name: p for p in PROVIDER_PRESETS},
)


def get_preset(name: str) -> CloudPreset | LocalPreset | None:
    """Look up a preset by name.

    Args:
        name: Preset identifier (e.g. ``"ollama"``).

    Returns:
        The matching preset, or ``None`` if not found.
    """
    return _PRESET_LOOKUP.get(name)


def list_presets() -> tuple[CloudPreset | LocalPreset, ...]:
    """Return all available presets (featured first, then soft).

    Returns:
        Tuple of all provider presets (cloud + local).  Featured
        (hand-curated, branded) presets are first; soft (auto-derived
        from ``litellm.model_cost``) presets follow.
    """
    return PROVIDER_PRESETS


def list_featured_presets() -> tuple[CloudPreset | LocalPreset, ...]:
    """Return only the hand-curated (branded) presets.

    Returns:
        Tuple of presets where :attr:`is_featured` is ``True``.  Used
        by the wizard's primary grid to surface the curated set
        separately from the auto-derived "More providers" section.
    """
    return tuple(p for p in PROVIDER_PRESETS if p.is_featured)


def list_soft_presets() -> tuple[CloudPreset, ...]:
    """Return only the auto-derived soft presets.

    Returns:
        Tuple of :class:`CloudPreset` instances where
        :attr:`is_featured` is ``False``.  Used by the wizard's
        "More providers" section.
    """
    return _SOFT_PRESETS


def list_local_presets() -> tuple[LocalPreset, ...]:
    """Return only the local-server presets.

    Returns:
        Tuple of presets for self-hosted backends, regardless of whether
        they have ``candidate_urls`` configured.
    """
    return tuple(p for p in PROVIDER_PRESETS if isinstance(p, LocalPreset))


def list_probable_presets() -> tuple[LocalPreset, ...]:
    """Return local presets that have at least one candidate URL.

    Used by the wizard's batch probe endpoint.  Excludes vLLM
    (deliberately no candidate URLs -- port-collision risk).

    Returns:
        Tuple of local presets with non-empty ``candidate_urls``.
    """
    return tuple(p for p in list_local_presets() if p.candidate_urls)


def candidate_urls_for(preset: CloudPreset | LocalPreset) -> tuple[str, ...]:
    """Return candidate URLs for any preset.

    Cloud presets always return an empty tuple (they have no
    auto-detect surface).  Lets consumers iterate across the union
    without ``isinstance`` branches when they only care about the
    URL list.

    Returns:
        Tuple of candidate URLs for ``LocalPreset`` instances; empty
        tuple for ``CloudPreset`` instances.
    """
    return preset.candidate_urls if isinstance(preset, LocalPreset) else ()


def default_models_for(
    preset: CloudPreset | LocalPreset,
) -> tuple[ProviderModelConfig, ...]:
    """Return default models for any preset.

    Local presets always return an empty tuple (they discover models
    from the running server, not from a prefilled list).  Lets
    consumers iterate across the union without ``isinstance``
    branches when they only care about the prefilled model list.

    Returns:
        Tuple of model configs for ``CloudPreset`` instances; empty
        tuple for ``LocalPreset`` instances.
    """
    return preset.default_models if isinstance(preset, CloudPreset) else ()


# ── Model generation filters ─────────────────────────────────
# Provider-specific model generation allowlists for
# ``models_from_litellm()``.  Only models matching the pattern
# are included.  Providers not listed here include all models.
# Patterns must be updated when new major generations are released.
# Vendor-specific names are allowed here per CLAUDE.md:
# "provider presets (presets.py) which are user-facing runtime data".

MODEL_VERSION_FILTERS: Final[MappingProxyType[str, re.Pattern[str]]] = MappingProxyType(
    {
        "anthropic": re.compile(r"^claude-(opus|sonnet|haiku)-4-[56789]"),
    }
)
