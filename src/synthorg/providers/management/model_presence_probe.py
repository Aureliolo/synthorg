"""Runtime model-presence probe.

Flags when a configured or preset-baked model id is no longer advertised
by its provider, so operators can spot stale references.  The default
:class:`StaticPresenceProbe` compares against the offline LiteLLM
catalogue (no network); the :class:`ModelPresenceProbe` protocol is the
seam a future live-discovery probe plugs into.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.config.schema import ProviderConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_MODEL_ABSENT
from synthorg.providers.management._helpers import models_from_litellm

logger = get_logger(__name__)


class ModelPresenceReport(BaseModel):
    """Outcome of a presence probe for one provider.

    Attributes:
        provider_name: The probed provider.
        missing_ids: Configured ids absent from the provider's catalogue.
        checked_ids: Configured ids that were actually checked (empty when
            no catalogue was available, i.e. a documented no-op).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider_name: NotBlankStr
    missing_ids: tuple[str, ...] = Field(default=())
    checked_ids: tuple[str, ...] = Field(default=())


@runtime_checkable
class ModelPresenceProbe(Protocol):
    """Reports configured models absent from a provider's catalogue."""

    async def probe(
        self,
        provider_name: str,
        provider: ProviderConfig,
    ) -> ModelPresenceReport:
        """Return a presence report for *provider*."""
        ...


class StaticPresenceProbe:
    """Default probe comparing configured ids to the LiteLLM catalogue.

    No network I/O: the available set is re-derived from
    :func:`models_from_litellm`.  When that catalogue is empty (local
    providers, or LiteLLM absent) the probe is a documented no-op rather
    than reporting every configured model as absent.
    """

    async def probe(
        self,
        provider_name: str,
        provider: ProviderConfig,
    ) -> ModelPresenceReport:
        """Flag configured ids absent from the LiteLLM catalogue.

        Returns:
            A :class:`ModelPresenceReport`; ``checked_ids`` is empty when
            no catalogue is available for the provider.
        """
        litellm_provider = provider.litellm_provider or provider_name
        available = {m.id for m in models_from_litellm(litellm_provider)}
        configured = tuple(m.id for m in provider.models)
        if not available:
            return ModelPresenceReport(provider_name=provider_name)

        missing = tuple(mid for mid in configured if mid not in available)
        for mid in missing:
            logger.warning(
                PROVIDER_MODEL_ABSENT,
                provider=provider_name,
                model=mid,
            )
        return ModelPresenceReport(
            provider_name=provider_name,
            missing_ids=missing,
            checked_ids=configured,
        )
