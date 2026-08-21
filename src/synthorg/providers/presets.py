# module-kind: declarative
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
from functools import cache
from types import MappingProxyType
from typing import Final

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderModelConfig
from synthorg.core.billing_enums import BillingModel
from synthorg.providers._preset_audit import audit_presets
from synthorg.providers.enums import AuthType
from synthorg.providers.family_parser import FamilyRule
from synthorg.providers.preset_models import (
    CloudPreset,
    LocalPreset,
    ProviderPreset,
)
from synthorg.providers.preset_softlist import build_soft_presets

__all__ = [
    "MODEL_FAMILY_RULES",
    "MODEL_VERSION_FILTERS",
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
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                supports_reasoning=True,
                max_output_tokens=64_000,
                family="claude-sonnet",
                generation=4.6,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="claude-haiku-4-5-20251001",
            alias="haiku",
            cost_per_1k_input=0.0008,
            cost_per_1k_output=0.004,
            max_context=200_000,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                supports_reasoning=True,
                max_output_tokens=32_000,
                family="claude-haiku",
                generation=4.5,
                metadata_source="preset",
            ),
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
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                max_output_tokens=32_768,
                family="gpt",
                generation=4.1,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="gpt-4.1-mini",
            alias="gpt4-mini",
            cost_per_1k_input=0.0004,
            cost_per_1k_output=0.0016,
            max_context=1_047_576,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                max_output_tokens=32_768,
                family="gpt-mini",
                generation=4.1,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="o3",
            alias="o3",
            cost_per_1k_input=0.002,
            cost_per_1k_output=0.008,
            max_context=200_000,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                supports_reasoning=True,
                max_output_tokens=100_000,
                family="o",
                generation=3.0,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            # Image-output model: billed per image, not per token, so
            # ``max_context`` is nominal and ``cost_per_image`` carries the
            # price (operator-tunable, like the token costs).
            id="gpt-image-1",
            alias="gpt-image",
            cost_per_image=0.04,
            max_context=1,
            metadata=ModelMetadata(
                supports_image_generation=True,
                family="gpt-image",
                generation=1.0,
                metadata_source="preset",
            ),
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
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                supports_reasoning=True,
                max_output_tokens=65_536,
                family="gemini-pro",
                generation=2.5,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="gemini-2.5-flash",
            alias="gemini-flash",
            cost_per_1k_input=0.00015,
            cost_per_1k_output=0.0006,
            max_context=1_048_576,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                max_output_tokens=65_536,
                family="gemini-flash",
                generation=2.5,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            # Image-output model: billed per image (nominal ``max_context``).
            id="imagen-4.0-generate-001",
            alias="imagen",
            cost_per_image=0.04,
            max_context=1,
            metadata=ModelMetadata(
                supports_image_generation=True,
                family="imagen",
                generation=4.0,
                metadata_source="preset",
            ),
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
    # Generations are per-family and intentionally differ: Mistral Large 2
    # (``-2411``) is generation 2, Mistral Small 3 (``-2503``) is
    # generation 3, matching Mistral's own model naming.
    default_models=(
        ProviderModelConfig(
            id="mistral-large-2411",
            alias="mistral-large",
            cost_per_1k_input=0.002,
            cost_per_1k_output=0.006,
            max_context=128_000,
            metadata=ModelMetadata(
                supports_tools=True,
                max_output_tokens=8_192,
                family="mistral-large",
                generation=2.0,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="mistral-small-2503",
            alias="mistral-small",
            cost_per_1k_input=0.0002,
            cost_per_1k_output=0.0006,
            max_context=128_000,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                max_output_tokens=8_192,
                family="mistral-small",
                generation=3.0,
                metadata_source="preset",
            ),
        ),
    ),
)

