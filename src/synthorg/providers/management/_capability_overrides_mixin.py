# module-kind: service
"""Mixin for the operator model-capability-override mutation.

Split out of ``_capabilities_mixin.py`` so that file stays under the
project's 600-line service-tier ceiling; composed onto
``ProviderManagementService`` via the same plain-MRO pattern.
"""

import asyncio
from typing import Protocol, cast

from pydantic import JsonValue

from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_CAPABILITY_OVERRIDES_UPDATED,
    PROVIDER_NOT_FOUND,
)
from synthorg.providers.errors import ProviderNotFoundError
from synthorg.providers.management._capability_helpers import (
    resolve_capability_override_update,
)
from synthorg.providers.management.capability_dtos import (
    CapabilityOverridesUpdateRequest,
    ProviderAuditEventType,
)
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


# Narrows the mixin's self-type to what it consumes from the host
# service; mirrors ``_capabilities_mixin.py``'s ``_ServiceProtocol``.
class _ServiceProtocol(Protocol):
    """Subset of ``ProviderManagementService`` accessed by this mixin."""

    _lock: asyncio.Lock
    _config_resolver: ConfigResolver

    async def _validate_and_persist(
        self, new_providers: dict[str, ProviderConfig]
    ) -> None:
        """Validate + persist + hot-reload providers (provided by host)."""
        ...

    async def _audit(
        self,
        *,
        provider_name: str,
        event_type: ProviderAuditEventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        """Emit one provider audit event (provided by ``ProviderCapabilitiesMixin``)."""
        ...


class ProviderCapabilityOverridesMixin:
    """Mutation for one model's operator-declared capability overrides.

    Composed into ``ProviderManagementService`` via plain Python MRO,
    alongside ``ProviderCapabilitiesMixin`` (whose ``_audit`` this mixin
    calls through the host).
    """

    async def update_model_capability_overrides(
        self: _ServiceProtocol,
        name: str,
        model_id: str,
        request: CapabilityOverridesUpdateRequest,
    ) -> ProviderModelConfig:
        """Apply a partial update to one model's capability overrides.

        Unlike ``update_model_config`` (local-provider launch parameters
        only), this applies to any provider: the defect it fixes -- a
        capability card silent on a field with no probe to fall back to --
        is not local-provider-specific. Merges onto any existing overrides
        so a prior override on a different field survives.

        Returns:
            The updated ``ProviderModelConfig`` carrying the merged
            overrides.

        Raises:
            ProviderNotFoundError: If the named provider does not exist.
            ProviderModelNotFoundError: If the model does not exist on
                the provider.
        """
        explicit = request.model_dump(exclude_unset=True)
        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            existing = providers.get(name)
            if existing is None:
                msg = f"Provider {name!r} not found"
                logger.warning(PROVIDER_NOT_FOUND, provider=name, error=msg)
                raise ProviderNotFoundError(msg)
            updated, updated_model = resolve_capability_override_update(
                existing,
                provider_name=name,
                model_id=model_id,
                explicit=explicit,
            )
            new_providers = {**providers, name: updated}
            await self._validate_and_persist(new_providers)

        logger.info(
            PROVIDER_MODEL_CAPABILITY_OVERRIDES_UPDATED,
            provider=name,
            model=model_id,
            fields_changed=sorted(explicit.keys()),
        )
        await self._audit(
            provider_name=name,
            event_type="model_config_updated",
            payload={
                "model_id": model_id,
                "fields_changed": cast("list[JsonValue]", sorted(explicit.keys())),
            },
        )
        return updated_model
