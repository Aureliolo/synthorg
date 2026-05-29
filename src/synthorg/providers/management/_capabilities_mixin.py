"""Mixin for the six new provider capability mutations.

Splits the ``ProviderManagementService`` body so the file owning the
core CRUD logic stays under the project's 800-line ceiling.  The
mixin reads ``self._lock`` / ``self._config_resolver`` / etc.
provided by the host service; ``TYPE_CHECKING`` annotations narrow
the contract for mypy strict.
"""

import asyncio
from typing import TYPE_CHECKING, Protocol

from synthorg.config.schema import (
    ProviderConfig,
    ProviderModelConfig,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_ALREADY_EXISTS,
    PROVIDER_AUDIT_WRITE_FAILED,
    PROVIDER_CREDENTIALS_ROTATED,
    PROVIDER_MODEL_ADDED,
    PROVIDER_MODELS_SYNCED,
    PROVIDER_NOT_FOUND,
    PROVIDER_RATE_LIMITS_UPDATED,
    PROVIDER_VALIDATION_FAILED,
)
from synthorg.providers.errors import (
    ProviderAlreadyExistsError,
    ProviderNotFoundError,
    ProviderValidationError,
)
from synthorg.providers.management._capability_helpers import (
    SYSTEM_ACTOR,
    credentials_update_fields,
)
from synthorg.providers.management.capability_dtos import (
    AddModelRequest,
    CredentialsRotateRequest,
    ProviderAuditActor,
    ProviderAuditEventType,
    RateLimitsResponse,
    RateLimitsUpdateRequest,
    SyncModelsRequest,
    SyncModelsResponse,
)

if TYPE_CHECKING:
    from synthorg.providers.management.audit_service import ProviderAuditService
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


def _reject_destructive_empty_discovery(
    *,
    name: str,
    request: SyncModelsRequest,
    discovered: tuple[ProviderModelConfig, ...],
    pre_discover: ProviderConfig,
) -> None:
    """Refuse a replace-mode sync that would wipe every persisted model.

    Invariant: when ``replace_existing=True`` and discovery returns
    no models while persisted models exist, refuse the sync so a
    transient 404 / timeout / wrong-URL outcome cannot delete every
    persisted model. ``replace_existing=False`` (append-only) is the
    safe path for operators to retry while debugging the discovery
    endpoint, since it adds nothing when discovery is empty.

    Raises:
        ProviderValidationError: If ``replace_existing=True``, discovery
            returned zero models, and at least one persisted model
            exists (destructive-wipe guard).
    """
    if not request.replace_existing or discovered:
        return
    existing_count = len(pre_discover.models)
    if not existing_count:
        return
    msg = (
        f"Sync would delete all {existing_count} persisted "
        f"model(s) for provider {name!r} because discovery "
        f"returned no models; refusing destructive replace. "
        f"Re-check the provider URL or retry with "
        f"``replace_existing=false``."
    )
    logger.warning(
        PROVIDER_VALIDATION_FAILED,
        provider=name,
        error=msg,
        discovered_count=0,
        existing_count=existing_count,
        replace_existing=True,
    )
    raise ProviderValidationError(msg)


# Narrows the mixin's self-type to the 3 attrs + 3 methods consumed;
# host: ProviderManagementService (composed via MRO in service.py).
class _ServiceProtocol(Protocol):
    """Subset of ``ProviderManagementService`` accessed by the mixin.

    Declared as a typing ``Protocol`` so mypy strict can verify the
    mixin is composed onto a host class providing these attributes
    and methods, without importing the concrete service (avoiding a
    circular import with ``service.py``).
    """

    _lock: asyncio.Lock
    _config_resolver: ConfigResolver
    _audit_service: ProviderAuditService | None

    async def get_provider(self, name: str) -> ProviderConfig:
        """Load a provider by name (provided by the host service)."""
        ...

    async def discover_models_for_provider(
        self,
        name: str,
        *,
        preset_hint: str | None = None,
    ) -> tuple[ProviderModelConfig, ...]:
        """Discover models for a provider (provided by the host service)."""
        ...

    async def _validate_and_persist(self, providers: dict[str, ProviderConfig]) -> None:
        """Validate + persist + hot-reload providers (provided by host)."""
        ...


