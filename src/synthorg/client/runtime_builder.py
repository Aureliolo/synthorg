"""Boot-time builder for the client-simulation runtime.

This is the shipped construction site for the :class:`IntakeEngine`.
``create_app`` calls :func:`build_client_simulation_runtime` from its
construction phase (when a ``TaskEngine`` is present and no explicit
``client_simulation_state`` was injected) so ``has_simulation_runtime``
becomes true and the ``/simulations`` + ``/requests`` controllers
register.

The intake strategy is selected at the boot site via the bootstrap
resolver (env > registered default), matching how ``app.py`` reads
other construction-phase settings: ``ConfigResolver`` is not wired
until on-startup, so the database tier is intentionally not consulted
for this baked-in-at-startup choice (``read_only_post_init`` in the
registry). The default ``direct`` strategy needs no provider and makes
no LLM calls, so the runtime comes online for an empty company.
"""

import os
from collections.abc import Mapping  # noqa: TC003
from typing import TYPE_CHECKING

from synthorg.client.config import IntakeConfig
from synthorg.client.factory import UnknownStrategyError, build_intake_strategy
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review.stages.internal import InternalReviewStage
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.client import CLIENT_SIMULATION_RUNTIME_WIRED
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_INTAKE_STRATEGY_KEY = "intake_strategy"
_INTAKE_MODEL_KEY = "intake_model"
_DEFAULT_STRATEGY = "direct"


def _select_provider(app_state: AppState) -> CompletionProvider | None:
    """Return the first registered provider, or ``None`` (empty company).

    Mirrors the worker-execution-service builder's provider selection:
    ``has_active_provider`` is the single source of truth for the
    provider-present switch, and the first registered provider backs
    the boot agent-intake strategy when ``agent`` is selected.
    """
    if not app_state.has_active_provider:
        return None
    registry = app_state.provider_registry
    names = registry.list_providers()
    if not names:
        return None
    return registry.get(names[0])


def build_client_simulation_runtime(
    app_state: AppState,
    *,
    env: Mapping[str, str] = os.environ,
) -> ClientSimulationState:
    """Construct the boot client-simulation runtime state.

    Resolves the intake strategy / model from the ``simulations``
    settings namespace (env > default), builds the strategy via
    :func:`build_intake_strategy`, and returns a
    :class:`ClientSimulationState` carrying a live
    :class:`IntakeEngine` and a single-stage
    :class:`ReviewPipeline` (``InternalReviewStage`` only:
    ``ClientReviewStage`` needs a per-request client, not available
    generically at boot).

    An ``agent`` strategy that cannot be satisfied (no provider or no
    model) degrades to ``direct`` with a WARNING rather than failing
    boot, so a misconfigured non-default strategy never bricks the
    runtime. A ``direct`` failure is a real defect and propagates.

    Args:
        app_state: Live application state. ``task_engine`` must be set
            (the caller gates on this); ``provider_registry`` and
            ``cost_tracker`` are consulted when present.
        env: Environment mapping for the bootstrap resolver. Defaults
            to ``os.environ``; tests pass an explicit dict.

    Returns:
        A populated :class:`ClientSimulationState`.
    """
    task_engine = app_state.task_engine
    requested_strategy = str(
        resolve_init_value(
            SettingNamespace.SIMULATIONS,
            _INTAKE_STRATEGY_KEY,
            env=env,
        ).value
    )
    resolved_model = str(
        resolve_init_value(
            SettingNamespace.SIMULATIONS,
            _INTAKE_MODEL_KEY,
            env=env,
        ).value
    )
    provider = _select_provider(app_state)
    cost_tracker = app_state.cost_tracker if app_state.has_cost_tracker else None

    config = IntakeConfig(
        strategy=requested_strategy,
        model=resolved_model or None,
    )
    try:
        strategy = build_intake_strategy(
            config,
            task_engine=task_engine,
            provider=provider,
            cost_tracker=cost_tracker,
        )
        effective_strategy = requested_strategy
    except UnknownStrategyError as exc:
        if requested_strategy == _DEFAULT_STRATEGY:
            # A failure building the default strategy is a real bug
            # (TaskEngine contract broken), not a config-degrade case.
            raise
        logger.warning(
            CLIENT_SIMULATION_RUNTIME_WIRED,
            requested_strategy=requested_strategy,
            effective_strategy=_DEFAULT_STRATEGY,
            reason="requested strategy unsatisfiable; degraded to direct",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        strategy = build_intake_strategy(
            IntakeConfig(strategy=_DEFAULT_STRATEGY),
            task_engine=task_engine,
        )
        effective_strategy = _DEFAULT_STRATEGY

    intake_engine = IntakeEngine(strategy=strategy)
    review_pipeline = ReviewPipeline(stages=(InternalReviewStage(),))
    logger.info(
        CLIENT_SIMULATION_RUNTIME_WIRED,
        requested_strategy=requested_strategy,
        effective_strategy=effective_strategy,
        has_provider=provider is not None,
        review_stages=list(review_pipeline.stage_names),
    )
    return ClientSimulationState(
        intake_engine=intake_engine,
        review_pipeline=review_pipeline,
    )
