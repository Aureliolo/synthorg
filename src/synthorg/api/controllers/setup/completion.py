# module-kind: controller
"""Setup-completion endpoint and its prerequisite-validation helpers.

Marks first-run setup complete after validating company / providers /
agent assignments, running best-effort embedder auto-selection, and
reloading the runtime (provider reload + agent bootstrap). The
completion flag is persisted only after the reinit returns clean, so a
broken provider config leaves the operator a retryable error rather than
a half-configured runtime that reports itself as "complete".
"""

import asyncio
from typing import Final

from litestar import Controller, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.controllers.setup._embedder_setup import (
    auto_select_embedder,
)
from synthorg.api.controllers.setup._embedder_setup import (
    collect_model_ids as _collect_model_ids,
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
from synthorg.core.normalization import normalize_ascii_lowercase_or_default
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_COMPLETE_CHECK_ERROR,
    SETUP_COMPLETE_SERIALIZED,
    SETUP_COMPLETED,
    SETUP_NO_AGENTS,
    SETUP_NO_COMPANY,
    SETUP_NO_PROVIDERS,
)
from synthorg.providers.state import (
    ProvidersStateSlice,
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

    provider_registry = app_state.slice(ProvidersStateSlice).registry
    if provider_registry is None or len(provider_registry) == 0:
        msg = "At least one provider must be configured before completing setup"
        logger.warning(SETUP_NO_PROVIDERS)
        raise ValidationError(msg)

    # Cross-check persisted agents against provider_management config so
    # an agent whose provider/model was deleted between agent creation
    # and setup completion cannot pass through as ``complete``. Skip
    # when provider_management is empty: in-process test fixtures
    # populate the runtime registry without seeding the config, but
    # production always has both populated together.
    if has_agents:
        persisted_agents = await get_existing_agents(settings_svc)
        providers_map = await provider_management_of(app_state).list_providers()
        if providers_map:
            validate_persisted_agents_against_providers(
                providers_map,
                persisted_agents,
            )
    return has_agents


async def _run_embedder_auto_select(
    app_state: AppState,
    settings_svc: SettingsServiceProtocol,
) -> str | None:
    """Best-effort embedder auto-selection. Returns failure reason or None.

    Extracted from ``complete_setup`` for the same line-budget reason
    as :func:`_validate_completion_prereqs`. Non-critical exceptions are
    logged at WARNING and folded into the failure reason rather than
    re-raised, so the completion flow continues to the reinit step
    (which is the gate that actually blocks completion on failure).

    Returns:
        The ``str`` value when present, ``None`` otherwise.
    """
    provider_registry = require_service(
        app_state.slice(ProvidersStateSlice).registry, "Provider Registry"
    )
    provider_names = provider_registry.list_providers()
    provider_preset_name = provider_names[0] if provider_names else None
    has_gpu = await _read_has_gpu_setting(settings_svc)
    try:
        model_ids = await _collect_model_ids(app_state)
        return await auto_select_embedder(
            settings_svc=settings_svc,
            available_model_ids=model_ids,
            provider_preset_name=provider_preset_name,
            has_gpu=has_gpu,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETUP_COMPLETE_CHECK_ERROR,
            check="auto_select_embedder",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return "Embedder auto-selection raised an unexpected error."


async def _read_has_gpu_setting(settings_svc: SettingsServiceProtocol) -> bool | None:
    """Return the operator-owned ``api/setup_has_gpu`` boolean.

    Returns ``None`` on non-critical read failure (logged at WARNING with
    exception type + scrubbed description) or if the value is unparseable.

    Returns:
        The ``bool`` value when present, ``None`` otherwise.
    """
    try:
        entry = await settings_svc.get("api", "setup_has_gpu")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETUP_COMPLETE_CHECK_ERROR,
            check="read_has_gpu",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    raw = normalize_ascii_lowercase_or_default(entry.value)
    match raw:
        case "true" | "1" | "yes":
            return True
        case "false" | "0" | "no" | "":
            return False
        case _:
            return None


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
            await _check_setup_not_complete(settings_svc)
            has_agents = await _validate_completion_prereqs(app_state, settings_svc)
            embedder_failure_reason = await _run_embedder_auto_select(
                app_state, settings_svc
            )
            # Reload providers + bootstrap agents BEFORE persisting the
            # completion flag. ``_post_setup_reinit`` propagates failures
            # so a broken provider config or bootstrap error leaves the
            # flag at ``false``; the operator fixes the underlying issue
            # and retries. Without this ordering, the frontend would
            # believe setup succeeded while the runtime is half-configured.
            del has_agents
            await _post_setup_reinit(app_state)
            await settings_svc.set("api", "setup_complete", "true")
            logger.info(SETUP_COMPLETED)
            return ApiResponse(
                data=SetupCompleteResponse(
                    setup_complete=True,
                    embedder_selected=embedder_failure_reason is None,
                    embedder_failure_reason=embedder_failure_reason,
                ),
            )