class ProviderCapabilitiesMixin:
    """Mutations for audit / rate-limits / credentials rotate / model add+sync.

    Composed into ``ProviderManagementService`` via plain Python MRO.
    The host class supplies ``_lock``, ``_config_resolver``,
    ``_audit_service``, and the helper methods declared on
    ``_ServiceProtocol``.
    """

    async def _audit(
        self: _ServiceProtocol,
        *,
        provider_name: str,
        event_type: ProviderAuditEventType,
        actor: ProviderAuditActor | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        """Emit one provider audit event if the audit service is wired.

        No-op when ``self._audit_service is None`` so legacy bootstrap
        paths keep working unchanged.  Audit failures NEVER propagate
        out of a mutation: the mutation has already succeeded by the
        time we reach here, and a downstream audit row failing must
        not roll back the persisted provider change.  Failures are
        logged at WARNING and swallowed (excluding the sentinel
        ``MemoryError`` / ``RecursionError`` pair that always
        propagates per CLAUDE.md async conventions).
        """
        if self._audit_service is None:
            return
        try:
            await self._audit_service.record(
                provider_name=provider_name,
                event_type=event_type,
                actor=actor or SYSTEM_ACTOR,
                payload=dict(payload) if payload else {},
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PROVIDER_AUDIT_WRITE_FAILED,
                provider=provider_name,
                event_type=event_type,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def add_model(
        self: _ServiceProtocol,
        name: str,
        request: AddModelRequest,
        *,
        actor: ProviderAuditActor | None = None,
    ) -> ProviderConfig:
        """Add a single ``ProviderModelConfig`` to the persisted list.

        Conflict (model with the same id already exists) raises
        ``ProviderAlreadyExistsError`` so the controller can surface
        HTTP 409.

        Returns:
            The updated ``ProviderConfig`` with the new model appended.

        Raises:
            ProviderNotFoundError: If the named provider does not exist.
            ProviderAlreadyExistsError: If a model with the same id
                already exists on the provider.
        """
        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            existing = providers.get(name)
            if existing is None:
                msg = f"Provider {name!r} not found"
                logger.warning(PROVIDER_NOT_FOUND, provider=name, error=msg)
                raise ProviderNotFoundError(msg)

            new_model = request.model
            if any(m.id == new_model.id for m in existing.models):
                msg = f"Model {new_model.id!r} already exists on provider {name!r}"
                logger.warning(
                    PROVIDER_ALREADY_EXISTS,
                    provider=name,
                    model=new_model.id,
                    error=msg,
                )
                raise ProviderAlreadyExistsError(msg)

            updated = existing.model_copy(
                update={"models": (*existing.models, new_model)},
            )
            new_providers = {**providers, name: updated}
            await self._validate_and_persist(new_providers)

        logger.info(
            PROVIDER_MODEL_ADDED,
            provider=name,
            model=new_model.id,
            alias=new_model.alias,
        )
        # Audit AFTER the mutation is durably persisted and the lock
        # is released; audit-row I/O must not extend the critical
        # section for every concurrent provider mutation.  Mirrors
        # the pattern in ``sync_models``.
        await self._audit(  # type: ignore[attr-defined]
            provider_name=name,
            event_type="model_added",
            actor=actor,
            payload={"model_id": new_model.id, "alias": new_model.alias},
        )
        return updated

    async def sync_models(
        self: _ServiceProtocol,
        name: str,
        request: SyncModelsRequest,
        *,
        actor: ProviderAuditActor | None = None,
    ) -> SyncModelsResponse:
        """Re-run discovery + pricing enrichment and merge with persisted.

        ``replace_existing=True`` (the default) replaces the persisted
        list entirely with the merged discovered+enriched set.
        ``replace_existing=False`` keeps existing models verbatim and
        only appends models the discovery surfaced as new.

        Returns:
            A ``SyncModelsResponse`` describing the added, removed, and
            updated model ids plus the full new persisted model list.

        Raises:
            ProviderNotFoundError: If the named provider does not exist
                (or was deleted during discovery).
            ProviderValidationError: If discovery returned empty for a
                replace-mode sync that would wipe all persisted models,
                or the provider config changed between discovery and
                persistence.
        """
        # Snapshot the provider config that drove discovery (base_url
        # / auth_type / preset_name) so we can detect a concurrent
        # mutation that swapped the endpoint or credentials between
        # the discovery call and the persistence write.
        pre_discover = await self.get_provider(name)

        # Discovery is idempotent and pure-read; run it outside the
        # lock so concurrent provider mutations are not blocked
        # behind potentially-slow upstream HTTP calls.  Forward the
        # caller-supplied ``preset_hint`` so preset-guided discovery
        # paths (Ollama vs standard ``/models``) work as documented.
        discovered = await self.discover_models_for_provider(
            name,
            preset_hint=request.preset_hint,
        )

        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            current = providers.get(name)
            if current is None:
                # Race: provider deleted between the discover read and
                # the lock acquisition.
                msg = f"Provider {name!r} not found"
                logger.warning(PROVIDER_NOT_FOUND, provider=name, error=msg)
                raise ProviderNotFoundError(msg)

            # Re-check the destructive-empty guard against the
            # post-lock snapshot; a concurrent ``add_model()`` that
            # lands between the pre-lock ``pre_discover`` read and
            # ``self._lock`` acquisition can flip the persisted set
            # from empty (guard returns early) to non-empty (guard
            # MUST refuse the wipe). Using ``current`` closes that
            # window.
            _reject_destructive_empty_discovery(
                name=name,
                request=request,
                discovered=discovered,
                pre_discover=current,
            )

            # If the discovery target was swapped under us (different
            # base_url / auth_type / preset) we must NOT persist the
            # discovered set onto the new endpoint config -- it would
            # be stale data from a different upstream.  Reject with
            # ProviderValidationError so the operator can re-issue
            # the sync; HTTP 422 surfaces this on the controller.
            if (
                current.base_url != pre_discover.base_url
                or current.auth_type != pre_discover.auth_type
                or current.preset_name != pre_discover.preset_name
            ):
                msg = (
                    f"Provider {name!r} configuration changed during "
                    f"discovery; aborting sync to avoid persisting "
                    f"stale upstream data"
                )
                logger.warning(
                    PROVIDER_VALIDATION_FAILED,
                    provider=name,
                    error=msg,
                )
                raise ProviderValidationError(msg)

            # Re-derive the diff against ``current`` (the post-lock
            # snapshot) rather than the pre-lock ``existing`` we
            # peeked at earlier.  Otherwise a concurrent
            # ``add_model()`` or manual edit that lands between
            # discovery and lock acquisition would be silently
            # clobbered by the merge -- exactly the lost-update race
            # the lock is meant to prevent.
            prev_by_id = {m.id: m for m in current.models}
            disc_by_id = {m.id: m for m in discovered}

            added: list[str] = []
            removed: list[str] = []
            updated: list[str] = []
            new_models: list[ProviderModelConfig] = []
            if request.replace_existing:
                for m in discovered:
                    if m.id not in prev_by_id:
                        added.append(m.id)
                    elif prev_by_id[m.id].model_dump() != m.model_dump():
                        updated.append(m.id)
                    new_models.append(m)
                removed.extend(
                    prev_id for prev_id in prev_by_id if prev_id not in disc_by_id
                )
            else:
                new_models.extend(current.models)
                for m in discovered:
                    if m.id not in prev_by_id:
                        new_models.append(m)
                        added.append(m.id)

            updated_config = current.model_copy(
                update={"models": tuple(new_models)},
            )
            new_providers = {**providers, name: updated_config}
            await self._validate_and_persist(new_providers)

        logger.info(
            PROVIDER_MODELS_SYNCED,
            provider=name,
            added_count=len(added),
            removed_count=len(removed),
            updated_count=len(updated),
            replace_existing=request.replace_existing,
        )
        await self._audit(  # type: ignore[attr-defined]
            provider_name=name,
            event_type="models_synced",
            actor=actor,
            payload={
                "added_count": len(added),
                "removed_count": len(removed),
                "updated_count": len(updated),
                "replace_existing": request.replace_existing,
            },
        )

        return SyncModelsResponse(
            added=tuple(sorted(added)),
            removed=tuple(sorted(removed)),
            updated=tuple(sorted(updated)),
            models=tuple(new_models),
        )

    async def rotate_credentials(
        self: _ServiceProtocol,
        name: str,
        request: CredentialsRotateRequest,
        *,
        actor: ProviderAuditActor | None = None,
    ) -> ProviderConfig:
        """Rotate the secret credentials on an existing provider.

        Validates the request variant matches the provider's persisted
        ``auth_type``, applies the new secret, and hot-reloads the
        registry. The audit row carries ONLY the masked credential
        prefix; plaintext is never persisted to the audit log.

        Returns:
            The updated ``ProviderConfig`` with the new credentials
            applied.

        Raises:
            ProviderNotFoundError: If the named provider does not exist.
            ProviderValidationError: If the request's ``auth_type`` does
                not match the persisted one, or a subscription rotation
                lacks ``tos_accepted=true``.
        """
        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            existing = providers.get(name)
            if existing is None:
                msg = f"Provider {name!r} not found"
                logger.warning(PROVIDER_NOT_FOUND, provider=name, error=msg)
                raise ProviderNotFoundError(msg)
            if existing.auth_type != request.auth_type:
                msg = (
                    f"Provider {name!r} has auth_type {existing.auth_type.value!r}; "
                    f"rotation payload is {request.auth_type.value!r}"
                )
                logger.warning(
                    PROVIDER_VALIDATION_FAILED,
                    provider=name,
                    error=msg,
                )
                raise ProviderValidationError(msg)

            update_fields, masked_secret = credentials_update_fields(request)
            updated = existing.model_copy(update=update_fields)
            new_providers = {**providers, name: updated}
            await self._validate_and_persist(new_providers)

        logger.info(
            PROVIDER_CREDENTIALS_ROTATED,
            provider=name,
            auth_type=existing.auth_type.value,
        )
        # Audit out of the critical section: rotation has already
        # been persisted and hot-reloaded by the time we get here;
        # the audit row must not extend lock contention.
        await self._audit(  # type: ignore[attr-defined]
            provider_name=name,
            event_type="provider_credentials_rotated",
            actor=actor,
            payload={
                "auth_type": existing.auth_type.value,
                "masked_secret": masked_secret,
            },
        )
        return updated

    async def get_rate_limits(self: _ServiceProtocol, name: str) -> RateLimitsResponse:
        """Read the persisted rate-limit configuration for one provider.

        Returns an envelope where ``0`` means unlimited per the
        existing ``RateLimiterConfig`` semantics.

        Returns:
            A ``RateLimitsResponse`` with the provider's persisted RPM
            and concurrency caps (``0`` means unlimited).
        """
        config = await self.get_provider(name)
        rl = config.rate_limiter
        return RateLimitsResponse(
            requests_per_minute=rl.max_requests_per_minute,
            concurrent_requests=rl.max_concurrent,
        )

    async def update_rate_limits(
        self: _ServiceProtocol,
        name: str,
        request: RateLimitsUpdateRequest,
        *,
        actor: ProviderAuditActor | None = None,
    ) -> RateLimitsResponse:
        """Apply a partial update to a provider's rate-limit config.

        Reads the current ``RateLimiterConfig``, merges the explicit
        fields from ``request`` (``model_dump(exclude_unset=True)``),
        validates, persists, hot-reloads the ProviderRegistry, audits.

        Returns:
            A ``RateLimitsResponse`` reflecting the new effective RPM and
            concurrency caps after the partial update.

        Raises:
            ProviderNotFoundError: If the named provider does not exist.
        """
        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            existing = providers.get(name)
            if existing is None:
                msg = f"Provider {name!r} not found"
                logger.warning(PROVIDER_NOT_FOUND, provider=name, error=msg)
                raise ProviderNotFoundError(msg)

            updates = request.model_dump(exclude_unset=True)
            current = existing.rate_limiter
            new_rl = current.model_copy(
                update={
                    **(
                        {"max_requests_per_minute": updates["requests_per_minute"]}
                        if "requests_per_minute" in updates
                        else {}
                    ),
                    **(
                        {"max_concurrent": updates["concurrent_requests"]}
                        if "concurrent_requests" in updates
                        else {}
                    ),
                },
            )
            updated = existing.model_copy(update={"rate_limiter": new_rl})
            new_providers = {**providers, name: updated}
            await self._validate_and_persist(new_providers)

        logger.info(
            PROVIDER_RATE_LIMITS_UPDATED,
            provider=name,
            fields_changed=sorted(updates.keys()),
        )
        # Audit out of the lock for the same reason as
        # ``rotate_credentials`` and ``add_model``: the change is
        # durably persisted by here, and audit-row I/O should not
        # extend the critical section.
        await self._audit(  # type: ignore[attr-defined]
            provider_name=name,
            event_type="provider_rate_limits_updated",
            actor=actor,
            payload={
                "fields_changed": sorted(updates.keys()),
                **updates,
            },
        )
        return RateLimitsResponse(
            requests_per_minute=new_rl.max_requests_per_minute,
            concurrent_requests=new_rl.max_concurrent,
        )
