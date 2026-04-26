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

Consumers iterating across all presets should use the helpers
:func:`default_models_for`, :func:`candidate_urls_for`, and
:func:`list_local_presets` instead of conditional ``isinstance`` checks.
"""

import re
from types import MappingProxyType
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.providers.enums import AuthType


class _BasePreset(BaseModel):
    """Common fields shared by every preset kind.

    Not instantiated directly -- use :class:`CloudPreset` or
    :class:`LocalPreset`.

    Attributes:
        name: Machine-readable preset identifier.
        display_name: Human-readable display name.
        description: Short description of the provider.
        driver: Driver backend name.
        litellm_provider: LiteLLM routing identifier (e.g. ``"anthropic"``).
        auth_type: Default authentication type.
        default_base_url: Default API base URL.
        requires_base_url: Whether the user must supply a base URL.
            ``False`` for cloud providers (the routing library knows
            the URL), ``True`` for self-hosted and deployment-specific
            backends (per-deployment).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    name: NotBlankStr
    display_name: NotBlankStr
    description: NotBlankStr
    driver: NotBlankStr
    litellm_provider: NotBlankStr
    auth_type: AuthType
    default_base_url: NotBlankStr | None = None
    requires_base_url: bool = False


class CloudPreset(_BasePreset):
    """Hosted LLM provider (no auto-detect, prefilled model list).

    Attributes:
        kind: Discriminator literal ``"cloud"``.
        supported_auth_types: All auth types this preset supports.
            Shown in the UI so users can choose (e.g. API key or
            subscription).
        default_models: Pre-configured model definitions used as a
            fallback when the LiteLLM model_cost database returns no
            entries for ``litellm_provider``.
    """

    kind: Literal["cloud"] = "cloud"
    supported_auth_types: tuple[AuthType, ...] = Field(
        default=(AuthType.API_KEY,),
        min_length=1,
    )
    default_models: tuple[ProviderModelConfig, ...] = ()

    @model_validator(mode="after")
    def _validate_auth_type_in_supported(self) -> Self:
        """Ensure default ``auth_type`` is in the supported set."""
        if self.auth_type not in self.supported_auth_types:
            msg = (
                f"auth_type {self.auth_type!r} not in "
                f"supported_auth_types {self.supported_auth_types!r}"
            )
            raise ValueError(msg)
        return self


class LocalPreset(_BasePreset):
    """Self-hosted LLM server (auto-detect via candidate URLs).

    Attributes:
        kind: Discriminator literal ``"local"``.
        candidate_urls: URLs to probe during auto-detection, in priority
            order.  The first reachable URL becomes the base URL.  May
            be empty when the local server runs on user-chosen ports
            (e.g. vLLM) -- such presets are configured manually only.
        supports_model_pull: Whether pulling/downloading models is
            supported via the provider's management API.
        supports_model_delete: Whether deleting models is supported.
        supports_model_config: Whether per-model launch parameter
            configuration (e.g. context window, GPU layers) is supported.
    """

    kind: Literal["local"] = "local"
    candidate_urls: tuple[NotBlankStr, ...] = ()
    supports_model_pull: bool = False
    supports_model_delete: bool = False
    supports_model_config: bool = False


ProviderPreset = Annotated[CloudPreset | LocalPreset, Field(discriminator="kind")]
"""Discriminated union of all preset kinds.

Pydantic models receiving a ``ProviderPreset`` use the ``kind``
discriminator to deserialize into the correct concrete type.
"""


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

_OLLAMA_CLOUD = CloudPreset(
    name="ollama-cloud",
    display_name="Ollama Cloud",
    description=(
        "Hosted Ollama models (managed inference). Supply the API base URL"
        " from your ollama.com account."
    ),
    driver="litellm",
    litellm_provider="ollama",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    # No default base URL on purpose: the canonical hosted endpoint can
    # change as the service evolves, and we should not bake an unverified
    # marketing URL into the form.  Users supply the URL from their
    # ollama.com account; once a stable canonical endpoint is documented
    # we can ship a safe default and flip ``requires_base_url`` back.
    default_base_url=None,
    requires_base_url=True,
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


PROVIDER_PRESETS: tuple[CloudPreset | LocalPreset, ...] = (
    # Cloud (alphabetical)
    _ANTHROPIC,
    _AZURE_OPENAI,
    _DEEPSEEK,
    _GEMINI,
    _GROQ,
    _MISTRAL,
    _OLLAMA_CLOUD,
    _OPENAI,
    _OPENROUTER,
    # Self-hosted (alphabetical)
    _LM_STUDIO,
    _OLLAMA,
    _VLLM,
)

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
    """Return all available presets.

    Returns:
        Tuple of all provider presets (cloud + local).
    """
    return PROVIDER_PRESETS


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
