# module-kind: controller
"""Setup-completion endpoint and its prerequisite-validation helpers.

Marks first-run setup complete after validating company / providers /
agent assignments, best-effort binding the operator's chosen embedder,
and reloading the runtime (provider reload + agent bootstrap). The
completion flag is persisted only after the reinit returns clean, so a
broken provider config leaves the operator a retryable error rather than
a half-configured runtime that reports itself as "complete".
"""

import asyncio
from typing import Final

from litestar import Controller, post
from litestar.datastructures import State

from synthorg.api.controllers.setup._embedder_setup import (
    bind_chosen_embedder,
)
from synthorg.api.controllers.setup._feature_model_setup import (
    ensure_per_feature_models as _ensure_per_feature_models,
)
from synthorg.api.controllers.setup._feature_model_setup import (
    pick_decomposition_model_ref as _pick_decomposition_model_ref,
)
from synthorg.api.controllers.setup._runtime_wiring import (
    COMPLETE_LOCK as _COMPLETE_LOCK,
)
from synthorg.api.controllers.setup._runtime_wiring import (
    post_setup_reinit as _post_setup_reinit,
)
from synthorg.api.controllers.setup._status_checks import (
    check_has_agents as _check_has_agents,
)
from synthorg.api.controllers.setup.company_helpers import (
    check_has_company as _check_has_company,
)
from synthorg.api.controllers.setup.company_helpers import (
    check_setup_not_complete as _check_setup_not_complete,
)
from synthorg.api.controllers.setup_agent_validation import (
    validate_persisted_agents_against_providers,
)
from synthorg.api.controllers.setup_agents import (
    get_existing_agents,
)
from synthorg.api.controllers.setup_models import (
    SetupCompleteResponse,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ValidationError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_COMPLETE_CHECK_ERROR,
    SETUP_COMPLETE_SERIALIZED,
    SETUP_COMPLETED,
    SETUP_DECOMPOSITION_MODEL_SELECTED,
    SETUP_NO_AGENTS,
    SETUP_NO_COMPANY,
    SETUP_NO_PROVIDERS,
)
from synthorg.providers.state import (
    ProvidersStateSlice,
    embedding_endpoint_resolver_of,
    provider_management_of,
)
from synthorg.settings.service_protocol import SettingsServiceProtocol
from synthorg.settings.state import settings_service_of

logger = get_logger(__name__)

# Above this wait the completion lock was genuinely contended (a
# concurrent /setup/complete was in flight); an uncontended acquire
# does not suspend, so its measured wait is effectively zero.
_LOCK_CONTENTION_LOG_THRESHOLD_SECONDS: Final[float] = 0.001


