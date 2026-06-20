# module-kind: complex_service
"""Provider management service -- runtime CRUD for LLM providers.

Orchestrates config validation, persistence via SettingsService,
and hot-reload of ProviderRegistry + ModelRouter in AppState.

One cohesive responsibility: manage the provider catalog. CRUD,
preset bootstrap, connection probing, model discovery, local-model
lifecycle, and discovery-allowlist management all mutate or read the
SAME catalog blob through the SAME asyncio lock + the SAME
``_validate_and_persist`` (validate + persist + hot-reload) pipeline.
Splitting introduces a coordination cost without an architectural
win because every write path must round-trip through that pipeline.
The capabilities mixin already extracts the six additional mutation
entry points (rate-limits, presets, credentials, manual model add,
bulk model sync) so the residual surface is the cohesive catalog
core.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping

from pydantic import JsonValue

from synthorg.api.state import AppState
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import (
    LocalModelParams,
    ProviderConfig,
    ProviderModelConfig,
    RootConfig,
)
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.state import provider_credential_catalog_of
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.provider import (
    PROVIDER_ALREADY_EXISTS,
    PROVIDER_CONNECTION_TESTED,
    PROVIDER_DISCOVERY_FAILED,
    PROVIDER_DISCOVERY_SELF_CONNECTION_BLOCKED,
    PROVIDER_LOCAL_MANAGER_NOT_AVAILABLE,
    PROVIDER_MODEL_CONFIG_UPDATED,
    PROVIDER_NOT_FOUND,
    PROVIDER_VALIDATION_FAILED,
)
from synthorg.observability.events.security import (
    SECURITY_PROVIDER_CREATED,
    SECURITY_PROVIDER_DELETED,
    SECURITY_PROVIDER_UPDATED,
)
from synthorg.providers._auth_type_descriptor import AUTH_TYPE_DESCRIPTORS
from synthorg.providers.discovery import discover_models
from synthorg.providers.discovery_policy import (
    ProviderDiscoveryPolicy,
    is_url_allowed,
)
from synthorg.providers.enums import AuthType, MessageRole
from synthorg.providers.errors import (
    ProviderAlreadyExistsError,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderNotFoundError,
    ProviderValidationError,
)
from synthorg.providers.management._capabilities_mixin import (
    ProviderCapabilitiesMixin,
)
from synthorg.providers.management._credential_helpers import (
    apply_update_with_credential,
    delete_provider_credential,
    resolve_provider_api_key,
    rollback_credential,
    store_provider_api_key,
)
from synthorg.providers.management._helpers import (
    apply_update,
    build_discovery_headers,
    build_provider_config,
    infer_preset_hint,
    models_from_litellm,
    serialize_providers,
)
from synthorg.providers.management.allowlist import DiscoveryAllowlistManager
from synthorg.providers.management.audit_service import ProviderAuditService
from synthorg.providers.management.dtos import (
    CreateFromPresetRequest,
    CreateProviderRequest,
    TestConnectionRequest,
    TestConnectionResponse,
    UpdateProviderRequest,
)
from synthorg.providers.management.local_models import (
    LocalModelManager,
    PullProgressEvent,
)
from synthorg.providers.models import ChatMessage
from synthorg.providers.presets import (
    CloudPreset,
    LocalPreset,
    default_models_for,
    get_preset,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.router import ModelRouter
from synthorg.providers.routing.selector import ModelCandidateSelector
from synthorg.providers.url_utils import is_self_url, redact_url
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

# Provider fields that carry credential / secret material.  Their
# values must never appear in audit-row payloads: the audit table is
# operator-readable and is included in data backups.  Diffs against
# these fields collapse to a sentinel that preserves the
# "this field changed" signal without exposing either value.
#
# This list must stay synchronised with every credential-bearing
# field on ``ProviderConfig``.  Adding a new credential field without
# extending this set leaks it on the next ``update_provider`` audit
# entry.  ``_assert_sensitive_fields_complete`` runs at import time
# to fail fast when ``ProviderConfig`` grows a credential field that
# is not whitelisted here.
_SENSITIVE_PROVIDER_FIELDS: frozenset[str] = frozenset(
    {
        "subscription_token",
        "oauth_client_secret",
        "custom_header_value",
    },
)


def _assert_sensitive_fields_complete() -> None:
    """Fail-fast guard against silent credential leakage in audit diffs.

    A new ``ProviderConfig`` field whose name encodes a credential
    (matches ``*_key`` / ``*_token`` / ``*_secret`` / contains
    ``password``) must be added to ``_SENSITIVE_PROVIDER_FIELDS``.
    Catching the omission here, at import time, prevents the field
    from ever reaching an audit row in production.

    Raises:
        RuntimeError: If a credential-named ``ProviderConfig`` field
            (``*_key`` / ``*_token`` / ``*_secret`` / contains
            ``password``) is missing from ``_SENSITIVE_PROVIDER_FIELDS``.
    """
    credential_suffixes = ("_key", "_token", "_secret", "_password")
    suspected = {
        name
        for name in ProviderConfig.model_fields
        if name.endswith(credential_suffixes) or "password" in name.lower()
    }
    leaks = suspected - _SENSITIVE_PROVIDER_FIELDS
    if leaks:
        msg = (
            "ProviderConfig fields look credential-bearing but are not "
            f"redacted in audit diffs: {sorted(leaks)!r}. Add them to "
            "synthorg.providers.management.service._SENSITIVE_PROVIDER_FIELDS."
        )
        raise RuntimeError(msg)


_assert_sensitive_fields_complete()


def _diff_provider_update(
    existing: ProviderConfig,
    updated: ProviderConfig,
) -> dict[str, JsonValue]:
    """Build an audit payload listing only fields whose value changed.

    The EDIT form on the frontend re-sends every field on every submit,
    so ``request.model_dump(exclude_unset=True)`` would mark every
    field as "changed" even when the user only touched ``base_url``.
    Comparing the persisted ``existing`` config against the post-merge
    ``updated`` config produces the operator-meaningful diff: each
    changed field gets an ``{"old": ..., "new": ...}`` entry, sensitive
    fields collapse to ``"<redacted>"`` sentinels, and the legacy
    ``fields_changed`` list is kept for downstream audit-log
    consumers that already filter on it.

    Returns:
        An audit payload with a ``"fields_changed"`` list and a
        ``"diff"`` sub-dict of ``{field: {"old": ..., "new": ...}}``,
        with sensitive fields collapsed to ``"<redacted>"``.
    """
    before = existing.model_dump(mode="json")
    after = updated.model_dump(mode="json")
    changes: dict[str, JsonValue] = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) == after.get(key):
            continue
        entry: dict[str, JsonValue] = (
            {"old": "<redacted>", "new": "<redacted>"}
            if key in _SENSITIVE_PROVIDER_FIELDS
            else {"old": before.get(key), "new": after.get(key)}
        )
        changes[key] = entry
    return {
        "fields_changed": [*changes],
        "diff": changes,
    }


def _safe_task_id_segment(value: str) -> str:
    """Strip control / whitespace / colon characters from a task-id segment.

    The probe ``task_id`` is built from a user-supplied provider name
    embedded in a colon-delimited template
    (``system:providers:test_connection:{name}``). ``NotBlankStr``
    rejects empty input but permits control characters (newlines, NUL,
    vertical tab, ...) AND colons, both of which would corrupt
    downstream log parsers and task-id segment splitters that rely on
    ``:`` as the canonical separator. Unicode is preserved -- only the
    C0/C1 control range, ASCII delete, whitespace, and ``:`` itself
    get replaced with ``_``. Returns ``"_"`` if every character was
    filtered (preserves ``NotBlankStr``).

    Returns:
        The sanitised segment with control/whitespace/colon characters
        replaced by ``_`` (``"_"`` if every character was filtered).
    """
    cleaned = "".join(
        ch if ch.isprintable() and not ch.isspace() and ch != ":" else "_"
        for ch in value
    )
    return cleaned or "_"


class ProviderManagementService(ProviderCapabilitiesMixin):
    """Runtime CRUD service for LLM providers.

    All mutating operations are serialized under an asyncio lock
    to prevent read-modify-write races on the provider config blob.

    Args:
        settings_service: Settings persistence layer.
        config_resolver: Typed config accessor.
        app_state: Application state for hot-reload swaps.
        config: Root company configuration.
        audit_service: Optional provider mutation audit log writer.
            ``None`` when the persistence backend has not been wired
            (legacy bootstrap paths, in-memory test rigs); each
            mutation entry point is a no-op for audit emission in that
            case.
        cost_tracker: Optional cost tracker. When wired, the connection
            probe records a ``CostRecord`` for the paid completion it
            sends. ``None`` makes the probe scope a no-op (the cost
            chokepoint reads ``None`` from the context and skips
            recording).
    """

    def __init__(  # noqa: PLR0913 -- explicit DI; all kw-only and optional after the 4th arg
        self,
        *,
        settings_service: SettingsService,
        config_resolver: ConfigResolver,
        app_state: AppState,
        config: RootConfig,
        audit_service: ProviderAuditService | None = None,
        cost_tracker: CostTracker | None = None,
        clock: Clock | None = None,
    ) -> None:
        from synthorg.settings.bootstrap_resolver import (  # noqa: PLC0415
            resolve_init_value,
        )
        from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415

        self._settings_service = settings_service
        self._config_resolver = config_resolver
        self._app_state = app_state
        self._config = config
        self._audit_service = audit_service
        self._cost_tracker = cost_tracker
        self._clock: Clock = clock if clock is not None else SystemClock()
        # api.server_port is read_only_post_init; the resolved value
        # is stable for the process lifetime so we cache it once.
        self._backend_port = int(
            resolve_init_value(
                SettingNamespace.API,
                "server_port",
                parse=int,
            ).value
        )
        self._lock = asyncio.Lock()
        self._allowlist = DiscoveryAllowlistManager(
            settings_service=settings_service,
            config_resolver=config_resolver,
        )

    async def list_providers(self) -> Mapping[str, ProviderConfig]:
        """List all configured providers keyed by name.

        Returns an immutable :class:`types.MappingProxyType` view;
        build a fresh dict with ``{**providers, name: config}`` to
        apply updates.

        Returns:
            An immutable ``MappingProxyType`` of all configured providers
            keyed by name.
        """
        return await self._config_resolver.get_provider_configs()

    async def get_provider(self, name: str) -> ProviderConfig:
        """Get a single provider by name.

        Returns:
            The ``ProviderConfig`` for the named provider.

        Raises:
            ProviderNotFoundError: If the provider does not exist.
        """
        providers = await self._config_resolver.get_provider_configs()
        config = providers.get(name)
        if config is None:
            msg = f"Provider {name!r} not found"
            logger.warning(PROVIDER_NOT_FOUND, provider=name, error=msg)
            raise ProviderNotFoundError(msg)
        return config

    async def create_provider(
        self,
        request: CreateProviderRequest,
    ) -> ProviderConfig:
        """Create a new provider.

        Returns:
            The newly created and persisted ``ProviderConfig``.

        Raises:
            ProviderAlreadyExistsError: If name is taken.
            ProviderValidationError: If config fails validation.
        """
        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            if request.name in providers:
                msg = f"Provider {request.name!r} already exists"
                logger.warning(
                    PROVIDER_ALREADY_EXISTS,
                    provider=request.name,
                    error=msg,
                )
                raise ProviderAlreadyExistsError(msg)

            # Catalog-only credentials: an api_key supplied at the boundary is
            # minted into a ConnectionCatalog connection FIRST, then threaded
            # into the config as connection_name -- API_KEY auth mandates it,
            # so the config could not validate with the secret embedded or
            # absent. The secret is never persisted on the ProviderConfig.
            mints_api_key = AUTH_TYPE_DESCRIPTORS[request.auth_type].supports_api_key
            conn_name: str | None = None
            if mints_api_key and request.api_key is not None:
                conn_name = await store_provider_api_key(
                    self._app_state,
                    request.name,
                    request.api_key.get_secret_value(),
                )
            try:
                # Config construction stays inside the try: a validation
                # failure here must also unwind the catalog mint above,
                # else the secret is left orphaned with no owning provider.
                new_config = build_provider_config(request, connection_name=conn_name)
                new_providers = {**providers, request.name: new_config}
                await self._validate_and_persist(new_providers)
            except Exception:
                # Pre-persist failure (build / validate / persist): nothing is
                # durably stored, so drop the minted secret to avoid an
                # orphaned connection with no owning provider.
                if conn_name is not None:
                    await delete_provider_credential(self._app_state, request.name)
                raise
            try:
                await self._allowlist.update_for_create(new_config)
            except Exception:
                # Post-persist failure: the config (referencing conn_name) is
                # already stored, so roll it back to the pre-create snapshot
                # BEFORE dropping the secret -- otherwise the persisted config
                # would point at a deleted credential.
                await self._restore_providers(providers)
                if conn_name is not None:
                    await delete_provider_credential(self._app_state, request.name)
                raise

            logger.info(
                SECURITY_PROVIDER_CREATED,
                provider=request.name,
                driver=new_config.driver,
                auth_type=new_config.auth_type,
            )
            await self._audit(
                provider_name=request.name,
                event_type="provider_created",
                payload={
                    "driver": new_config.driver,
                    "auth_type": new_config.auth_type.value,
                    "model_count": len(new_config.models),
                },
            )
            return new_config

    async def update_provider(
        self,
        name: str,
        request: UpdateProviderRequest,
    ) -> ProviderConfig:
        """Update an existing provider.

        Returns:
            The updated and persisted ``ProviderConfig`` after merging
            the request fields.

        Raises:
            ProviderNotFoundError: If the provider does not exist.
            ProviderValidationError: If the update fails validation.
        """
        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            existing = providers.get(name)
            if existing is None:
                msg = f"Provider {name!r} not found"
                logger.warning(PROVIDER_NOT_FOUND, provider=name, error=msg)
                raise ProviderNotFoundError(msg)

            # ``apply_update_with_credential`` mutates the catalog in both
            # directions: it mints/replaces the secret when an api_key is
            # supplied, and DELETES the backing connection when the update
            # clears the key or switches to an auth type that has none.
            # Snapshot the prior secret before any of those so a failed
            # persist / allowlist step restores it (see rollback_credential).
            final_auth_type = (
                request.auth_type
                if request.auth_type is not None
                else existing.auth_type
            )
            supports_api_key = AUTH_TYPE_DESCRIPTORS[final_auth_type].supports_api_key
            credential_mutated = (
                supports_api_key
                and (request.api_key is not None or request.clear_api_key)
            ) or (not supports_api_key and existing.connection_name is not None)
            prior_api_key: str | None = (
                await resolve_provider_api_key(self._app_state, existing)
                if credential_mutated
                else None
            )
            try:
                updated = await apply_update_with_credential(
                    self._app_state, name, existing, request
                )
                new_providers = {**providers, name: updated}
                await self._validate_and_persist(new_providers)
            except Exception:
                await rollback_credential(
                    self._app_state, name, prior_api_key, mutated=credential_mutated
                )
                raise
            try:
                await self._allowlist.update_for_update(
                    existing,
                    updated,
                    new_providers,
                )
            except Exception:
                await self._restore_providers(providers)
                await rollback_credential(
                    self._app_state, name, prior_api_key, mutated=credential_mutated
                )
                raise

            logger.info(
                SECURITY_PROVIDER_UPDATED,
                provider=name,
                driver=updated.driver,
                auth_type=updated.auth_type,
            )
            await self._audit(
                provider_name=name,
                event_type="provider_updated",
                payload=_diff_provider_update(existing, updated),
            )
            return updated

    async def delete_provider(
        self,
        name: str,
    ) -> None:
        """Delete a provider.

        Args:
            name: Provider name to delete.

        Raises:
            ProviderNotFoundError: If the provider does not exist.
        """
        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            if name not in providers:
                msg = f"Provider {name!r} not found"
                logger.warning(PROVIDER_NOT_FOUND, provider=name, error=msg)
                raise ProviderNotFoundError(msg)

            removed_config = providers[name]
            new_providers = {k: v for k, v in providers.items() if k != name}
            await self._validate_and_persist(new_providers)
            # Remove the catalog connection minted for this provider's
            # credential so a deleted provider leaves no orphaned secret.
            await delete_provider_credential(self._app_state, name)
            await self._allowlist.update_for_delete(
                removed_config,
                new_providers,
            )

            logger.info(SECURITY_PROVIDER_DELETED, provider=name)
            await self._audit(
                provider_name=name,
                event_type="provider_deleted",
                payload={
                    "driver": removed_config.driver,
                    "auth_type": removed_config.auth_type.value,
                    "model_count": len(removed_config.models),
                },
            )

    async def test_connection(
        self,
        name: str,
        request: TestConnectionRequest,
    ) -> TestConnectionResponse:
        """Test connectivity to a provider.

        Returns:
            A ``TestConnectionResponse`` with the probe outcome (success,
            latency, model tested, and any error message).

        Raises:
            ProviderNotFoundError: If the provider does not exist.
        """
        providers = await self._config_resolver.get_provider_configs()
        config = providers.get(name)
        if config is None:
            msg = f"Provider {name!r} not found"
            logger.warning(PROVIDER_NOT_FOUND, provider=name, error=msg)
            raise ProviderNotFoundError(msg)

        if not config.models:
            return TestConnectionResponse(
                success=False,
                error="Provider has no models configured",
            )

        model_id = request.model or config.models[0].id
        return await self._do_test_connection(name, config, model_id)

    async def _do_test_connection(
        self,
        name: str,
        config: ProviderConfig,
        model_id: str,
    ) -> TestConnectionResponse:
        """Execute the actual connection test probe.

        Returns:
            A ``TestConnectionResponse`` reflecting the probe outcome
            (success with latency, or failure with an error message).

        Raises:
            asyncio.CancelledError: Propagated immediately if the task is
                cancelled during the probe.
        """
        from synthorg.providers.resilience.errors import (  # noqa: PLC0415
            RetryExhaustedError,
        )

        try:
            return await self._probe_provider(name, config, model_id)
        except RetryExhaustedError as exc:
            # ``RetryExhaustedError`` is a ``ProviderError`` subtype but
            # carries different operational meaning: every retry tier
            # was exhausted, the provider isn't reachable, and the
            # retry-handler signal is the actionable diagnostic.
            # Logging it separately preserves that signal -- otherwise
            # operators see "connection failed" without knowing whether
            # the upstream timed out once or N times.
            logger.warning(
                PROVIDER_CONNECTION_TESTED,
                provider=name,
                model=model_id,
                success=False,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                retry_exhausted=True,
            )
            return TestConnectionResponse(
                success=False,
                error=safe_error_description(exc),
                model_tested=model_id,
            )
        except ProviderError as exc:
            logger.warning(
                PROVIDER_CONNECTION_TESTED,
                provider=name,
                model=model_id,
                success=False,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # The response error is consumed by the /providers/test
            # endpoint and surfaced to the dashboard; scrub it so
            # the API key embedded in HTTPStatusError messages does
            # not round-trip back over HTTP.
            return TestConnectionResponse(
                success=False,
                error=safe_error_description(exc),
                model_tested=model_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                PROVIDER_CONNECTION_TESTED,
                exc,
                provider=name,
                model=model_id,
                success=False,
            )
            return TestConnectionResponse(
                success=False,
                error=f"Connection test failed: {type(exc).__name__}",
                model_tested=model_id,
            )

    async def _probe_provider(
        self,
        name: str,
        config: ProviderConfig,
        model_id: str,
    ) -> TestConnectionResponse:
        """Send a minimal completion request to verify connectivity.

        Returns:
            A ``TestConnectionResponse`` with the probe result (success,
            latency, model tested, and any error message).
        """
        from synthorg.providers.cost_recording import (  # noqa: PLC0415
            cost_recording_scope,
        )
        from synthorg.providers.drivers.litellm_driver import (  # noqa: PLC0415
            LiteLLMDriver,
        )

        driver = LiteLLMDriver(name, config)
        # Bind the always-on credential catalog so a connection_name-backed
        # provider resolves its credentials during the probe, exactly as it
        # will at runtime. Embedded fields still work when no catalog is wired.
        driver.bind_credential_catalog(provider_credential_catalog_of(self._app_state))
        messages = [ChatMessage(role=MessageRole.USER, content="ping")]
        start = self._clock.monotonic()
        # Probes hit a real provider and are billed; route through the
        # cost-recording chokepoint so the spend appears in the same
        # accounting surface as production calls. ``cost_tracker=None``
        # (legacy bootstrap rigs) makes the scope a no-op.
        # ``_safe_task_id_segment(name)`` strips control characters so
        # a crafted provider name (newlines, NUL, etc.) cannot inject
        # log lines or distort downstream task-id parsers; the
        # provider-side ``provider`` field on the CostRecord still
        # carries the raw name for forensic accuracy.
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=NotBlankStr("system"),
            task_id=NotBlankStr(
                f"system:providers:test_connection:{_safe_task_id_segment(name)}",
            ),
            call_category=LLMCallCategory.SYSTEM,
        ):
            await driver.complete(messages, model_id)
        elapsed_ms = (self._clock.monotonic() - start) * 1000

        logger.info(
            PROVIDER_CONNECTION_TESTED,
            provider=name,
            model=model_id,
            success=True,
            latency_ms=round(elapsed_ms, 1),
        )
        return TestConnectionResponse(
            success=True,
            latency_ms=round(elapsed_ms, 1),
            model_tested=model_id,
        )

    async def create_from_preset(
        self,
        request: CreateFromPresetRequest,
    ) -> ProviderConfig:
        """Create a provider from a preset template.

        Returns:
            The newly created and persisted ``ProviderConfig`` built from
            the preset template and request overrides.

        Raises:
            ProviderValidationError: If the preset is unknown.
            ProviderAlreadyExistsError: If the name is taken.
        """
        preset = get_preset(request.preset_name)
        if preset is None:
            msg = f"Unknown preset: {request.preset_name!r}"
            logger.warning(
                PROVIDER_VALIDATION_FAILED,
                preset=request.preset_name,
                error=msg,
            )
            raise ProviderValidationError(msg)

        if request.models is not None:
            models = request.models
        elif preset.auth_type == AuthType.NONE:
            # Local providers: skip static LiteLLM DB, rely on live
            # discovery in _maybe_discover_preset_models below.
            models = default_models_for(preset)
        else:
            litellm_models = models_from_litellm(preset.litellm_provider)
            models = litellm_models or default_models_for(preset)
        base_url = request.base_url or preset.default_base_url
        if preset.requires_base_url and not base_url:
            msg = (
                f"Preset {preset.name!r} requires a base URL -- "
                "provide one via base_url"
            )
            logger.warning(
                PROVIDER_VALIDATION_FAILED,
                preset=request.preset_name,
                error=msg,
            )
            raise ProviderValidationError(msg)
        auth_type = request.auth_type or preset.auth_type
        models = await self._maybe_discover_preset_models(
            preset,
            base_url,
            models,
            auth_type=auth_type,
        )
        create_request = CreateProviderRequest(
            name=request.name,
            driver=preset.driver,
            litellm_provider=preset.litellm_provider,
            auth_type=auth_type,
            api_key=request.api_key,
            subscription_token=request.subscription_token,
            tos_accepted=request.tos_accepted,
            base_url=base_url,
            models=models,
            preset_name=preset.name,
        )
        return await self.create_provider(create_request)

    async def _maybe_discover_preset_models(
        self,
        preset: CloudPreset | LocalPreset,
        base_url: str | None,
        models: tuple[ProviderModelConfig, ...],
        *,
        auth_type: AuthType,
    ) -> tuple[ProviderModelConfig, ...]:
        """Auto-discover models for no-auth presets when none given.

        Args:
            preset: Resolved preset definition.
            base_url: Provider base URL (may be user-overridden).
            models: Explicitly provided models (may be empty).
            auth_type: Effective auth type.

        Returns:
            Discovered models if any, otherwise the original models.
        """
        if models or auth_type != AuthType.NONE or not base_url:
            return models
        if self._is_self_connection(base_url):
            return models
        policy = await self._allowlist.load()
        trust = is_url_allowed(base_url, policy)
        discovered = await discover_models(
            base_url,
            preset.name,
            trust_url=trust,
        )
        return discovered or models

    async def discover_models_readonly(
        self,
        name: str,
        *,
        preset_hint: str | None = None,
    ) -> tuple[ProviderModelConfig, ...]:
        """Discover a provider's live models without persisting anything.

        The read-only core of :meth:`discover_models_for_provider`: it
        resolves the endpoint, builds auth headers, applies the SSRF
        trust decision, and queries the provider. It performs NO
        persistence, so the periodic model-refresh probe can use it for
        detection without mutating the configured model list.

        Args:
            name: Provider name.
            preset_hint: Optional preset name for endpoint selection.

        Returns:
            The discovered models, or an empty tuple when the provider
            has no base URL, targets this backend, or discovery returns
            nothing.

        Raises:
            ProviderNotFoundError: If the provider does not exist.
        """
        config = await self.get_provider(name)

        if config.base_url is None:
            logger.info(
                PROVIDER_DISCOVERY_FAILED,
                provider=name,
                reason="no_base_url",
            )
            return ()

        if self._is_self_connection(config.base_url):
            return ()

        resolved_hint = preset_hint or infer_preset_hint(config.base_url)
        api_key = await resolve_provider_api_key(self._app_state, config)
        headers = build_discovery_headers(config, api_key)
        policy = await self._allowlist.load()
        trust = is_url_allowed(config.base_url, policy)
        return await discover_models(
            config.base_url,
            resolved_hint,
            headers=headers,
            trust_url=trust,
        )

    async def discover_models_for_provider(
        self,
        name: str,
        *,
        preset_hint: str | None = None,
    ) -> tuple[ProviderModelConfig, ...]:
        """Discover and update models for an existing provider.

        Args:
            name: Provider name.
            preset_hint: Optional preset name for endpoint selection.

        Returns:
            A tuple of discovered ``ProviderModelConfig`` instances (may
            be empty if the provider has no base URL, targets the backend
            itself, or discovery returns nothing).

        Raises:
            ProviderNotFoundError: If the provider does not exist.
        """
        # Capture the pre-discovery base_url so ``_apply_discovered_models``
        # can detect a concurrent change (TOCTOU): if the provider's
        # base_url is updated while discovery is in flight, the apply is
        # refused and the discovered models are dropped.
        config = await self.get_provider(name)
        discovered = await self.discover_models_readonly(name, preset_hint=preset_hint)

        if discovered and config.base_url is not None:
            applied = await self._apply_discovered_models(
                name,
                config.base_url,
                discovered,
            )
            if not applied:
                return ()

        return discovered

    def _is_self_connection(self, base_url: str) -> bool:
        """Check if a URL points at this backend; log warning if so.

        Returns:
            ``True`` when *base_url* resolves to this backend's own host
            and port; ``False`` otherwise.
        """
        backend_port = self._backend_port
        if is_self_url(base_url, backend_port=backend_port):
            logger.warning(
                PROVIDER_DISCOVERY_SELF_CONNECTION_BLOCKED,
                url=redact_url(base_url),
                backend_port=backend_port,
            )
            return True
        return False

    async def get_discovery_policy(self) -> ProviderDiscoveryPolicy:
        """Return the current discovery allowlist policy.

        Returns:
            The current ``ProviderDiscoveryPolicy`` from the allowlist
            manager.
        """
        return await self._allowlist.load()

    async def add_custom_allowlist_entry(
        self,
        host_port: str,
    ) -> ProviderDiscoveryPolicy:
        """Add a custom host:port to the discovery allowlist.

        Returns:
            The updated ``ProviderDiscoveryPolicy`` after adding the new
            ``host:port`` entry.
        """
        async with self._lock:
            return await self._allowlist.add_entry(host_port)

    async def remove_custom_allowlist_entry(
        self,
        host_port: str,
    ) -> ProviderDiscoveryPolicy:
        """Remove a host:port from the discovery allowlist.

        Returns:
            The updated ``ProviderDiscoveryPolicy`` after removing the
            specified ``host:port`` entry.
        """
        async with self._lock:
            return await self._allowlist.remove_entry(host_port)

    async def _apply_discovered_models(
        self,
        name: str,
        original_base_url: str,
        discovered: tuple[ProviderModelConfig, ...],
    ) -> bool:
        """Atomically verify base_url and persist discovered models.

        Args:
            name: Provider name.
            original_base_url: The base_url used for discovery.
            discovered: Models discovered from the endpoint.

        Returns:
            True if models were persisted, False if aborted.
        """
        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            existing = providers.get(name)
            if existing is None:
                logger.warning(
                    PROVIDER_DISCOVERY_FAILED,
                    provider=name,
                    reason="deleted_during_discovery",
                )
                return False
            if existing.base_url != original_base_url:
                logger.warning(
                    PROVIDER_DISCOVERY_FAILED,
                    provider=name,
                    reason="base_url_changed",
                )
                return False

            updated = apply_update(
                existing,
                UpdateProviderRequest(models=discovered),
            )
            new_providers = {**providers, name: updated}
            await self._validate_and_persist(new_providers)

            logger.info(
                SECURITY_PROVIDER_UPDATED,
                provider=name,
                driver=updated.driver,
                auth_type=updated.auth_type,
            )
        return True

    async def _restore_providers(
        self,
        snapshot: Mapping[str, ProviderConfig],
    ) -> None:
        """Best-effort rollback to a pre-mutation provider snapshot.

        Re-persists ``snapshot`` so a post-persist step that fails (e.g. an
        allowlist update) leaves no half-applied config behind. ``snapshot``
        was a valid persisted state moments earlier, so this should succeed; a
        rollback failure is logged, never raised, so it cannot mask the
        original error that triggered the rollback.

        Args:
            snapshot: The provider mapping to restore.
        """
        try:
            await self._validate_and_persist(dict(snapshot))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
            reraise_critical(exc)
            logger.warning(
                PROVIDER_VALIDATION_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                provider_count=len(snapshot),
            )

    async def _validate_and_persist(
        self,
        new_providers: dict[str, ProviderConfig],
    ) -> None:
        """Validate, persist, and hot-reload providers.

        Args:
            new_providers: Complete new provider dict.

        Raises:
            ProviderValidationError: If build or persist fails.
        """
        # 1. Validate: build registry + router before any I/O
        try:
            registry = ProviderRegistry.from_config(new_providers)
            router = self._build_router(new_providers)
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Provider configuration validation failed: {type(exc).__name__}"
            logger.warning(
                PROVIDER_VALIDATION_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                provider_count=len(new_providers),
            )
            raise ProviderValidationError(msg) from exc

        # 2. Persist to settings
        try:
            serialized = serialize_providers(new_providers)
            await self._settings_service.set(
                "providers",
                "configs",
                json.dumps(serialized),
            )
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to persist provider configuration: {type(exc).__name__}"
            # ``error=str(exc)`` would leak credential material via
            # exception text, so we redact via
            # ``safe_error_description``. ``exc_info=True`` would
            # re-introduce the leak path -- tracebacks attach the
            # exception args (which can include credentials when the
            # raise originated in a credential-bearing call) -- so we
            # deliberately omit it. The redacted error text plus
            # ``error_type`` is enough to triage; the full trace lives
            # only in the in-process exception object that
            # ``ProviderValidationError`` wraps via ``from exc``.
            logger.warning(
                PROVIDER_VALIDATION_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                provider_count=len(new_providers),
            )
            raise ProviderValidationError(msg) from exc

        # 3. Hot-reload: swap in AppState (both sync, no await gap)
        from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

        self._app_state.swap_provider_registry(registry)
        self._app_state.wire(ProvidersStateSlice, model_router=router)

    # ── Local model management ───────────────────────────────

    async def _resolve_local_manager(
        self,
        name: str,
        *,
        capability: str,
    ) -> tuple[ProviderConfig, LocalModelManager]:
        """Resolve provider config and local model manager.

        Returns:
            A ``(ProviderConfig, LocalModelManager)`` tuple for the named
            local provider.

        Raises:
            ProviderValidationError: If the provider's preset does not
                support local model management or has no base URL.
        """
        from synthorg.providers.management.local_models import (  # noqa: PLC0415
            get_local_model_manager,
        )

        config = await self.get_provider(name)
        preset = get_preset(config.preset_name) if config.preset_name else None
        cap_attr = f"supports_model_{capability}"
        if preset is None or not getattr(preset, cap_attr, False):
            msg = f"Provider {name!r} does not support model {capability}"
            logger.warning(
                PROVIDER_VALIDATION_FAILED,
                provider=name,
                error=msg,
            )
            raise ProviderValidationError(msg)
        if not config.base_url:
            msg = f"Provider {name!r} has no base URL configured"
            logger.warning(
                PROVIDER_VALIDATION_FAILED,
                provider=name,
                error=msg,
            )
            raise ProviderValidationError(msg)
        manager = get_local_model_manager(
            config.preset_name,
            config.base_url,
        )
        if manager is None:
            msg = f"No local model manager for preset {config.preset_name!r}"
            logger.warning(
                PROVIDER_LOCAL_MANAGER_NOT_AVAILABLE,
                provider=name,
                preset=config.preset_name,
            )
            raise ProviderValidationError(msg)
        return config, manager

    async def pull_model(
        self,
        name: str,
        model_name: str,
    ) -> AsyncIterator[PullProgressEvent]:
        """Pull a model on a local provider.

        Args:
            name: Provider name.
            model_name: Model to pull.

        Yields:
            Pull progress events.

        Raises:
            ProviderNotFoundError: If the provider does not exist.
            ProviderValidationError: If pull is unsupported.
        """
        _, manager = await self._resolve_local_manager(
            name,
            capability="pull",
        )
        async for event in manager.pull_model(model_name):
            yield event

    async def delete_model(
        self,
        name: str,
        model_id: str,
    ) -> None:
        """Delete a model from a local provider.

        Args:
            name: Provider name.
            model_id: Model identifier to delete.

        Raises:
            ProviderNotFoundError: If the provider does not exist.
            ProviderValidationError: If delete is unsupported.
        """
        _, manager = await self._resolve_local_manager(
            name,
            capability="delete",
        )
        await manager.delete_model(model_id)
        try:
            await self.discover_models_for_provider(name)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROVIDER_DISCOVERY_FAILED,
                provider=name,
                reason="post_delete_refresh_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        await self._audit(
            provider_name=name,
            event_type="model_removed",
            payload={"model_id": model_id},
        )

    async def update_model_config(
        self,
        name: str,
        model_id: str,
        local_params: LocalModelParams,
    ) -> ProviderConfig:
        """Update per-model launch parameters for a local provider.

        Args:
            name: Provider name.
            model_id: Model identifier.
            local_params: New launch parameters.

        Returns:
            Updated provider configuration.

        Raises:
            ProviderNotFoundError: If the provider does not exist.
            ProviderModelNotFoundError: If the model does not exist on
                the provider.
            ProviderValidationError: If config is unsupported.
        """
        await self._resolve_local_manager(name, capability="config")
        async with self._lock:
            providers = await self._config_resolver.get_provider_configs()
            config = providers.get(name)
            if config is None:
                msg = f"Provider {name!r} not found"
                logger.warning(
                    PROVIDER_NOT_FOUND,
                    provider=name,
                    error=msg,
                )
                raise ProviderNotFoundError(msg)
            model_idx = next(
                (i for i, m in enumerate(config.models) if m.id == model_id),
                None,
            )
            if model_idx is None:
                msg = f"Model {model_id!r} not found in provider {name!r}"
                logger.warning(
                    PROVIDER_VALIDATION_FAILED,
                    provider=name,
                    model=model_id,
                    error=msg,
                )
                raise ProviderModelNotFoundError(msg)
            updated_model = config.models[model_idx].model_copy(
                update={"local_params": local_params},
            )
            new_models = (
                *config.models[:model_idx],
                updated_model,
                *config.models[model_idx + 1 :],
            )
            updated = config.model_copy(
                update={"models": new_models},
            )
            new_providers = {**providers, name: updated}
            await self._validate_and_persist(new_providers)
            logger.info(
                PROVIDER_MODEL_CONFIG_UPDATED,
                provider=name,
                model=model_id,
            )
            payload: dict[str, JsonValue] = {
                "model_id": model_id,
                "fields_changed": [
                    *sorted(local_params.model_dump(exclude_unset=True).keys()),
                ],
            }
            await self._audit(
                provider_name=name,
                event_type="model_config_updated",
                payload=payload,
            )
            return updated

    # ── Capability mutations live on ``ProviderCapabilitiesMixin`` ─

    # The six new mutation entry points (audit log, rate-limits
    # GET/PATCH, preset overrides, credentials rotate, manual model
    # add, bulk model sync) are defined on
    # ``ProviderCapabilitiesMixin`` so this file stays under the
    # 800-line ceiling.  Keep the import + inheritance intact at
    # the class declaration.

    def _build_router(
        self,
        providers: dict[str, ProviderConfig],
        *,
        selector: ModelCandidateSelector | None = None,
    ) -> ModelRouter:
        """Build a new ModelRouter from provider configs.

        Args:
            providers: Provider configurations.
            selector: Optional candidate selector (defaults to
                ``QuotaAwareSelector()``).

        Returns:
            New ModelRouter instance.
        """
        from synthorg.providers.routing.router import (  # noqa: PLC0415
            ModelRouter,
        )

        return ModelRouter(
            routing_config=self._config.routing,
            providers=providers,
            selector=selector,
        )
