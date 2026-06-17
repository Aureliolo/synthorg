# module-kind: code
"""Live-discovery model-presence probe.

Extends the ``ModelPresenceProbe`` seam with a probe that hits the
provider's live catalogue (rather than the offline LiteLLM database the
default ``StaticPresenceProbe`` uses).  For providers with a ``base_url``
it performs read-only discovery (no persistence) and stamps
``metadata_source="probe"`` on the discovered models; for cloud providers
without a ``base_url`` it falls back to the offline LiteLLM catalogue
(keeping ``metadata_source="litellm"`` provenance).  Like the static
probe, an empty discovered set is a
documented no-op so a transient failure never reports every configured
model as absent.
"""

from collections.abc import Callable
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_MODEL_ABSENT
from synthorg.providers.management._helpers import models_from_litellm
from synthorg.providers.management.model_presence_probe import ModelPresenceReport

logger = get_logger(__name__)


@runtime_checkable
class ReadonlyModelDiscovery(Protocol):
    """Read-only live discovery source (the provider management service)."""

    async def discover_models_readonly(
        self,
        name: str,
        *,
        preset_hint: str | None = None,
    ) -> tuple[ProviderModelConfig, ...]:
        """Discover a provider's live models without persisting."""
        ...


class LiveCatalogReport(BaseModel):
    """Outcome of a live catalogue probe for one provider.

    Carries both the presence diff (so it can project down to a
    :class:`ModelPresenceReport`) and the freshly discovered model
    configs (so the refresh service can persist refreshed metadata).

    Attributes:
        provider_name: The probed provider.
        discovered: Models the live catalogue advertised (empty on a
            documented no-op).
        missing_ids: Configured ids absent from the live catalogue.
        added_ids: Discovered ids not currently configured.
        checked_ids: Configured ids actually checked (empty no-op).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider_name: NotBlankStr
    discovered: tuple[ProviderModelConfig, ...] = Field(default=())
    missing_ids: tuple[str, ...] = Field(default=())
    added_ids: tuple[str, ...] = Field(default=())
    checked_ids: tuple[str, ...] = Field(default=())

    @model_validator(mode="after")
    def _diff_coherent(self) -> Self:
        """Enforce the no-op and diff-coherence invariants.

        An empty ``discovered`` set is a documented no-op, so every diff
        field must also be empty; otherwise ``missing_ids`` must be a
        subset of ``checked_ids`` (a configured id cannot be reported
        missing without having been checked). Guards against an incoherent
        report driving an unjustified stale flag downstream.

        Returns:
            The validated report.

        Raises:
            ValueError: If the no-op or subset invariant is violated.
        """
        if not self.discovered:
            if self.missing_ids or self.added_ids or self.checked_ids:
                msg = "empty discovered implies empty missing/added/checked ids"
                raise ValueError(msg)
            return self
        if not set(self.missing_ids).issubset(self.checked_ids):
            msg = "missing_ids must be a subset of checked_ids"
            raise ValueError(msg)
        return self


def _stamp_probe_source(model: ProviderModelConfig) -> ProviderModelConfig:
    """Return *model* with its metadata provenance set to ``probe``.

    Returns:
        A copy whose ``metadata.metadata_source`` is ``"probe"``,
        marking it as live-discovered.
    """
    probed_metadata = model.metadata.model_copy(update={"metadata_source": "probe"})
    return model.model_copy(update={"metadata": probed_metadata})


class LiveDiscoveryProbe:
    """Live-catalogue presence probe satisfying ``ModelPresenceProbe``.

    Performs read-only discovery against a provider's ``base_url`` and
    falls back to the offline LiteLLM catalogue for cloud providers that
    expose no ``base_url``.
    """

    def __init__(
        self,
        *,
        discovery: ReadonlyModelDiscovery,
        catalog: Callable[[str], tuple[ProviderModelConfig, ...]] = models_from_litellm,
    ) -> None:
        """Initialise the probe.

        Args:
            discovery: Read-only live discovery source (the provider
                management service).
            catalog: Offline LiteLLM catalogue lookup used as the cloud
                fallback (injected for tests).
        """
        self._discovery = discovery
        self._catalog = catalog

    async def discover_report(
        self,
        provider_name: str,
        provider: ProviderConfig,
    ) -> LiveCatalogReport:
        """Probe the live catalogue and return the full diff + discovered set.

        Returns:
            A :class:`LiveCatalogReport`; ``checked_ids`` and
            ``discovered`` are empty when no catalogue was available
            (a documented no-op).
        """
        configured = tuple(m.id for m in provider.models)
        if provider.base_url is not None:
            live = await self._discovery.discover_models_readonly(provider_name)
            discovered = tuple(_stamp_probe_source(m) for m in live)
        else:
            discovered = self._catalog(provider.litellm_provider or provider_name)

        if not discovered:
            return LiveCatalogReport(provider_name=provider_name)

        discovered_ids = {m.id for m in discovered}
        configured_set = set(configured)
        missing = tuple(mid for mid in configured if mid not in discovered_ids)
        added = tuple(m.id for m in discovered if m.id not in configured_set)
        for mid in missing:
            logger.warning(PROVIDER_MODEL_ABSENT, provider=provider_name, model=mid)
        return LiveCatalogReport(
            provider_name=provider_name,
            discovered=discovered,
            missing_ids=missing,
            added_ids=added,
            checked_ids=configured,
        )

    async def probe(
        self,
        provider_name: str,
        provider: ProviderConfig,
    ) -> ModelPresenceReport:
        """Return the presence-only projection of the live catalogue diff.

        Returns:
            A :class:`ModelPresenceReport` carrying the missing/checked
            ids (satisfies the ``ModelPresenceProbe`` protocol).
        """
        report = await self.discover_report(provider_name, provider)
        return ModelPresenceReport(
            provider_name=provider_name,
            missing_ids=report.missing_ids,
            checked_ids=report.checked_ids,
        )
