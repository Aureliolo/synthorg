# module-kind: service
"""Mixin for the runtime tool-call capability mutations.

Splits the ``tool_calls_verified`` write path off
``ProviderCapabilitiesMixin`` so the capability-mutation file stays under
its size budget. These methods are the
:class:`~synthorg.providers.tool_call_feedback.tracker.ToolCallCapabilityWriter`
that the runtime tool-call feedback tracker (and the manual re-enable
endpoint) call to downgrade / re-enable a model. Each mutation reuses the
host service's lock / config-resolver / persist / audit collaborators via
the :class:`_ToolCallServiceProtocol` typing contract, mirroring
``ProviderCapabilitiesMixin``.
"""

import asyncio
from collections.abc import Mapping
from typing import Protocol

from pydantic import JsonValue

from synthorg.config.provider_schema import ProviderConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_ABSENT,
    PROVIDER_MODEL_CONFIG_UPDATED,
    PROVIDER_NOT_FOUND,
)
from synthorg.providers.errors import (
    ProviderModelNotFoundError,
    ProviderNotFoundError,
)
from synthorg.providers.management.capability_dtos import ProviderAuditEventType
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


class _ToolCallServiceProtocol(Protocol):
    """Subset of ``ProviderManagementService`` this mixin reads.

    Declared as a typing ``Protocol`` so mypy strict can verify the mixin
    is composed onto a host providing these collaborators, without
    importing the concrete service.
    """

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
        payload: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Emit one provider audit event (provided by the host via MRO)."""
        ...

    async def _apply_tool_calls_verified(
        self,
        provider: str,
        model: str,
        *,
        value: bool | None,
    ) -> bool:
        """Set a model's ``tool_calls_verified`` flag (provided by the mixin)."""
        ...


class ProviderToolCallCapabilityMixin:
    """Runtime tool-call capability mutations (``tool_calls_verified``)."""

    async def _apply_tool_calls_verified(
        self: _ToolCallServiceProtocol,
        provider: str,
        model: str,
        *,
        value: bool | None,
    ) -> bool:
        """Set one model's ``tool_calls_verified`` flag, persisting on change.

        Mirrors ``ProviderCapabilitiesMixin.flag_models_stale`` (lock,
        re-read configs, ``model_copy`` the metadata, splice the tuple,
        ``_validate_and_persist`` for the DB write + registry hot-reload +
        rollback). Idempotent: a no-op when the flag already equals
        ``value``, so a steady stream of observations never rewrites the
        provider-config blob. A success setting ``value=True`` on an
        untested (``None``) model is ALSO a no-op: optimism already selects
        an untested model, so runtime proof is not worth a config rewrite +
        hot-reload; only a genuine ``False`` -> ``True`` re-enable writes.

        Args:
            provider: Provider registry key.
            model: Model id within the provider.
            value: Target tristate (``False`` downgrade, ``True`` proven,
                ``None`` untested).

        Returns:
            ``True`` when the persisted flag actually changed, ``False`` on
            an idempotent no-op (so callers can distinguish a real
            re-enable from a steady-state success).

        Raises:
            ProviderNotFoundError: If the provider does not exist.
            ProviderModelNotFoundError: If the model does not exist.
        """
        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            existing = providers.get(provider)
            if existing is None:
                msg = f"Provider {provider!r} not found"
                logger.warning(PROVIDER_NOT_FOUND, provider=provider, error=msg)
                raise ProviderNotFoundError(msg)
            idx = next(
                (i for i, m in enumerate(existing.models) if m.id == model), None
            )
            if idx is None:
                msg = f"Model {model!r} not found on provider {provider!r}"
                logger.warning(
                    PROVIDER_MODEL_ABSENT, provider=provider, model=model, error=msg
                )
                raise ProviderModelNotFoundError(msg)
            target = existing.models[idx]
            current = target.metadata.tool_calls_verified
            if current == value or (value is True and current is None):
                return False
            new_metadata = target.metadata.model_copy(
                update={"tool_calls_verified": value}
            )
            updated_model = target.model_copy(update={"metadata": new_metadata})
            new_models = (
                *existing.models[:idx],
                updated_model,
                *existing.models[idx + 1 :],
            )
            updated = existing.model_copy(update={"models": tuple(new_models)})
            await self._validate_and_persist({**providers, provider: updated})
        logger.info(
            PROVIDER_MODEL_CONFIG_UPDATED,
            provider=provider,
            model=model,
            tool_calls_verified=value,
        )
        await self._audit(
            provider_name=provider,
            event_type="model_config_updated",
            payload={"model_id": model, "tool_calls_verified": value},
        )
        return True

    async def mark_tool_calls_unverified(
        self: _ToolCallServiceProtocol, provider: str, model: str
    ) -> bool:
        """Downgrade a model: ``tool_calls_verified`` -> ``False``.

        Called by the runtime tool-call feedback tracker when a model
        crosses the failure threshold, so the matcher stops assigning it
        to tool-requiring agents. Idempotent: a no-op (no rewrite, no
        registry hot-reload) when already ``False``.

        Returns:
            ``True`` if the flag changed, ``False`` on an idempotent no-op.
        """
        return await self._apply_tool_calls_verified(provider, model, value=False)

    async def mark_tool_calls_verified(
        self: _ToolCallServiceProtocol, provider: str, model: str
    ) -> bool:
        """Re-enable a downgraded model: ``tool_calls_verified`` ``False`` -> ``True``.

        Called by the tracker when a genuine tool call is observed. Only a
        truly-downgraded (``False``) model flips to ``True``; a success on
        an untested (``None``) model is a no-op (optimism already selects
        it, so no rewrite is worth it), as is a model already ``True``.

        Returns:
            ``True`` only when a ``False`` model was actually re-enabled, so
            the caller can log an auto-recovery distinctly.
        """
        return await self._apply_tool_calls_verified(provider, model, value=True)

    async def clear_tool_calls_verification(
        self: _ToolCallServiceProtocol, provider: str, model: str
    ) -> bool:
        """Reset to untested: ``tool_calls_verified`` -> ``None``.

        Called by the manual operator "re-enable tool calling" action so
        the matcher's optimistic path resumes (we have no runtime proof on
        a manual reset). Idempotent when already ``None``.

        Returns:
            ``True`` if the flag changed, ``False`` on an idempotent no-op.
        """
        return await self._apply_tool_calls_verified(provider, model, value=None)
