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
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from synthorg.budget.state import BudgetStateSlice
from synthorg.client.config import IntakeConfig
from synthorg.client.factory import UnknownStrategyError, build_intake_strategy
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.state import ClientStateSlice
from synthorg.core.types import NotBlankStr
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.quality.verification_config import (
    DecomposerVariant,
    GraderVariant,
    VerificationConfig,
)
from synthorg.engine.quality.verification_factory import (
    build_decomposer,
    build_grader,
)
from synthorg.engine.review.factory import (
    ReviewPipelineStrategy,
    build_review_pipeline,
)
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review.stages.verification import VerificationReviewStage
from synthorg.engine.state import task_engine_of
from synthorg.llm.model_tier_policy import tier_model_id
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.client import CLIENT_SIMULATION_RUNTIME_WIRED
from synthorg.providers.model_binding import resolve_ref_provider
from synthorg.providers.state import has_active_provider, provider_registry_of
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_bool
from synthorg.settings.model_ref import parse_model_ref
from synthorg.settings.state import SettingsStateSlice

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.budget.tracker_protocol import CostTrackerProtocol
    from synthorg.engine.intake.protocol import IntakeStrategy
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_INTAKE_STRATEGY_KEY = "intake_strategy"
_INTAKE_MODEL_KEY = "intake_model"
_INTAKE_DEFAULT_PROJECT_KEY = "intake_default_project"
_REVIEW_PIPELINE_STRATEGY_KEY = "review_pipeline_strategy"
_VERIFICATION_ENABLED_KEY = "verification_review_enabled"
_VERIFICATION_GRADER_KEY = "verification_grader"
_VERIFICATION_DECOMPOSER_KEY = "verification_decomposer"
_DEFAULT_STRATEGY = "direct"


def _select_provider(app_state: AppState) -> CompletionProvider | None:
    """Return the explicit default system provider, or ``None``.

    The verification-stage grader/decomposer and the boot agent-intake
    strategy are system actors with no dedicated per-feature model, so they
    dispatch on the explicit ``providers.default_provider`` (a sole registered
    provider resolves automatically; several with none chosen resolve to
    ``None``). There is no first-registered fallback.
    """
    if not has_active_provider(app_state):
        return None
    return provider_registry_of(app_state).default_provider()


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


def _resolve_verification_choices(env: Mapping[str, str]) -> tuple[bool, str, str]:
    """Resolve ``(enabled, grader, decomposer)`` from the bootstrap chain.

    Boot helper (env > registered default). The reload path resolves these
    same three keys through the DB-backed ``ConfigResolver`` instead.

    Returns:
        The ``(enabled, grader, decomposer)`` triple.
    """
    enabled = bool(
        resolve_init_value(
            SettingNamespace.SIMULATIONS,
            _VERIFICATION_ENABLED_KEY,
            env=env,
            parse=parse_bool,
        ).value
    )
    grader = str(
        resolve_init_value(
            SettingNamespace.SIMULATIONS, _VERIFICATION_GRADER_KEY, env=env
        ).value
    )
    decomposer = str(
        resolve_init_value(
            SettingNamespace.SIMULATIONS, _VERIFICATION_DECOMPOSER_KEY, env=env
        ).value
    )
    return enabled, grader, decomposer


def _make_verification_config(
    grader: str,
    decomposer: str,
    *,
    has_provider: bool,
) -> VerificationConfig:
    """Build the verification config from resolved grader/decomposer choices.

    The ``llm`` variants degrade to the deterministic ``heuristic`` /
    ``identity`` variants when no provider is registered (empty company),
    so the stage always comes online without a provider.

    Returns:
        The resolved :class:`VerificationConfig`.
    """
    if not has_provider and (grader == "llm" or decomposer == "llm"):
        logger.warning(
            CLIENT_SIMULATION_RUNTIME_WIRED,
            note="verification llm variant requested without a provider; "
            "degrading to deterministic heuristic/identity",
            grader=grader,
            decomposer=decomposer,
        )
        # Degrade only the setting that asked for "llm"; leave the other so
        # GraderVariant / DecomposerVariant still validates and rejects an
        # otherwise-invalid value instead of having it silently rewritten.
        if grader == "llm":
            grader = "heuristic"
        if decomposer == "llm":
            decomposer = "identity"
    return VerificationConfig(
        grader=GraderVariant(grader),
        decomposer=DecomposerVariant(decomposer),
    )


