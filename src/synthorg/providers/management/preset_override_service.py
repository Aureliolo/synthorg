"""Service that owns operator-authored preset overrides.

Reads / writes :class:`PresetOverride` from
:mod:`synthorg.providers.management.capability_dtos` through a
:class:`PresetOverrideRepo`.  The DTOs imported below
(``PresetOverride``, ``PresetOverrideUpdateRequest``,
``ProviderAuditActor``) live in the management-layer DTO module rather
than the API DTO layer so this service stays inside the provider domain
boundary.  Cross-shape validation (cloud preset rejecting
``candidate_urls``, local preset rejecting ``base_url``) lives here so
the persistence layer stays semantics-free.

The "effective preset" merge (in-code preset + override) is the
responsibility of :meth:`get_effective_override` callers.  The legacy
``synthorg.providers.presets.get_preset(name)`` entry point still
serves the in-code preset directly; consumers that need overrides
applied call this service.  Migrating every caller to consult the
override at read time is a planned enhancement.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.providers.errors import ProviderValidationError
from synthorg.providers.management.capability_dtos import (
    PresetOverride,
    PresetOverrideUpdateRequest,
    ProviderAuditActor,
)
from synthorg.providers.presets import CloudPreset, LocalPreset, get_preset

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.preset_override_protocol import PresetOverrideRepo
    from synthorg.providers.management.audit_service import ProviderAuditService

logger = get_logger(__name__)


class PresetOverrideService:
    """Service-layer wrapper over :class:`PresetOverrideRepo`.

    Owns cross-shape validation against the in-code preset catalog and
    audit emission for every override write.

    Args:
        repo: A ``PresetOverrideRepo`` implementation.
        audit_service: Optional audit writer.  ``None`` is allowed for
            in-memory test rigs that do not exercise audit; production
            wiring always provides one.
    """

    def __init__(
        self,
        repo: PresetOverrideRepo,
        *,
        audit_service: ProviderAuditService | None = None,
    ) -> None:
        self._repo = repo
        self._audit_service = audit_service

    async def get_override(
        self,
        preset_name: NotBlankStr,
    ) -> PresetOverride | None:
        """Return the persisted override for ``preset_name`` or ``None``."""
        return await self._repo.get(preset_name)

    async def upsert_override(
        self,
        preset_name: NotBlankStr,
        request: PresetOverrideUpdateRequest,
        *,
        actor: ProviderAuditActor,
    ) -> PresetOverride:
        """Apply a partial-update payload to ``preset_name``'s override.

        Reads the current override (or starts from an empty one),
        merges fields from ``request.model_dump(exclude_unset=True)``,
        validates against the in-code preset's shape, persists, audits.

        Args:
            preset_name: Preset whose override to write.  Must match
                an in-code preset; unknown presets raise
                :class:`ProviderValidationError`.
            request: Partial override payload.  Empty patches are
                rejected at the DTO layer.
            actor: Audit actor (typically derived from the request's
                authenticated user).

        Returns:
            The persisted override (with ``updated_at`` / ``updated_by``
            filled in).

        Raises:
            ProviderValidationError: When the preset name is unknown
                or the override shape conflicts with the preset's
                kind (cloud vs local).
        """
        preset = get_preset(preset_name)
        if preset is None:
            msg = f"Unknown preset {preset_name!r}; cannot author override"
            raise ProviderValidationError(msg)

        existing = await self._repo.get(preset_name)
        updates = request.model_dump(exclude_unset=True)

        merged = self._build_merged(preset_name, existing, updates, actor)
        self._validate_against_preset(preset, merged)

        await self._repo.save(merged)
        if self._audit_service is not None:
            await self._audit_service.record(
                provider_name=preset_name,
                event_type="preset_override_updated",
                actor=actor,
                payload={
                    "fields_changed": sorted(updates.keys()),
                },
            )
        return merged

    async def delete_override(
        self,
        preset_name: NotBlankStr,
        *,
        actor: ProviderAuditActor,
    ) -> bool:
        """Drop the override for ``preset_name``.

        Returns:
            ``True`` if a row was removed; ``False`` if no override
            existed (still emits an audit row to record intent).
        """
        removed = await self._repo.delete(preset_name)
        if self._audit_service is not None:
            await self._audit_service.record(
                provider_name=preset_name,
                event_type="preset_override_updated",
                actor=actor,
                payload={"action": "deleted", "removed": removed},
            )
        return removed

    def _build_merged(
        self,
        preset_name: NotBlankStr,
        existing: PresetOverride | None,
        updates: dict[str, object],
        actor: ProviderAuditActor,
    ) -> PresetOverride:
        """Merge ``updates`` onto ``existing`` (or a blank base).

        The merged dict carries mixed-type values (datetime, list,
        ``None``, str) so the local type is ``dict[str, Any]``;
        ``PresetOverride.model_validate`` enforces the per-field
        contract.

        Returns:
            The validated ``PresetOverride`` built from the merged base
            and updates.
        """
        from typing import Any  # noqa: PLC0415

        base: dict[str, Any] = (
            existing.model_dump()
            if existing is not None
            else {
                "preset_name": preset_name,
                "default_models": None,
                "supported_auth_types": None,
                "candidate_urls": None,
                "base_url": None,
            }
        )
        merged: dict[str, Any] = {
            **base,
            **updates,
            "updated_at": datetime.now(UTC),
            "updated_by": actor.id,
        }
        return PresetOverride.model_validate(merged)

    @staticmethod
    def _validate_against_preset(
        preset: CloudPreset | LocalPreset,
        override: PresetOverride,
    ) -> None:
        """Reject overrides that clash with the preset's kind.

        Raises:
            ProviderValidationError: If ``candidate_urls`` is set on a
                cloud-preset override, or ``base_url`` is set on a
                local-preset override.
        """
        is_cloud = isinstance(preset, CloudPreset)
        if is_cloud and override.candidate_urls is not None:
            msg = (
                f"Preset {override.preset_name!r} is a cloud preset; "
                "candidate_urls overrides are illegal"
            )
            raise ProviderValidationError(msg)
        if not is_cloud and override.base_url is not None:
            msg = (
                f"Preset {override.preset_name!r} is a local preset; "
                "base_url overrides are illegal"
            )
            raise ProviderValidationError(msg)
