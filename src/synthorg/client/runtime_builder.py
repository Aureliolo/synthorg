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
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from synthorg.budget.state import BudgetStateSlice
from synthorg.client.config import IntakeConfig
from synthorg.client.factory import UnknownStrategyError, build_intake_strategy
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.review.factory import (
    ReviewPipelineStrategy,
    build_review_pipeline,
)
from synthorg.engine.state import task_engine_of
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.client import CLIENT_SIMULATION_RUNTIME_WIRED
from synthorg.providers.state import has_active_provider, provider_registry_of
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.budget.tracker import CostTracker
    from synthorg.engine.intake.protocol import IntakeStrategy
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_INTAKE_STRATEGY_KEY = "intake_strategy"
_INTAKE_MODEL_KEY = "intake_model"
_INTAKE_DEFAULT_PROJECT_KEY = "intake_default_project"
_REVIEW_PIPELINE_STRATEGY_KEY = "review_pipeline_strategy"
_DEFAULT_STRATEGY = "direct"


def _select_provider(app_state: AppState) -> CompletionProvider | None:
    """Return the first registered provider, or ``None`` (empty company).

    Mirrors the worker-execution-service builder's provider selection:
    ``has_active_provider`` is the single source of truth for the
    provider-present switch, and the first registered provider backs
    the boot agent-intake strategy when ``agent`` is selected.
    """
    if not has_active_provider(app_state):
        return None
    registry = provider_registry_of(app_state)
    names = registry.list_providers()
    if not names:
        return None
    return registry.get(names[0])


def _resolve_intake_settings(
    env: Mapping[str, str],
) -> tuple[str, str | None, str]:
    """Resolve ``(strategy, model, default_project)`` from settings.

    Boot-site read (env > registered default) via the bootstrap
    resolver; ``ConfigResolver`` is not wired at construction. The
    ``default_project`` resolves through the same chain and has a
    non-blank registered default, so it is always a usable project id.

    Returns:
        A ``(strategy, model, default_project)`` triple resolved from the
        simulations settings.
    """
    strategy = str(
        resolve_init_value(
            SettingNamespace.SIMULATIONS, _INTAKE_STRATEGY_KEY, env=env
        ).value
    )
    raw_model = resolve_init_value(
        SettingNamespace.SIMULATIONS, _INTAKE_MODEL_KEY, env=env
    ).value
    model = None if raw_model is None else str(raw_model).strip() or None
    default_project = str(
        resolve_init_value(
            SettingNamespace.SIMULATIONS, _INTAKE_DEFAULT_PROJECT_KEY, env=env
        ).value
    ).strip()
    return strategy, model, default_project


def _resolve_review_pipeline_strategy(
    env: Mapping[str, str],
) -> ReviewPipelineStrategy:
    """Resolve the boot review-pipeline strategy from settings.

    Boot-site read (env > registered default) via the bootstrap
    resolver, matching the intake-strategy read above. The setting is a
    closed ENUM, so the resolved string is one of the two registered
    members.

    Returns:
        The ``review_pipeline_strategy`` discriminator.
    """
    value = str(
        resolve_init_value(
            SettingNamespace.SIMULATIONS, _REVIEW_PIPELINE_STRATEGY_KEY, env=env
        ).value
    )
    return cast("ReviewPipelineStrategy", value)


def _build_intake_with_fallback(  # noqa: PLR0913 -- keyword-only DI
    *,
    requested_strategy: str,
    model: str | None,
    default_project: str,
    task_engine: TaskEngine,
    provider: CompletionProvider | None,
    cost_tracker: CostTracker | None,
) -> tuple[IntakeStrategy, str]:
    """Build the requested intake strategy, degrading ``agent`` to ``direct``.

    A non-default strategy that cannot be satisfied (no provider / no
    model) degrades to ``direct`` with a WARNING so a misconfigured
    choice never bricks boot. A ``direct`` failure is a real defect
    and propagates unchanged.

    Returns:
        A ``(strategy, effective_strategy_name)`` pair: the built strategy
        and the strategy name actually used (degraded to ``direct`` on a
        non-default failure).

    Raises:
        UnknownStrategyError: When the default ``direct`` strategy itself
            fails to build.
    """
    try:
        strategy = build_intake_strategy(
            IntakeConfig(strategy=requested_strategy, model=model),
            task_engine=task_engine,
            default_project=default_project,
            provider=provider,
            cost_tracker=cost_tracker,
        )
    except UnknownStrategyError as exc:
        if requested_strategy == _DEFAULT_STRATEGY:
            log_exception_redacted(
                logger,
                CLIENT_SIMULATION_RUNTIME_WIRED,
                exc,
                requested_strategy=requested_strategy,
                effective_strategy=_DEFAULT_STRATEGY,
                reason="default direct strategy failed during boot",
            )
            raise
        logger.warning(
            CLIENT_SIMULATION_RUNTIME_WIRED,
            requested_strategy=requested_strategy,
            effective_strategy=_DEFAULT_STRATEGY,
            reason="requested strategy unsatisfiable; degraded to direct",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        fallback = build_intake_strategy(
            IntakeConfig(strategy=_DEFAULT_STRATEGY),
            task_engine=task_engine,
            default_project=default_project,
        )
        return fallback, _DEFAULT_STRATEGY
    else:
        return strategy, requested_strategy


def build_client_simulation_runtime(
    app_state: AppState,
    *,
    env: Mapping[str, str] = os.environ,
) -> ClientSimulationState:
    """Construct the boot client-simulation runtime state.

    Default ``direct`` intake makes no LLM call (works for an empty
    company). The review pipeline is ``InternalReviewStage`` only:
    ``ClientReviewStage`` needs a per-request client, unavailable
    generically at boot. ``app_state.task_engine`` must be set (the
    caller gates on this); ``provider_registry`` / ``cost_tracker``
    are consulted when present. ``env`` overrides ``os.environ`` for
    tests.

    Returns:
        The wired ``ClientSimulationState`` for boot.
    """
    task_engine = task_engine_of(app_state)
    requested_strategy, model, default_project = _resolve_intake_settings(env)
    provider = _select_provider(app_state)
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    strategy, effective_strategy = _build_intake_with_fallback(
        requested_strategy=requested_strategy,
        model=model,
        default_project=default_project,
        task_engine=task_engine,
        provider=provider,
        cost_tracker=cost_tracker,
    )
    review_strategy = _resolve_review_pipeline_strategy(env)
    review_pipeline = build_review_pipeline(strategy=review_strategy)
    logger.info(
        CLIENT_SIMULATION_RUNTIME_WIRED,
        requested_strategy=requested_strategy,
        effective_strategy=effective_strategy,
        has_provider=provider is not None,
        review_stages=list(review_pipeline.stage_names),
        intake_default_project=default_project,
    )
    return ClientSimulationState(
        intake_engine=IntakeEngine(strategy=strategy),
        review_pipeline=review_pipeline,
        intake_default_project=default_project,
    )