_MOONSHOT = CloudPreset(
    name="moonshot",
    display_name="Moonshot AI (Kimi)",
    description="Kimi long-context models from Moonshot AI",
    driver="litellm",
    litellm_provider="moonshot",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(
        ProviderModelConfig(
            id="kimi-k2-0905-preview",
            alias="kimi-k2",
            cost_per_1k_input=0.0006,
            cost_per_1k_output=0.0025,
            max_context=256_000,
            metadata=ModelMetadata(
                supports_tools=True,
                max_output_tokens=16_384,
                family="kimi",
                generation=2.0,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="moonshot-v1-128k",
            alias="moonshot-128k",
            cost_per_1k_input=0.0012,
            cost_per_1k_output=0.0012,
            max_context=128_000,
            metadata=ModelMetadata(
                supports_tools=True,
                max_output_tokens=8_192,
                family="moonshot",
                generation=1.0,
                metadata_source="preset",
            ),
        ),
    ),
)

_NVIDIA_NIM = CloudPreset(
    name="nvidia_nim",
    display_name="NVIDIA NIM",
    description="NVIDIA-hosted inference for Llama, Qwen, and others",
    driver="litellm",
    litellm_provider="nvidia_nim",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    # No curated seed: the NIM catalogue spans many third-party families
    # (Llama, Qwen, Mistral, ...) that churn frequently, so create seeds
    # from ``litellm.model_cost`` and the operator refreshes via the
    # explicit /discover-models endpoint rather than a hand-maintained list.
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
    default_models=(
        ProviderModelConfig(
            id="llama-3.3-70b-versatile",
            alias="groq-llama",
            cost_per_1k_input=0.00059,
            cost_per_1k_output=0.00079,
            max_context=128_000,
            metadata=ModelMetadata(
                supports_tools=True,
                max_output_tokens=32_768,
                family="llama",
                generation=3.3,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="openai/gpt-oss-120b",
            alias="groq-gpt-oss",
            cost_per_1k_input=0.00015,
            cost_per_1k_output=0.00075,
            max_context=131_072,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_reasoning=True,
                max_output_tokens=32_768,
                family="gpt-oss",
                generation=1.0,
                metadata_source="preset",
            ),
        ),
    ),
)

_DEEPSEEK = CloudPreset(
    name="deepseek",
    display_name="DeepSeek",
    description="DeepSeek reasoning and chat models",
    driver="litellm",
    litellm_provider="deepseek",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(
        ProviderModelConfig(
            id="deepseek-chat",
            alias="deepseek-chat",
            cost_per_1k_input=0.00027,
            cost_per_1k_output=0.0011,
            max_context=128_000,
            metadata=ModelMetadata(
                supports_tools=True,
                max_output_tokens=8_192,
                family="deepseek-chat",
                generation=3.0,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="deepseek-reasoner",
            alias="deepseek-reasoner",
            cost_per_1k_input=0.00055,
            cost_per_1k_output=0.00219,
            max_context=128_000,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_reasoning=True,
                max_output_tokens=65_536,
                family="deepseek-reasoner",
                generation=1.0,
                metadata_source="preset",
            ),
        ),
    ),
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

_MAMMOUTH = CloudPreset(
    name="mammouth",
    display_name="Mammouth",
    description="Multi-model gateway (mammouth.ai) with one subscription",
    driver="litellm",
    # Mammouth exposes an OpenAI-compatible endpoint (https://api.mammouth.ai/v1)
    # with a Bearer API key, so it routes through LiteLLM's ``openai`` provider
    # against the custom base URL -- the same pattern as Ollama Cloud. Its
    # catalogue aggregates many vendors (GPT, Claude, Gemini, DeepSeek, ...)
    # under bare model ids, so ``prefer_live_discovery`` pulls the full live
    # catalogue from ``/v1/models`` rather than the static ``litellm.model_cost``
    # table (which under ``litellm_provider="openai"`` would surface OpenAI's
    # catalogue).
    litellm_provider="openai",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_base_url="https://api.mammouth.ai/v1",
    requires_base_url=False,
    prefer_live_discovery=True,
    billing_model=BillingModel.FLAT_RATE,
    # No seed: a live-discovery gateway's models come from the live
    # ``/v1/models`` catalogue. A curated seed here could only ever act as a
    # silent fallback masking a failed discovery (bad key / rate limit /
    # unreachable), so discovery is the single source of truth.
    default_models=(),
)

_OLLAMA_CLOUD = CloudPreset(
    name="ollama-cloud",
    display_name="Ollama Cloud",
    description="Hosted Ollama models (managed inference) at ollama.com",
    driver="litellm",
    # Ollama Cloud is reached through its OpenAI-compatible endpoint
    # (https://ollama.com/v1) with a Bearer API key -- the documented,
    # auth-working cloud path. The native ``ollama`` LiteLLM driver is
    # local-first and does not reliably forward the API key, so it is NOT
    # used here. ``prefer_live_discovery`` makes create seed from the
    # curated list below and then pull the full live catalogue from
    # ``/v1/models`` rather than the static ``litellm.model_cost`` table
    # (which under ``litellm_provider="openai"`` would surface OpenAI's
    # catalogue, not Ollama's).
    litellm_provider="openai",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_base_url="https://ollama.com/v1",
    requires_base_url=False,
    prefer_live_discovery=True,
    # There is no per-1k price to attribute, so cost stays 0.0 on every call
    # and a money-denominated ceiling measures nothing here.
    billing_model=BillingModel.FLAT_RATE,
    # Live discovery refreshes the catalogue; this curated list is only the
    # create-time seed, and it goes stale on the upstream's schedule rather
    # than ours. A hosted model is RETIRED, not merely deprecated: the endpoint
    # answers a create-time request with "<id> was retired at <date>", so a
    # seed nobody has refreshed hands an operator models that cannot serve a
    # single call. Two here had been retired for over a month before anyone
    # tried them. Prefer ids under a dated or numbered tag over a `:preview`
    # one, which changes under whoever pinned it.
    default_models=(
        ProviderModelConfig(
            id="gpt-oss:120b",
            alias="oss-120b",
            max_context=131_072,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_reasoning=True,
                max_output_tokens=32_768,
                family="gpt-oss",
                generation=1.0,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="deepseek-v4-pro:0813",
            alias="deepseek-v4",
            max_context=160_000,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_reasoning=True,
                max_output_tokens=32_768,
                family="deepseek-v",
                generation=4.0,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="kimi-k2.7-code",
            alias="kimi-code",
            max_context=131_072,
            metadata=ModelMetadata(
                supports_tools=True,
                max_output_tokens=32_768,
                family="kimi-k-code",
                generation=2.7,
                metadata_source="preset",
            ),
        ),
    ),
)

# ── Self-hosted / local ────────────────────────────────────────

_OLLAMA = LocalPreset(
    name="ollama",
    display_name="Ollama",
    description="Local Ollama inference server",
    driver="litellm",
    litellm_provider="ollama",
    auth_type=AuthType.NONE,
    # Local-dev defaults: Ollama runs on the operator's own machine, so
    # the localhost default and the docker-bridge candidate URLs are
    # correct for local discovery (not a deployment hardcode).
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
    # Local-dev defaults: LM Studio runs on the operator's own machine,
    # so the localhost default and docker-bridge candidate URLs are
    # correct for local discovery (not a deployment hardcode).
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
    # Local-dev default: vLLM runs on the operator's own machine, so the
    # localhost default is correct for local use (not a deployment
    # hardcode).
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
    default_models=(
        ProviderModelConfig(
            id="anthropic/claude-sonnet-4.6",
            alias="or-sonnet",
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            max_context=200_000,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                supports_reasoning=True,
                max_output_tokens=64_000,
                family="claude-sonnet",
                generation=4.6,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="openai/gpt-4.1",
            alias="or-gpt",
            cost_per_1k_input=0.002,
            cost_per_1k_output=0.008,
            max_context=1_047_576,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                max_output_tokens=32_768,
                family="gpt",
                generation=4.1,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="deepseek/deepseek-chat",
            alias="or-deepseek",
            cost_per_1k_input=0.00027,
            cost_per_1k_output=0.0011,
            max_context=128_000,
            metadata=ModelMetadata(
                supports_tools=True,
                max_output_tokens=8_192,
                family="deepseek-chat",
                generation=3.0,
                metadata_source="preset",
            ),
        ),
    ),
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
    default_models=(
        ProviderModelConfig(
            id="grok-4",
            alias="grok-4",
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            max_context=256_000,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                supports_reasoning=True,
                max_output_tokens=64_000,
                family="grok",
                generation=4.0,
                metadata_source="preset",
            ),
        ),
        ProviderModelConfig(
            id="grok-3-mini",
            alias="grok-3-mini",
            cost_per_1k_input=0.0003,
            cost_per_1k_output=0.0005,
            max_context=131_072,
            metadata=ModelMetadata(
                supports_tools=True,
                supports_reasoning=True,
                max_output_tokens=32_768,
                family="grok-mini",
                generation=3.0,
                metadata_source="preset",
            ),
        ),
    ),
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
    _MAMMOUTH,
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


@cache
def _soft_presets() -> tuple[CloudPreset, ...]:
    """Auto-derive one soft preset per uncovered LiteLLM chat namespace.

    Covers every namespace not already in :data:`_FEATURED_PRESETS` or
    denied by the soft-list module's denylist.

    Built lazily on first access rather than at import: the source
    ``litellm.model_cost`` requires importing ``litellm`` (~5s cold),
    so deferring keeps app construction and cold module imports off
    that cost until a preset listing is actually requested. The result
    is cached; two threads racing the very first call may each build it
    (``@cache`` does not serialise the miss), but the build is pure and
    deterministic, so the cached value is identical either way.

    Returns:
        Soft presets, alphabetical by namespace.
    """
    return build_soft_presets(_FEATURED_PRESETS)


@cache
def _provider_presets() -> tuple[CloudPreset | LocalPreset, ...]:
    """Return every provider preset, featured first then soft.

    Featured (hand-curated, branded) entries come first in declared
    order; soft (auto-derived) entries follow, alphabetical by namespace.

    Audited on first build; audit failures surface on first access
    (boot wiring touches presets early, so this still fails fast).

    Returns:
        Every provider preset (cloud + local).
    """
    presets = (*_FEATURED_PRESETS, *_soft_presets())
    audit_presets(presets)
    return presets


@cache
def _preset_lookup() -> MappingProxyType[str, CloudPreset | LocalPreset]:
    """Name -> preset index over :func:`_provider_presets`.

    Returns:
        Read-only mapping from preset name to preset.
    """
    return MappingProxyType({p.name: p for p in _provider_presets()})


def get_preset(name: str) -> CloudPreset | LocalPreset | None:
    """Look up a preset by name.

    Args:
        name: Preset identifier (e.g. ``"ollama"``).

    Returns:
        The matching preset, or ``None`` if not found.
    """
    return _preset_lookup().get(name)


def list_presets() -> tuple[CloudPreset | LocalPreset, ...]:
    """Return all available presets (featured first, then soft).

    Returns:
        Tuple of all provider presets (cloud + local).  Featured
        (hand-curated, branded) presets are first; soft (auto-derived
        from ``litellm.model_cost``) presets follow.
    """
    return _provider_presets()


def list_featured_presets() -> tuple[CloudPreset | LocalPreset, ...]:
    """Return only the hand-curated (branded) presets.

    Returns:
        Tuple of presets where :attr:`is_featured` is ``True``.  Used
        by the wizard's primary grid to surface the curated set
        separately from the auto-derived "More providers" section.
    """
    return tuple(p for p in _provider_presets() if p.is_featured)


def list_soft_presets() -> tuple[CloudPreset, ...]:
    """Return only the auto-derived soft presets.

    Returns:
        Tuple of :class:`CloudPreset` instances where
        :attr:`is_featured` is ``False``.  Used by the wizard's
        "More providers" section.
    """
    return _soft_presets()


def list_local_presets() -> tuple[LocalPreset, ...]:
    """Return only the local-server presets.

    Returns:
        Tuple of presets for self-hosted backends, regardless of whether
        they have ``candidate_urls`` configured.
    """
    return tuple(p for p in _provider_presets() if isinstance(p, LocalPreset))


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
        # ``4-(?:[5-9]|[1-9]\d+)`` keeps the >=4.5 floor while matching
        # multi-digit minors (4-10, 4-11, ...); a bare ``4-[5-9]`` class
        # would cap at 4.9 and exclude every model from 4.10 onward. The
        # ``[1-9]\d+`` arm (not ``\d{2,}``) rejects leading-zero minors
        # like ``4-04`` that would otherwise slip under the 4.5 floor.
        "anthropic": re.compile(r"^claude-(opus|sonnet|haiku)-4-(?:[5-9]|[1-9]\d+)"),
        "openai": re.compile(r"^(gpt-[45]|o[34])"),
        "xai": re.compile(r"^grok-[34]"),
    }
)


