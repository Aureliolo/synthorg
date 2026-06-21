# module-kind: declarative
"""Provider-preset model definitions (the ``CloudPreset`` / ``LocalPreset`` union).

Schema-only module extracted from ``presets.py`` so the preset DATA and
accessor helpers stay in ``presets.py`` (the single vendor-name-allowlisted
module). These classes carry no vendor names.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.providers.enums import AuthType

logger = get_logger(__name__)


class _BasePreset(BaseModel):
    """Common fields shared by every preset kind.

    Not instantiated directly -- use :class:`CloudPreset` or
    :class:`LocalPreset`.

    Attributes:
        name: Machine-readable preset identifier.
        display_name: Human-readable display name.
        description: Short description of the provider.
        driver: Driver backend name.
        litellm_provider: LiteLLM routing identifier.
        auth_type: Default authentication type.
        default_base_url: Default API base URL.
        requires_base_url: Whether the user must supply a base URL.
            ``False`` for cloud providers (the routing library knows
            the URL), ``True`` for self-hosted and deployment-specific
            backends (per-deployment).
        is_featured: Whether this preset is hand-curated (logo, vetted
            description, default-model fallbacks) versus auto-derived
            from ``litellm.model_cost``.  Featured presets render in
            the wizard's primary grid; non-featured (soft) presets
            render in the "More providers" section.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    display_name: NotBlankStr
    description: NotBlankStr
    driver: NotBlankStr
    litellm_provider: NotBlankStr
    auth_type: AuthType
    default_base_url: NotBlankStr | None = None
    requires_base_url: bool = False
    is_featured: bool = True


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
        prefer_live_discovery: When ``True``, ``create_from_preset`` seeds
            from ``default_models`` instead of the static
            ``litellm.model_cost`` table (which would surface the wrong
            catalogue for an OpenAI-compatible gateway) and runs an
            authenticated live model discovery against the provider's
            endpoint so the full live catalogue is populated on create.
            Used by gateways like Ollama Cloud whose live ``/v1/models``
            is the source of truth.
    """

    kind: Literal["cloud"] = "cloud"
    supported_auth_types: tuple[AuthType, ...] = Field(
        default=(AuthType.API_KEY,),
        min_length=1,
    )
    default_models: tuple[ProviderModelConfig, ...] = ()
    prefer_live_discovery: bool = False

    @model_validator(mode="after")
    def _validate_auth_type_in_supported(self) -> Self:
        """Ensure default ``auth_type`` is in the supported set.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``auth_type`` is not in ``supported_auth_types``.
        """
        if self.auth_type not in self.supported_auth_types:
            msg = (
                f"auth_type {self.auth_type!r} not in "
                f"supported_auth_types {self.supported_auth_types!r}"
            )
            logger.error(
                CONFIG_VALIDATION_FAILED,
                model="CloudPreset",
                preset_name=self.name,
                auth_type=self.auth_type.value,
                supported_auth_types=[t.value for t in self.supported_auth_types],
                error=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_soft_preset_shape(self) -> Self:
        """Ensure soft presets follow the API-key-only contract.

        Soft presets are auto-derived from ``litellm.model_cost`` and
        cannot reasonably support subscription / OAuth / custom-header
        auth without per-provider research.  Today only
        :func:`_make_soft_preset` constructs ``is_featured=False``
        instances, but encoding the invariant on the type prevents a
        future caller from minting a misconfigured soft preset.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If a soft preset (``is_featured=False``) uses a
                non-``API_KEY`` ``auth_type`` or its
                ``supported_auth_types`` is not exactly ``(API_KEY,)``.
        """
        if self.is_featured:
            return self
        if self.auth_type != AuthType.API_KEY:
            msg = (
                f"Soft preset {self.name!r} (is_featured=False) must use "
                f"AuthType.API_KEY; got {self.auth_type!r}."
            )
            logger.error(
                CONFIG_VALIDATION_FAILED,
                model="CloudPreset",
                preset_name=self.name,
                is_featured=self.is_featured,
                auth_type=self.auth_type.value,
                error=msg,
            )
            raise ValueError(msg)
        if self.supported_auth_types != (AuthType.API_KEY,):
            msg = (
                f"Soft preset {self.name!r} (is_featured=False) must declare "
                f"supported_auth_types=(API_KEY,); got "
                f"{self.supported_auth_types!r}."
            )
            logger.error(
                CONFIG_VALIDATION_FAILED,
                model="CloudPreset",
                preset_name=self.name,
                is_featured=self.is_featured,
                supported_auth_types=[t.value for t in self.supported_auth_types],
                error=msg,
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