def _build_verification_stage(
    *,
    enabled: bool,
    grader: str,
    decomposer: str,
    provider: CompletionProvider | None,
    cost_tracker: CostTrackerProtocol | None,
) -> VerificationReviewStage | None:
    """Build the rubric-grading review stage when enabled.

    The ``enabled`` / ``grader`` / ``decomposer`` choices are resolved by the
    caller (DB-backed on the reload path, env on the boot path) so the stage
    rebuilds with the operator's live values on a settings change.

    Returns:
        A :class:`VerificationReviewStage` when
        ``simulations.verification_review_enabled`` is set, otherwise
        ``None`` (the stage is omitted from the pipeline).
    """
    if not enabled:
        return None
    config = _make_verification_config(
        grader, decomposer, has_provider=provider is not None
    )
    # Honour the requested tier via the model-tier policy (large -> medium ->
    # small archetype id) rather than discarding it and pinning one model, so
    # an LLM-backed decomposer/grader selects the model its tier policy maps to.
    tier_resolver = (
        (lambda tier: NotBlankStr(tier_model_id(tier)))
        if provider is not None
        else None
    )
    decomposer_impl = build_decomposer(
        config,
        provider=provider,
        tier_resolver=tier_resolver,
        cost_tracker=cost_tracker,
    )
    grader_impl = build_grader(
        config,
        provider=provider,
        tier_resolver=tier_resolver,
        cost_tracker=cost_tracker,
    )
    return VerificationReviewStage(decomposer=decomposer_impl, grader=grader_impl)


