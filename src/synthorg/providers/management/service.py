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
import re
from collections.abc import AsyncIterator, Mapping
from typing import ClassVar

from pydantic import JsonValue

from synthorg.api.state import AppState
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.config.schema import (
    LocalModelParams,
    ProviderConfig,
    ProviderModelConfig,
    RootConfig,
)
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.state import provider_credential_catalog_of
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.provider import (
    PROVIDER_ALREADY_EXISTS,
    PROVIDER_CONFIG_PERSIST_FAILED,
    PROVIDER_CONNECTION_TESTED,
    PROVIDER_DISCOVERY_FAILED,
    PROVIDER_HEALTH_PROBE_FAILED,
    PROVIDER_HEALTH_PROBE_SKIPPED,
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
from synthorg.providers.discovery_policy import (
    ProviderDiscoveryPolicy,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import (
    ProviderAlreadyExistsError,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderNotFoundError,
    ProviderValidationError,
    classify_provider_error,
)
from synthorg.providers.health import CallOutcome, ProviderOutcomeClass
from synthorg.providers.health_prober_helpers import (
    ProbeIdentity,
    call_identity,
    call_identity_still_current,
)
from synthorg.providers.management._capabilities_mixin import (
    ProviderCapabilitiesMixin,
)
from synthorg.providers.management._capability_helpers import delete_local_model
from synthorg.providers.management._capability_overrides_mixin import (
    ProviderCapabilityOverridesMixin,
)
from synthorg.providers.management._config_transforms import (
    apply_update,
)
from synthorg.providers.management._credential_helpers import (
    delete_provider_credential,
)
from synthorg.providers.management._discovery_mixin import ProviderDiscoveryMixin
from synthorg.providers.management._persistence import apply_provider_change
from synthorg.providers.management._preset_creation import create_provider_from_preset
from synthorg.providers.management._tool_call_capability_mixin import (
    ProviderToolCallCapabilityMixin,
)
from synthorg.providers.management._transaction_mixin import ProviderTransactionMixin
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
    get_preset,
)
from synthorg.providers.probe_protocol import ProviderProbeRequester
from synthorg.providers.routing.router import ModelRouter
from synthorg.providers.routing.selector import ModelCandidateSelector
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


# The size digits sit immediately after a separator (``:1b``, ``-26b``); the
# negative lookbehind rejects a ``b`` glued to a preceding alphanumeric so a
# cloud id like ``gpt4b-turbo`` is not misread as a 4-billion local model.
_PARAM_SIZE_RE = re.compile(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def _estimated_param_billions(model_id: str) -> float:
    """Best-effort parameter count (in billions) parsed from a model id.

    Local model ids encode their size (``llama3.2:1b``, ``gemma4:26b-a4b``).
    The largest size token wins (total params, not MoE-active), so a sparse
    ``26b-a4b`` sorts by its 26B footprint. Ids with no recognisable size
    yield ``inf`` so sized models are always preferred for the probe.

    Returns:
        The estimated size in billions of parameters, or ``inf`` if unknown.
    """
    sizes = [float(match) for match in _PARAM_SIZE_RE.findall(model_id)]
    return max(sizes) if sizes else float("inf")


def _cheapest_probe_model_id(models: tuple[ProviderModelConfig, ...]) -> str:
    """Pick the cheapest model to probe for a connectivity test.

    A probe sends a chat completion, so a heavyweight default (the first
    configured model could be a 26B) makes the test cold-load gigabytes for
    a one-token reply. Choose the smallest non-embedding model instead;
    embedding-only models are skipped because they reject chat completion.
    Falls back to the first model when nothing better can be identified.

    Returns:
        The id of the model to probe.
    """
    chat_models = [m for m in models if "embed" not in m.id.lower()]
    candidates = chat_models or list(models)
    return min(candidates, key=lambda m: _estimated_param_billions(m.id)).id


class ProviderManagementService(
    ProviderDiscoveryMixin,
    ProviderCapabilitiesMixin,
    ProviderCapabilityOverridesMixin,
    ProviderToolCallCapabilityMixin,
    ProviderTransactionMixin,
):
    """Runtime CRUD service for LLM providers.

    All mutating operations are serialised under an asyncio lock
    to prevent read-modify-write races on the provider config blob.

    Args:
        settings_service: Settings persistence layer.
        config_resolver: Typed config accessor.
        app_state: Application state for hot-reload swaps.
        config: Root company configuration.
        backend_port: The resolved API server bind port, injected from the
            wiring site so the service performs no bootstrap env read.
            Used to detect self-referential discovery URLs.
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

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.PROVIDERS_TEST_CONNECTION

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        config_resolver: ConfigResolver,
        app_state: AppState,
        config: RootConfig,
        backend_port: int,
        audit_service: ProviderAuditService | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings_service = settings_service
        self._config_resolver = config_resolver
        self._app_state = app_state
        self._config = config
        self._audit_service = audit_service
        self._cost_tracker = cost_tracker
        self._clock: Clock = clock if clock is not None else SystemClock()
        # Resolved once at the wiring site (bootstrap) and injected so the
        # service stays free of bootstrap env reads; used to detect
        # self-referential discovery URLs.
        self._backend_port = backend_port
        self._lock = asyncio.Lock()
        self._allowlist = DiscoveryAllowlistManager(
            settings_service=settings_service,
            config_resolver=config_resolver,
        )
        # Injected post-construction: the prober is built during on-startup
        # wiring, after this service exists. ``None`` leaves a mutation
        # unprobed rather than failing it.
        self._probe_requester: ProviderProbeRequester | None = None
        # One in-flight connection test per provider. A test is a real billed
        # completion, and the dashboard reaches this from a per-provider
        # recheck, an all-provider sweep and the connection-test button at
        # once, so without this three arrivals for one provider bill three
        # calls to answer the same question. Keyed per provider rather than
        # shared, so a slow provider cannot hold up a test of a different one.
        self._test_locks: dict[str, asyncio.Lock] = {}

    def set_probe_requester(self, requester: ProviderProbeRequester) -> None:
        """Wire the health prober used to probe a provider on mutation.

        Args:
            requester: The prober that services an out-of-cycle probe.
        """
        self._probe_requester = requester

    async def _probe_after_mutation(self, name: str) -> None:
        """Probe *name* now so its health reflects the mutation immediately.

        Without this a newly created provider reports UNKNOWN -- rendered
        identically to a never-reachable one -- until the next periodic cycle,
        up to the full probe interval later. Best-effort by design: the
        provider is already persisted, so a probe failure must not turn a
        successful mutation into an error.

        The probe is awaited on the request, so it carries its own deadline:
        a mistyped host would otherwise hold the save open for the probe's own
        connect timeout plus DNS. Timing out costs only the immediate health
        reading, which the next sweep supplies anyway.

        Raises:
            asyncio.CancelledError: Propagated immediately so shutdown is not
                swallowed by the best-effort handler below.
        """
        requester = self._probe_requester
        if requester is None:
            return
        try:
            budget = await self._config_resolver.get_float(
                "api", "post_mutation_probe_timeout_seconds"
            )
            async with asyncio.timeout(budget):
                await requester.probe_provider(name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROVIDER_HEALTH_PROBE_FAILED,
                provider=name,
                note="post-mutation probe failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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

            new_config = await self._persist_new_provider(request, providers)

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
        # Probed outside the lock: this is a network round-trip (a 10s HTTP
        # budget plus DNS resolution), and ``self._lock`` serialises EVERY
        # mutating entry point on this service, so holding it across the probe
        # would stall unrelated providers' mutations behind one unreachable
        # endpoint. The config is already persisted, and the probe re-reads it
        # itself, so it needs no exclusion.
        await self._probe_after_mutation(request.name)
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

            updated = await self._persist_updated_provider(
                name, request, existing, providers
            )

            # Beside driver and auth type because it decides the same class
            # of question: how this connection charges is what makes its
            # spend measurable, so re-declaring a flat-rate connection as
            # per-token turns a ceiling that cannot bind back into one the
            # budget surface reports as binding.
            logger.info(
                SECURITY_PROVIDER_UPDATED,
                provider=name,
                driver=updated.driver,
                auth_type=updated.auth_type,
                billing_model=updated.billing_model.value,
            )
            await self._audit(
                provider_name=name,
                event_type="provider_updated",
                payload=_diff_provider_update(existing, updated),
            )
        # Outside the lock for the same reason as ``create_provider``: a
        # re-pointed endpoint must be re-probed, but not while every other
        # provider mutation waits on the result.
        await self._probe_after_mutation(name)
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

        Single-flight per provider: concurrent callers asking about the same
        provider wait on one in-flight test rather than each billing their
        own completion. Serialising by name rather than globally keeps a slow
        provider from delaying a test of a different one.

        Returns:
            A ``TestConnectionResponse`` with the probe outcome (success,
            latency, model tested, and any error message).

        Raises:
            ProviderNotFoundError: If the provider does not exist.
            asyncio.CancelledError: Propagated so shutdown is not swallowed,
                by the probe itself and by the health recording that follows
                it. A provider that simply could not be reached is not this
                case; that returns an unsuccessful response.
        """
        async with self._test_lock_for(name):
            return await self._test_connection_once(name, request)

    def _test_lock_for(self, name: str) -> asyncio.Lock:
        """The single-flight lock guarding tests of *name*.

        Created on first use and kept, because the set of providers is
        operator-sized and a lock is cheap; evicting one would need to prove
        nothing is waiting on it, which is the bug this guards against.

        Returns:
            The lock for *name*.
        """
        lock = self._test_locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._test_locks[name] = lock
        return lock

    async def _test_connection_once(
        self,
        name: str,
        request: TestConnectionRequest,
    ) -> TestConnectionResponse:
        """Run one connection test, already serialised per provider.

        Returns:
            A ``TestConnectionResponse`` with the probe outcome.

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

        model_id = request.model or _cheapest_probe_model_id(config.models)
        identity = call_identity(config)
        response, outcome_class = await self._do_test_connection(name, config, model_id)
        await self._record_test_outcome(
            name, response, identity, outcome_class=outcome_class
        )
        return response

    async def _record_test_outcome(
        self,
        name: str,
        response: TestConnectionResponse,
        identity: ProbeIdentity,
        *,
        outcome_class: ProviderOutcomeClass | None = None,
    ) -> None:
        """Let a connection test move the provider's health.

        A test is a real call to the provider, so its verdict is exactly the
        evidence health is derived from; leaving it unrecorded is what made a
        provider read DOWN long after the operator had fixed it, with no
        control short of re-saving the provider to say otherwise.

        Discarded when the provider no longer matches *identity*: a test is a
        long call, and an operator who repointed the endpoint or rotated the
        credential while it ran would otherwise see the old configuration's
        verdict land on the new one and stay there until something else calls
        it.

        Best-effort: the test already has its answer for the caller, so a
        tracker failure must not turn a completed test into an error.

        Args:
            name: Provider the test ran against.
            response: What the test found.
            identity: The configuration the test was a statement about.
            outcome_class: The classified failure, when the test failed.
                ``None`` for a success, which the recorder derives.

        Raises:
            asyncio.CancelledError: Propagated so shutdown is not swallowed.
        """
        requester = self._probe_requester
        if requester is None:
            return
        if not await call_identity_still_current(
            name, identity, config_resolver=self._config_resolver
        ):
            logger.debug(
                PROVIDER_HEALTH_PROBE_SKIPPED,
                provider=name,
                reason="config_changed",
            )
            return
        try:
            await requester.record_outcome(
                name,
                CallOutcome(
                    success=response.success,
                    # A failure that never reached the wire has no round trip
                    # to report; 0.0 keeps it out of the latency average it
                    # would otherwise drag, while still counting as a failed
                    # call.
                    response_time_ms=response.latency_ms or 0.0,
                    error_message=response.error,
                    model=response.model_tested,
                    # Without the class, a test refused for an empty balance
                    # records only "it failed", and the payment-required latch
                    # that keeps agents off a pair no retry will fix never
                    # arms from the one call an operator makes on purpose.
                    outcome_class=outcome_class,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised; see below
            # lint-allow: swallow-ok -- the test already has its answer for
            # the caller, so a tracker fault must not turn a completed test
            # into an error it did not have.
            reraise_critical(exc)
            logger.warning(
                PROVIDER_HEALTH_PROBE_FAILED,
                provider=name,
                note="recording the connection-test outcome failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _do_test_connection(
        self,
        name: str,
        config: ProviderConfig,
        model_id: str,
    ) -> tuple[TestConnectionResponse, ProviderOutcomeClass | None]:
        """Execute the actual connection test probe.

        Returns:
            The ``TestConnectionResponse`` reflecting the probe outcome
            (success with latency, or failure with an error message), paired
            with the classified failure so the recorder can put the test on
            the same footing as any other real call. ``None`` for a success,
            which the recorder derives.

        Raises:
            asyncio.CancelledError: Propagated immediately if the task is
                cancelled during the probe.
        """
        from synthorg.providers.resilience.errors import (  # noqa: PLC0415
            RetryExhaustedError,
        )

        try:
            return await self._probe_provider(name, config, model_id), None
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
            return (
                TestConnectionResponse(
                    success=False,
                    error=safe_error_description(exc),
                    model_tested=model_id,
                ),
                ProviderOutcomeClass.for_error(classify_provider_error(exc)),
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
            return (
                TestConnectionResponse(
                    success=False,
                    error=safe_error_description(exc),
                    model_tested=model_id,
                ),
                ProviderOutcomeClass.for_error(classify_provider_error(exc)),
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
            return (
                TestConnectionResponse(
                    success=False,
                    error=f"Connection test failed: {type(exc).__name__}",
                    model_tested=model_id,
                ),
                # Not a `ProviderError`, so nothing classified it. `OTHER` is
                # the honest bucket: it still counts as a failure, and no
                # bucket claims to know which.
                ProviderOutcomeClass.OTHER,
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
        # A connection probe belongs to no task, so it names none. The
        # record's own ``provider`` field carries the raw name, which is
        # also why nothing here has to sanitise it into an id template.
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            purpose=self.metadata.prompt_class_id,
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

        Thin delegator to :func:`create_provider_from_preset` (in
        ``_preset_creation``), which owns the preset resolution and
        model-seed selection.

        Returns:
            The newly created and persisted ``ProviderConfig`` built from
            the preset template and request overrides.

        Raises:
            ProviderValidationError: If the preset is unknown.
            ProviderAlreadyExistsError: If the name is taken.
        """
        return await create_provider_from_preset(self, request)

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
                billing_model=updated.billing_model.value,
            )
        return True

    async def _restore_providers(
        self,
        snapshot: Mapping[str, ProviderConfig],
    ) -> bool:
        """Best-effort rollback to a pre-mutation provider snapshot.

        Re-persists ``snapshot`` so a post-persist step that fails (e.g. an
        allowlist update) leaves no half-applied config behind. ``snapshot``
        was a valid persisted state moments earlier, so this should succeed; a
        rollback failure is logged, never raised, so it cannot mask the
        original error that triggered the rollback.

        Args:
            snapshot: The provider mapping to restore.

        Returns:
            ``True`` if the snapshot was re-persisted; ``False`` if the
            restore itself failed (so callers must NOT then mutate the
            credential, which the now-still-persisted config still
            references).
        """
        try:
            await self._validate_and_persist(dict(snapshot))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
            reraise_critical(exc)
            logger.error(
                PROVIDER_CONFIG_PERSIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                provider_count=len(snapshot),
                rollback_failed=True,
            )
            return False
        return True

    async def _validate_and_persist(
        self,
        new_providers: dict[str, ProviderConfig],
    ) -> None:
        """Validate, persist, and atomically hot-reload providers.

        Thin delegator to :func:`apply_provider_change` (in
        ``_persistence``), which owns the staged failure types and the
        rollback-on-swap-failure contract.

        Args:
            new_providers: Complete new provider dict.

        Raises:
            ProviderValidationError: If the registry/router build fails.
            ProviderSerializationError: If envelope serialisation fails.
            ProviderPersistenceError: If the DB write or hot-reload fails.
        """
        await apply_provider_change(
            app_state=self._app_state,
            settings_service=self._settings_service,
            config_resolver=self._config_resolver,
            new_providers=new_providers,
            build_router=self._build_router,
        )

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
            ProviderModelNotFoundError: If the model does not exist on the
                provider.
            ProviderError: If the upstream delete request fails.
        """
        _, manager = await self._resolve_local_manager(
            name,
            capability="delete",
        )
        await delete_local_model(manager, name=name, model_id=model_id)
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

    # ── Capability mutations live on ``ProviderCapabilitiesMixin`` /
    #    ``ProviderCapabilityOverridesMixin`` ─

    # The mutation entry points (audit log, rate-limits GET/PATCH,
    # preset overrides, credentials rotate, manual model add, bulk
    # model sync, capability-override PATCH) are defined on those two
    # mixins so this file stays under the 800-line ceiling.  Keep the
    # imports + inheritance intact at the class declaration.

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