async def _validate_completion_prereqs(
    app_state: AppState,
    settings_svc: SettingsServiceProtocol,
) -> bool:
    """Verify company / providers / agent provider+model pairs.

    Extracted from ``complete_setup`` so the controller method stays
    under the 50-line limit. Returns ``has_agents`` so the caller can
    decide whether to log the quick-setup note (already logged here
    too, but kept stable in the return so future callers can chain).

    Raises:
        ValidationError: If company is missing, no provider is
            configured, or a persisted agent references a now-absent
            provider / model.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    has_company = await _check_has_company(settings_svc, strict=True)
    if not has_company:
        msg = "A company must be created before completing setup"
        logger.warning(SETUP_NO_COMPANY)
        raise ValidationError(msg)

    # Quick Setup mode allows zero agents -- log a note but do not raise.
    # ``strict=True`` so a parse/read failure raises instead of silently
    # collapsing to ``False`` (which would skip the persisted-agent
    # validation below and let a corrupted ``company.agents`` blob pass
    # completion as a Quick Setup).
    has_agents = await _check_has_agents(settings_svc, strict=True)
    if not has_agents:
        logger.info(SETUP_NO_AGENTS, note="allowed_for_quick_setup")

    await _assert_provider_configured(app_state)
    if has_agents:
        await _validate_persisted_agents(app_state, settings_svc)
    return has_agents


async def _assert_provider_configured(app_state: AppState) -> None:
    """Raise unless a provider exists in the registry or persisted config.

    Accept a configured provider from either the live runtime registry or the
    persisted provider config. A backend restart before completion empties the
    in-memory registry while the persisted providers remain; ``post_setup_reinit``
    rebuilds the registry from those persisted configs, so gating only on the
    runtime registry would wrongly block completion after such a restart.

    Raises:
        ValidationError: When no provider is configured anywhere.
    """
    provider_registry = app_state.slice(ProvidersStateSlice).registry
    has_runtime_provider = provider_registry is not None and len(provider_registry) > 0
    if (
        not has_runtime_provider
        and not await provider_management_of(app_state).list_providers()
    ):
        msg = "At least one provider must be configured before completing setup"
        logger.warning(SETUP_NO_PROVIDERS)
        raise ValidationError(msg)


async def _validate_persisted_agents(
    app_state: AppState,
    settings_svc: SettingsServiceProtocol,
) -> None:
    """Reject persisted agents whose provider/model was deleted since creation.

    Skipped when ``provider_management`` is empty: in-process test fixtures
    populate the runtime registry without seeding the config, but production
    always has both populated together.

    Raises:
        ValidationError: When a persisted agent references a now-absent
            provider or model.
    """
    persisted_agents = await get_existing_agents(settings_svc)
    providers_map = await provider_management_of(app_state).list_providers()
    if providers_map:
        validate_persisted_agents_against_providers(providers_map, persisted_agents)


async def _run_embedder_binding(
    app_state: AppState,
    settings_svc: SettingsServiceProtocol,
) -> str | None:
    """Bind the operator's chosen embedder. Returns failure reason or None.

    Extracted from ``complete_setup`` for the same line-budget reason
    as :func:`_validate_completion_prereqs`. Non-critical exceptions are
    logged at WARNING and folded into the failure reason rather than
    re-raised, so the completion flow continues to the reinit step
    (which is the gate that actually blocks completion on failure).

    Returns:
        The ``str`` value when present, ``None`` otherwise.
    """
    try:
        return await bind_chosen_embedder(
            settings_svc=settings_svc,
            resolve_endpoint=embedding_endpoint_resolver_of(app_state),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETUP_COMPLETE_CHECK_ERROR,
            check="bind_chosen_embedder",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return "Binding the chosen embedding model raised an unexpected error."


async def _ensure_decomposition_model(
    settings_svc: SettingsServiceProtocol,
) -> None:
    """Auto-select the coordinator's decomposition model as a safety net.

    The coordinator builds eagerly once a provider is configured and requires a
    non-blank ``coordination.decomposition_model``. The wizard's model-selection
    panel prefills a recommendation, but the operator can advance without
    choosing one, so this fills a sensible default from the matched agent roster
    (a top-rung agent's bound ``{provider, model_id}`` assignment) before
    the runtime rebuild on ``/setup/complete`` -- a blank model would otherwise
    fail the rebuild. There is no bare-catalogue fallback: when no roster agent
    carries a bound assignment the setting stays blank (the operator must pick),
    since a provider-less value could not be dispatched anyway.
    """
    entry = await settings_svc.get("coordination", "decomposition_model")
    current = entry.value
    if isinstance(current, str) and current.strip():
        return
    model_ref = _pick_decomposition_model_ref(await get_existing_agents(settings_svc))
    if model_ref is None:
        logger.warning(
            SETUP_COMPLETE_CHECK_ERROR,
            check="auto_select_decomposition_model",
            error_type="NoBoundModelAvailable",
            error=(
                "no roster agent carries a bound (provider, model) assignment "
                "for the decomposition model; the coordinator rebuild on "
                "/setup/complete will require a model to be configured first"
            ),
        )
        return
    await settings_svc.set("coordination", "decomposition_model", model_ref)
    logger.info(SETUP_DECOMPOSITION_MODEL_SELECTED, model_ref=model_ref)


class SetupCompletionController(Controller):
    """Setup-completion endpoint for the setup wizard."""

    path = "/setup"
    tags = ("setup",)

    @post(
        "/complete",
        guards=[
            require_ceo,
            per_op_rate_limit_from_policy("setup.complete", key="user_or_ip"),
        ],
    )
    async def complete_setup(
        self,
        state: State,
    ) -> ApiResponse[SetupCompleteResponse]:
        """Mark first-run setup as complete.

        Validates that a company and at least one provider are configured
        before allowing completion.  Agent configuration is optional
        (Quick Setup mode) -- a warning is logged when no agents exist.

        Args:
            state: Application state.

        Returns:
            Success envelope.

        Raises:
            ConflictError: If setup has already been completed.
            ValidationError: If company or providers are missing.
        """
        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)

        # Serialise the entire check / validate / reinit / persist flow
        # so two concurrent /setup/complete requests cannot both observe
        # ``setup_complete=false`` and race on reinit + flag write. The
        # serialization log fires AFTER acquisition, gated on the measured
        # wait, so it reflects requests that genuinely queued behind an
        # in-flight completion rather than a stale pre-acquire snapshot;
        # the loser then hits ``_check_setup_not_complete`` and gets a
        # clean 409.
        _t_before = asyncio.get_running_loop().time()
        async with _COMPLETE_LOCK:
            _waited = asyncio.get_running_loop().time() - _t_before
            if _waited > _LOCK_CONTENTION_LOG_THRESHOLD_SECONDS:
                logger.info(SETUP_COMPLETE_SERIALIZED, waited_seconds=round(_waited, 3))
            embedder_failure_reason = await _finalize_completion(
                app_state, settings_svc
            )
            return ApiResponse(
                data=SetupCompleteResponse(
                    setup_complete=True,
                    embedder_selected=embedder_failure_reason is None,
                    embedder_failure_reason=embedder_failure_reason,
                ),
            )


async def _finalize_completion(
    app_state: AppState,
    settings_svc: SettingsServiceProtocol,
) -> str | None:
    """Run the gated completion sequence under the held ``_COMPLETE_LOCK``.

    Validates prerequisites, binds the chosen embedder, ensures a
    decomposition model, rebuilds the runtime, and persists the completion flag
    only after the rebuild returns clean.

    Returns:
        The embedder binding failure reason, or ``None`` on success.
    """
    await _check_setup_not_complete(settings_svc)
    await _validate_completion_prereqs(app_state, settings_svc)
    embedder_failure_reason = await _run_embedder_binding(app_state, settings_svc)
    # The coordinator builds eagerly during reinit and requires a non-blank
    # decomposition model; the wizard's picker is optional, so fill a sensible
    # default from the matched roster before the rebuild.
    await _ensure_decomposition_model(settings_svc)
    # On-by-default research + Chief-of-Staff chat read their own models;
    # fill sensible defaults from the roster before the rebuild so the
    # post-setup feature rewire can bring research online live.
    await _ensure_per_feature_models(settings_svc)
    # Reload providers + bootstrap agents BEFORE persisting the completion flag.
    # ``_post_setup_reinit`` propagates failures so a broken provider config or
    # bootstrap error leaves the flag at ``false``; the operator fixes the
    # underlying issue and retries. Without this ordering, the frontend would
    # believe setup succeeded while the runtime is half-configured.
    await _post_setup_reinit(app_state)
    await settings_svc.set("api", "setup_complete", "true")
    logger.info(SETUP_COMPLETED)
    return embedder_failure_reason