def _build_intake_with_fallback(
    *,
    requested_strategy: str,
    model: str | None,
    default_project: str,
    task_engine: TaskEngine,
    provider: CompletionProvider | None,
    cost_tracker: CostTrackerProtocol | None,
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


def _build_simulation_components(
    app_state: AppState,
    *,
    requested_strategy: str,
    model: str | None,
    default_project: str,
    review_strategy: ReviewPipelineStrategy,
    verification_enabled: bool,
    verification_grader: str,
    verification_decomposer: str,
) -> tuple[IntakeEngine, ReviewPipeline]:
    """Build the config-driven simulation components from resolved choices.

    Returns the stateless intake engine + review pipeline that the boot builder
    and the runtime reload both compose; the intake strategy degrades to
    ``direct`` when unsatisfiable. Only these two config-driven objects are
    rebuilt, so the reload path can swap them onto the existing state and keep
    the mutable stores intact. Every config-driven choice (intake, review, and
    the three verification-stage settings) is resolved by the caller (DB-backed
    on reload, env on boot), so an operator change to any of them is picked up
    on the next reload without a restart.

    Returns:
        The ``(intake_engine, review_pipeline)`` pair.
    """
    task_engine = task_engine_of(app_state)
    provider = _select_provider(app_state)
    # The agent intake honours the model ref's provider; the verification
    # stage keeps the active provider (its grader/decomposer models are their
    # own settings, not the intake model).
    intake_ref = parse_model_ref(model or "")
    intake_model = intake_ref.model_id or None
    # Only resolve a provider when an intake model is actually set: an unset
    # model is not a misconfiguration, so it must not emit a "no provider"
    # warning (which would otherwise mask the agent->direct degrade log).
    intake_provider = (
        resolve_ref_provider(
            app_state,
            intake_ref,
            event=CLIENT_SIMULATION_RUNTIME_WIRED,
            subject="intake",
        )
        if intake_model
        else None
    )
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    strategy, effective_strategy = _build_intake_with_fallback(
        requested_strategy=requested_strategy,
        model=intake_model,
        default_project=default_project,
        task_engine=task_engine,
        provider=intake_provider,
        cost_tracker=cost_tracker,
    )
    verification_stage = _build_verification_stage(
        enabled=verification_enabled,
        grader=verification_grader,
        decomposer=verification_decomposer,
        provider=provider,
        cost_tracker=cost_tracker,
    )
    review_pipeline = build_review_pipeline(
        strategy=review_strategy,
        verification_stage=verification_stage,
    )
    logger.info(
        CLIENT_SIMULATION_RUNTIME_WIRED,
        requested_strategy=requested_strategy,
        effective_strategy=effective_strategy,
        has_provider=provider is not None,
        review_stages=list(review_pipeline.stage_names),
        verification_stage_active=verification_stage is not None,
        intake_default_project=default_project,
    )
    return IntakeEngine(strategy=strategy), review_pipeline


def build_client_simulation_runtime(
    app_state: AppState,
    *,
    env: Mapping[str, str] = os.environ,
) -> ClientSimulationState:
    """Construct the boot client-simulation runtime state.

    Default ``direct`` intake makes no LLM call (works for an empty
    company). The default review pipeline is ``("verification",
    "internal")``: a rubric-grading ``VerificationReviewStage`` gates
    before the ``InternalReviewStage`` (set
    ``simulations.verification_review_enabled`` to ``false`` to drop it,
    leaving ``internal`` only). ``ClientReviewStage`` is never wired
    here: it needs a per-request client, unavailable generically at
    boot. ``app_state.task_engine`` must be set (the caller gates on
    this); ``provider_registry`` / ``cost_tracker`` are consulted when
    present. ``env`` overrides ``os.environ`` for tests.

    The intake / review / verification choices are read via the bootstrap
    resolver (env > registered default) because ``ConfigResolver`` is not wired
    at construction; a DB override is then picked up on-startup and on every
    settings change via :func:`reload_client_simulation_runtime`.

    Returns:
        The wired ``ClientSimulationState`` for boot.
    """
    requested_strategy, model, default_project = _resolve_intake_settings(env)
    review_strategy = _resolve_review_pipeline_strategy(env)
    verification_enabled, verification_grader, verification_decomposer = (
        _resolve_verification_choices(env)
    )
    intake_engine, review_pipeline = _build_simulation_components(
        app_state,
        requested_strategy=requested_strategy,
        model=model,
        default_project=default_project,
        review_strategy=review_strategy,
        verification_enabled=verification_enabled,
        verification_grader=verification_grader,
        verification_decomposer=verification_decomposer,
    )
    return ClientSimulationState(
        intake_engine=intake_engine,
        review_pipeline=review_pipeline,
        intake_default_project=default_project,
    )


async def reload_client_simulation_runtime(app_state: AppState) -> None:
    """Rebuild the simulation components from the live settings and swap them in.

    Re-reads the hot intake / review / verification keys (``intake_strategy``,
    ``intake_model``, ``intake_default_project``, ``review_pipeline_strategy``,
    ``verification_review_enabled`` / ``verification_grader`` /
    ``verification_decomposer``) through the DB-backed ``ConfigResolver``
    (DB > env > default), rebuilds the intake engine + review pipeline (including
    the verification stage), and atomically swaps them onto the existing
    ``ClientSimulationState`` via ``dataclasses.replace``. Replacing only the
    config-driven fields preserves the live mutable stores (client pool, request
    / simulation / feedback stores, in-flight background tasks), so a hot-reload
    never discards in-flight work. Called on-startup (so a DB override is
    honoured on every boot, since construction reads only env/default) and on
    every ``reload_runtime_services`` / simulations settings change.

    When the resolver is not yet wired (a pre-startup context) the keys fall
    back to the bootstrap resolver (env > registered default). A blank
    ``intake_default_project`` is rejected (the previous runtime is retained
    unchanged) so an operator clearing the override cannot wire an empty project.
    """
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    if resolver is None:
        requested_strategy, model, default_project = _resolve_intake_settings(
            os.environ
        )
        review_strategy = _resolve_review_pipeline_strategy(os.environ)
        verification_enabled, verification_grader, verification_decomposer = (
            _resolve_verification_choices(os.environ)
        )
    else:
        namespace = SettingNamespace.SIMULATIONS.value
        try:
            requested_strategy = await resolver.get_str(namespace, _INTAKE_STRATEGY_KEY)
            raw_model = await resolver.get_str(namespace, _INTAKE_MODEL_KEY)
            model = (raw_model.strip() or None) if raw_model else None
            default_project = (
                await resolver.get_str(namespace, _INTAKE_DEFAULT_PROJECT_KEY)
            ).strip()
            review_strategy = cast(
                "ReviewPipelineStrategy",
                await resolver.get_str(namespace, _REVIEW_PIPELINE_STRATEGY_KEY),
            )
            verification_enabled = await resolver.get_bool(
                namespace, _VERIFICATION_ENABLED_KEY
            )
            verification_grader = await resolver.get_str(
                namespace, _VERIFICATION_GRADER_KEY
            )
            verification_decomposer = await resolver.get_str(
                namespace, _VERIFICATION_DECOMPOSER_KEY
            )
        except Exception as exc:
            # Log which subsystem's resolve failed before propagating: on the
            # startup-lifecycle path this is the only entry tying the failure
            # to the client-simulation runtime (the subscriber path also wraps
            # it with SETTINGS_SERVICE_SWAP_FAILED). Re-raise re-propagates
            # criticals, so no separate reraise_critical guard is needed.
            logger.warning(
                CLIENT_SIMULATION_RUNTIME_WIRED,
                service="client_simulation_runtime",
                note="settings resolve failed; runtime not rebuilt",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
    if not default_project:
        logger.warning(
            CLIENT_SIMULATION_RUNTIME_WIRED,
            service="client_simulation_runtime",
            note="intake_default_project resolved blank; retaining previous runtime",
        )
        return
    intake_engine, review_pipeline = _build_simulation_components(
        app_state,
        requested_strategy=requested_strategy,
        model=model,
        default_project=default_project,
        review_strategy=review_strategy,
        verification_enabled=verification_enabled,
        verification_grader=verification_grader,
        verification_decomposer=verification_decomposer,
    )
    existing = app_state.slice(ClientStateSlice).simulation_state
    if existing is None:
        new_state = ClientSimulationState(
            intake_engine=intake_engine,
            review_pipeline=review_pipeline,
            intake_default_project=default_project,
        )
    else:
        new_state = replace(
            existing,
            intake_engine=intake_engine,
            review_pipeline=review_pipeline,
            intake_default_project=default_project,
        )
    app_state.wire(ClientStateSlice, simulation_state=new_state)
