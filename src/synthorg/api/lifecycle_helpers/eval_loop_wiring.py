# module-kind: code
"""Boot wiring for the closed-loop evaluation coordinator.

Constructs :class:`EvalLoopCoordinator` from the performance tracker +
training service once those collaborators are wired (its other deps -- an
:class:`EvaluationService`, :class:`TrajectoryScorer`,
:class:`DogfoodingDatasetBuilder`, and an empty
:class:`ExternalBenchmarkRegistry` -- are built here from the same tracker),
then publishes it on :class:`HrStateSlice` so the coordinator is available
for operator-triggered cycles and the optional background driver.

The periodic :class:`EvalLoopCycleScheduler` that drives ``run_cycle`` on a
cadence is ghost-wired: it is always constructed and started, but only does
work each tick when ``hr.eval_loop_cycle_enabled`` is set AND
``hr.eval_loop_cycle_paused`` is not. A cycle can route corrective actions to
the training pipeline, so the loop is opt-in (default off): both the master
switch and the cadence / window are re-read live per tick, so an operator can
enable, pause, retune, or disable it with no restart. Operators can also
trigger cycles by hand via the API regardless of the switch.

Gated on a wired tracker + training service; without them the coordinator
stays absent and its consumers honestly 503.
"""

from datetime import timedelta
from typing import Final

from synthorg.api.lifecycle_helpers._model_pin_wiring import (
    build_pin_validation_registry,
)
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.trajectory.scorer import TrajectoryScorer
from synthorg.hr.evaluation.config import EvalLoopConfig
from synthorg.hr.evaluation.cycle_scheduler import EvalLoopCycleScheduler
from synthorg.hr.evaluation.deterministic_pattern_identifier import (
    DeterministicPatternIdentifier,
)
from synthorg.hr.evaluation.dogfooding_dataset_builder import DogfoodingDatasetBuilder
from synthorg.hr.evaluation.evaluator import EvaluationService
from synthorg.hr.evaluation.llm_fix_proposer import LlmFixProposer
from synthorg.hr.evaluation.llm_pattern_identifier import LlmPatternIdentifier
from synthorg.hr.evaluation.loop_coordinator import EvalLoopCoordinator
from synthorg.hr.evaluation.pattern_action_dispatcher_impl import (
    RemediationActionDispatcher,
)
from synthorg.hr.evaluation.pattern_protocols import FixProposer, PatternIdentifier
from synthorg.hr.evaluation.table_fix_proposer import TableFixProposer
from synthorg.hr.state import HrStateSlice
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_float
from synthorg.settings.model_ref import parse_model_ref
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)

_SECONDS_PER_HOUR: Final[float] = 3600.0
_LLM_MODE: Final[str] = "llm"


def _resolve_hr_str(key: str) -> str:
    """Resolve a string-typed ``hr.*`` setting at boot.

    Returns:
        The resolved string value (may be empty).
    """
    return str(resolve_init_value(SettingNamespace.HR, key).value)


def _select_provider(
    provider_registry: ProviderRegistry | None,
    requested: str,
) -> CompletionProvider | None:
    """Resolve the eval loop's explicit provider from its model ref.

    ``eval_loop_llm_model`` is a bound ``{provider, model_id}`` reference, so
    the provider is named explicitly. A blank provider (no model pinned) or a
    pinned-but-absent one resolves to ``None`` so the caller degrades to the
    deterministic strategy rather than auto-selecting a provider for the model.

    Returns:
        A completion provider, or ``None`` when none is explicitly resolvable.
    """
    if provider_registry is None or not requested:
        return None
    if requested in provider_registry:
        return provider_registry.get(requested)
    logger.warning(
        API_APP_STARTUP,
        service="eval_loop",
        note="eval-loop model provider absent; degrading to deterministic",
        requested_provider=requested,
    )
    return None