# ── Model family / generation parsing rules ──────────────────
# Per-provider capturing rules consumed by ``RegexFamilyParser`` to split
# a model id into a stable ``family`` label and a sortable ``generation``.
# Each rule's ``capture`` exposes named groups ``gen`` (the version token)
# plus any of ``family`` / ``date`` / a variant group referenced by
# ``family_template``.  Providers absent here fall back to the parser's
# generic heuristic.  Vendor names are allowed in this module per CLAUDE.md.

# Ollama (local + hosted) advertises a shared lineage stem with a trailing
# specialisation token -- ``qwen3-coder``, ``qwen3-embedding``,
# ``kimi-k2.7-code`` -- that is NOT a drop-in replacement for the same
# lineage's general chat model. The generic heuristic captures only the
# leading stem + first version token, collapsing coder / embedding into the
# base family, which lets the upgrade recommender surface a general (or
# embedding) model as the "newer" upgrade for a coder pin. Folding the
# specialisation into the family keeps each specialisation its own lineage;
# tier suffixes (``-pro`` / ``-flash``) and size (``:480b``) are deliberately
# left in one family so the recommender's scorer ranks within it.
_OLLAMA_FAMILY_RULES: Final[tuple[FamilyRule, ...]] = (
    FamilyRule(
        capture=re.compile(
            r"^(?P<family>[a-z][a-z._-]*?)-?(?P<gen>\d+(?:[.-]\d+)?)"
            r"-(?P<variant>coder|code|embedding|embed)",
        ),
        family_template="{family}-{variant}",
    ),
)