async def _resolve_eval_loop_modes(
    resolver: ConfigResolverProtocol | None,
) -> tuple[str, str, str, str]:
    """Resolve ``(identifier_mode, proposer_mode, model, provider)`` for the loop.

    DB-backed (DB > env > default) when a resolver is wired so an operator
    edit applies on reload; falls back to the bootstrap chain (env > default)
    in a pre-startup / test context where the resolver is absent.

    Returns:
        The ``(identifier_mode, proposer_mode, model, provider_name)`` tuple
        with ``model`` / ``provider_name`` stripped.
    """
    # ``eval_loop_llm_model`` is a model-assignment setting storing a
    # ``ModelRef``: the provider travels with the model (the picker writes
    # both), so the separate provider read is gone.
    if resolver is None:
        boot_ref = parse_model_ref(_resolve_hr_str("eval_loop_llm_model"))
        return (
            _resolve_hr_str("eval_loop_pattern_identifier_mode"),
            _resolve_hr_str("eval_loop_fix_proposer_mode"),
            boot_ref.model_id.strip(),
            boot_ref.provider.strip(),
        )
    namespace = SettingNamespace.HR.value
    try:
        identifier_mode = await resolver.get_str(
            namespace, "eval_loop_pattern_identifier_mode"
        )
        proposer_mode = await resolver.get_str(namespace, "eval_loop_fix_proposer_mode")
        ref = parse_model_ref(await resolver.get_str(namespace, "eval_loop_llm_model"))
        return (
            identifier_mode,
            proposer_mode,
            ref.model_id.strip(),
            ref.provider.strip(),
        )
    except Exception as exc:
        # Identify the failing subsystem before propagating: the reload-helper
        # caller (reload_eval_loop_pattern_strategies) re-raises without its own
        # context, so without this the only entry would be the resolver's. The
        # re-raise re-propagates criticals, so no separate guard is needed.
        logger.warning(
            API_APP_STARTUP,
            service="eval_loop",
            note="settings resolve failed; pattern strategies not rebuilt",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise


def _build_pattern_strategies(
    provider_registry: ProviderRegistry | None,
    *,
    identifier_mode: str,
    proposer_mode: str,
    model: str,
    provider_name: str,
) -> tuple[PatternIdentifier | None, FixProposer | None]:
    """Build provider-backed IDENTIFY/PROPOSE strategies from resolved choices.

    Returns ``(None, None)`` for any step left in deterministic mode (the
    coordinator then uses its shipped defaults). An ``llm``-mode step with no
    model configured or no provider available degrades to deterministic. The
    four mode / model / provider choices are resolved by the caller (DB on the
    reload path, env on boot) so a change rebuilds the strategies live.

    Returns:
        The ``(pattern_identifier, fix_proposer)`` overrides, each possibly
        ``None``.
    """
    if _LLM_MODE not in (identifier_mode, proposer_mode):
        return (None, None)

    if not model:
        logger.warning(
            API_APP_STARTUP,
            service="eval_loop",
            note="llm strategy requested but eval_loop_llm_model unset; deterministic",
        )
        return (None, None)
    provider = _select_provider(provider_registry, provider_name)
    if provider is None:
        logger.warning(
            API_APP_STARTUP,
            service="eval_loop",
            note="llm strategy requested but no provider available; deterministic",
            provider_registry_present=provider_registry is not None,
        )
        return (None, None)

    config = EvalLoopConfig()
    model_id = NotBlankStr(model)
    identifier: PatternIdentifier | None = None
    proposer: FixProposer | None = None
    if identifier_mode == _LLM_MODE:
        identifier = LlmPatternIdentifier(
            provider,
            model=model_id,
            fallback=DeterministicPatternIdentifier(config),
        )
    if proposer_mode == _LLM_MODE:
        proposer = LlmFixProposer(
            provider,
            model=model_id,
            fallback=TableFixProposer(config),
        )
    return (identifier, proposer)


async def wire_eval_loop(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None = None,
) -> None:
    """Wire the evaluation-loop coordinator + opt-in cycle scheduler.

    Idempotent for re-entered lifespans: returns early when the coordinator is
    already wired.

    Args:
        app_state: The application state holding the collaborator slices.
        provider_registry: Registry used to resolve a completion provider when
            an IDENTIFY/PROPOSE step is configured for ``llm`` mode.
    """
    hr = app_state.slice(HrStateSlice)
    if hr.eval_loop_coordinator is not None:
        return
    if hr.performance_tracker is None or hr.training_service is None:
        logger.warning(
            API_APP_STARTUP,
            service="eval_loop",
            note="tracker or training service absent; eval loop disabled",
        )
        return

    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    # Route proposed remediation actions to operators when a notification
    # dispatcher is wired; absent one, the coordinator proposes + logs only.
    notification_dispatcher = app_state.slice(NotificationsStateSlice).dispatcher
    action_dispatcher = (
        RemediationActionDispatcher(notification_dispatcher=notification_dispatcher)
        if notification_dispatcher is not None
        else None
    )
    (
        identifier_mode,
        proposer_mode,
        model,
        provider_name,
    ) = await _resolve_eval_loop_modes(config_resolver)
    pattern_identifier, fix_proposer = _build_pattern_strategies(
        provider_registry,
        identifier_mode=identifier_mode,
        proposer_mode=proposer_mode,
        model=model,
        provider_name=provider_name,
    )
    coordinator = EvalLoopCoordinator(
        performance_tracker=hr.performance_tracker,
        evaluation_service=EvaluationService(
            tracker=hr.performance_tracker,
            config_resolver=config_resolver,
        ),
        trajectory_scorer=TrajectoryScorer(),
        training_service=hr.training_service,
        dataset_builder=DogfoodingDatasetBuilder(
            performance_tracker=hr.performance_tracker,
        ),
        benchmark_registry=build_pin_validation_registry(app_state),
        action_dispatcher=action_dispatcher,
        pattern_identifier=pattern_identifier,
        fix_proposer=fix_proposer,
        clock=app_state.clock,
    )

    interval_seconds = float(
        resolve_init_value(
            SettingNamespace.HR,
            "eval_loop_cycle_interval_seconds",
            parse=parse_float,
        ).value
    )
    window_hours = float(
        resolve_init_value(
            SettingNamespace.HR,
            "eval_loop_cycle_window_hours",
            parse=parse_float,
        ).value
    )
    scheduler = EvalLoopCycleScheduler(
        coordinator,
        interval_seconds=interval_seconds,
        window=timedelta(seconds=window_hours * _SECONDS_PER_HOUR),
        config_resolver=config_resolver,
    )
    try:
        await scheduler.start()
        app_state.wire(
            HrStateSlice,
            eval_loop_coordinator=coordinator,
            eval_loop_cycle_scheduler=scheduler,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        try:
            await scheduler.stop()
        except Exception as stop_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(stop_exc)
            logger.warning(
                API_APP_STARTUP,
                service="eval_loop",
                note="scheduler rollback-stop failed",
                error_type=type(stop_exc).__name__,
                error=safe_error_description(stop_exc),
            )
        # The coordinator is still valuable for manual cycles even when the
        # unattended driver failed to start, so publish it without the
        # scheduler rather than leaving the whole subsystem dead.
        app_state.wire(HrStateSlice, eval_loop_coordinator=coordinator)
        logger.warning(
            API_APP_STARTUP,
            service="eval_loop",
            note="scheduler start failed; coordinator wired without driver",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="eval_loop", note="wired")


async def reload_eval_loop_pattern_strategies(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None = None,
) -> None:
    """Re-resolve the IDENTIFY/PROPOSE strategies and swap them onto the loop.

    Called by ``EvalLoopSettingsSubscriber`` when an operator edits a
    ``hr.eval_loop_*`` model / mode key. Re-resolves the four choices from the
    live settings DB, rebuilds the provider-backed strategies (degrading to
    deterministic when a model / provider is unavailable), and hot-swaps them
    onto the wired coordinator so the next cycle uses them. A no-op when the
    coordinator is not wired (the eval loop is off / unavailable).

    Args:
        app_state: Application state holding the coordinator + resolver.
        provider_registry: Registry used to resolve a completion provider for an
            ``llm``-mode step.
    """
    coordinator = app_state.slice(HrStateSlice).eval_loop_coordinator
    if coordinator is None:
        logger.info(
            API_APP_STARTUP,
            service="eval_loop",
            note="coordinator absent; pattern-strategy reload skipped",
        )
        return
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    (
        identifier_mode,
        proposer_mode,
        model,
        provider_name,
    ) = await _resolve_eval_loop_modes(resolver)
    pattern_identifier, fix_proposer = _build_pattern_strategies(
        provider_registry,
        identifier_mode=identifier_mode,
        proposer_mode=proposer_mode,
        model=model,
        provider_name=provider_name,
    )
    coordinator.set_pattern_strategies(
        pattern_identifier=pattern_identifier, fix_proposer=fix_proposer
    )
    logger.info(
        API_APP_STARTUP,
        service="eval_loop",
        note="pattern strategies reloaded",
    )


__all__ = ["reload_eval_loop_pattern_strategies", "wire_eval_loop"]