MODEL_FAMILY_RULES: Final[MappingProxyType[str, tuple[FamilyRule, ...]]] = (
    MappingProxyType(
        {
            "anthropic": (
                FamilyRule(
                    capture=re.compile(
                        r"^(?P<family>claude-(?:opus|sonnet|haiku))-"
                        r"(?P<gen>\d+(?:-\d+)?)$",
                    ),
                    family_template="{family}",
                ),
            ),
            "openai": (
                FamilyRule(
                    capture=re.compile(
                        r"^gpt-(?P<gen>\d+(?:\.\d+)?)(?P<variant>-mini|-nano)?$",
                    ),
                    family_template="gpt{variant}",
                ),
                FamilyRule(
                    capture=re.compile(r"^o(?P<gen>\d+)(?P<variant>-mini|-pro)?$"),
                    family_template="o{variant}",
                ),
            ),
            "gemini": (
                FamilyRule(
                    capture=re.compile(
                        r"^gemini-(?P<gen>\d+(?:\.\d+)?)-"
                        r"(?P<variant>pro|flash-lite|flash)$",
                    ),
                    family_template="gemini-{variant}",
                ),
            ),
            "mistral": (
                # Mistral versions are YYMM date codes, not semantic
                # generations; capturing them as ``gen`` would inflate the
                # cross-family quality axis. Parse the family only and let
                # within-family recency fall back to the id ordering.
                FamilyRule(
                    capture=re.compile(
                        r"^mistral-(?P<variant>large|medium|small)",
                    ),
                    family_template="mistral-{variant}",
                ),
            ),
            "xai": (
                FamilyRule(
                    capture=re.compile(
                        r"^grok-(?P<gen>\d+)(?P<variant>-mini|-fast|-vision)?$",
                    ),
                    family_template="grok{variant}",
                ),
            ),
            "moonshot": (
                FamilyRule(
                    capture=re.compile(r"^kimi-k(?P<gen>\d+)"),
                    family_template="kimi",
                ),
                FamilyRule(
                    capture=re.compile(r"^moonshot-v(?P<gen>\d+)"),
                    family_template="moonshot",
                ),
            ),
            "ollama": _OLLAMA_FAMILY_RULES,
            "ollama-cloud": _OLLAMA_FAMILY_RULES,
        }
    )
)
