# module-kind: orchestrator
"""On-startup wiring for the deep CEO-interview charter engine.

Split out of :mod:`feature_wiring` so each module stays within its
module-size tier. ``_wire_charter_engine`` is the ``charter_engine``
subsystem's ``activate``, ordered by what it declares it needs; it is
best-effort and idempotent (an already-wired interview service
short-circuits) and a
missing collaborator leaves the charter controllers to 503 rather than
poisoning startup.

The approve path is a **second** subsystem, ``charter_dispatch``, whose
activation is :func:`attach_charter_dispatcher`. Interviewing needs a provider
and persistence, which exist early; dispatching additionally needs the work
pipeline, the forecast store and the budget config, which arrive with the
runtime services seconds later. Building both here left the dispatcher absent
for the life of the process, because the interview service's own idempotency
guard then returned before the dispatcher on every later reconciler pass, and
``charter_engine`` reported ``active`` throughout because its probe reads the
interview service. There is exactly one owner of the dispatcher, and it is the
attach below.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from synthorg.api._feature_provider_resolution import resolve_feature_provider
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.budget.config import BudgetConfig
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.config import SelfImprovementConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.charter import CHARTER_SUBSTRATE_UNAVAILABLE
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.settings.state import SettingsStateSlice

if TYPE_CHECKING:
    from synthorg.meta.charter.dispatch import CharterDispatcher
    from synthorg.meta.charter.service import CharterInterviewService

logger = get_logger(__name__)


async def _resolve_live_charter_config(
    resolver: ConfigResolverProtocol, *, fallback: CharterConfig
) -> CharterConfig:
    """Build a ``CharterConfig`` from the live settings DB (DB > env > default).

    The boot *fallback* supplies the strategy discriminator (which is not a
    hot knob) and field validation bounds. Constructing a fresh config
    revalidates the resolved scalars against the model's field constraints.

    Returns:
        ``CharterConfig`` instance.
    """
    ns = SettingNamespace.CHARTER
    return CharterConfig(
        interview_strategy=fallback.interview_strategy,
        interview_model=await resolver.get_str(ns, "interview_model"),
        interview_temperature=await resolver.get_float(ns, "interview_temperature"),
        interview_max_tokens=await resolver.get_int(ns, "interview_max_tokens"),
        interview_max_turns=await resolver.get_int(ns, "interview_max_turns"),
        default_currency=await resolver.get_str(ns, "default_currency"),
    )


def _charter_config_provider(
    app_state: AppState, *, fallback: CharterConfig
) -> Callable[[], Awaitable[CharterConfig]]:
    """Build the per-turn live-config provider for the interview service.

    The resolver is read from the slice on each call (late-bound: it may be
    wired after this builder runs), and an unwired resolver yields the boot
    *fallback*.

    Returns:
        An async callable resolving the live ``CharterConfig``.
    """

    async def _provide() -> CharterConfig:
        resolver = app_state.slice(SettingsStateSlice).config_resolver
        if resolver is None:
            return fallback
        return await _resolve_live_charter_config(resolver, fallback=fallback)

    return _provide


async def _wire_charter_engine(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTrackerProtocol | None,
    si_config: SelfImprovementConfig,
) -> None:
    """Wire the deep CEO-interview charter engine behind a provider + persistence."""
    from synthorg.meta.charter.state import CharterStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    if app_state.slice(CharterStateSlice).interview_service is not None:
        return
    if (
        provider_registry is None
        or persistence is None
        or app_state.slice(PersistenceStateSlice).backend is None
    ):
        return
    try:
        interview_service = await _build_charter_interview(
            app_state,
            provider_registry=provider_registry,
            persistence=persistence,
            cost_tracker=cost_tracker,
            si_config=si_config,
        )
        if interview_service is None:
            return
        # Partial-wire onto the existing slice rather than swapping a fresh
        # instance, so a dispatcher an earlier pass attached is preserved.
        app_state.wire(CharterStateSlice, interview_service=interview_service)
        logger.info(API_APP_STARTUP, service="charter_engine", note="wired")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            CHARTER_SUBSTRATE_UNAVAILABLE,
            note="charter wiring raised; charter endpoints stay unavailable",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _build_charter_interview(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry,
    persistence: PersistenceBackend,
    cost_tracker: CostTrackerProtocol | None,
    si_config: SelfImprovementConfig,
) -> CharterInterviewService | None:
    """Build the charter interview service.

    Returns:
        The service, or ``None`` when the stores / provider are unavailable
        (e.g. before setup completes).
    """
    from synthorg.meta.charter.factory import (  # noqa: PLC0415
        build_charter_interview_strategy,
    )
    from synthorg.meta.charter.service import (  # noqa: PLC0415
        CharterInterviewService,
    )
    from synthorg.persistence.charter_factory import (  # noqa: PLC0415
        build_charter_repository,
    )
    from synthorg.persistence.conversational_factory import (  # noqa: PLC0415
        build_conversational_repositories,
    )

    # The charter interview is always available -- it is user-initiated and
    # harmless, gated only on the provider + persistence the product always
    # requires. It wires whenever those are present (i.e. post-setup).
    charter_config = si_config.charter
    charter_repo = build_charter_repository(persistence)
    conv_repos = build_conversational_repositories(persistence)
    provider = resolve_feature_provider(
        provider_registry,
        charter_config.interview_model,
        feature="charter_interview",
    )
    if charter_repo is None or conv_repos is None or provider is None:
        logger.warning(
            CHARTER_SUBSTRATE_UNAVAILABLE,
            note="charter interview stores/provider unavailable (pre-setup?)",
        )
        return None
    # The interviewer re-reads ``charter.interview_model`` on every turn, so it
    # takes the whole registry rather than one client: reassigning the pair
    # arms the next turn instead of the next boot, and the pre-check above
    # only decides whether the feature comes up at all.
    strategy = build_charter_interview_strategy(
        charter_config,
        connections=provider_registry.get,
        cost_tracker=cost_tracker,
    )
    return CharterInterviewService(
        strategy=strategy,
        config=charter_config,
        conversation_repo=conv_repos.conversation_repo,
        turn_repo=conv_repos.turn_repo,
        charter_repo=charter_repo,
        config_provider=_charter_config_provider(app_state, fallback=charter_config),
    )


def _budget_currency_provider(
    app_state: AppState, *, fallback: BudgetConfig
) -> Callable[[], str]:
    """Build the per-approval live reader for the envelope's currency.

    Read per call, not captured: the attachment that builds this returns early
    once a dispatcher exists, so it never runs again, and a captured value
    would pin every future approval to the currency that happened to be
    configured at boot. That the collaborator asks for a callable rather than
    a string is the signal it expects a live read.

    Returns:
        A callable yielding the operator's current currency, falling back to
        the *fallback* present when the dispatcher was attached.
    """

    def _provide() -> str:
        from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415

        live = app_state.slice(BudgetStateSlice).budget_config
        return (live or fallback).currency

    return _provide


async def _build_charter_dispatcher(app_state: AppState) -> CharterDispatcher:
    """Build the approve path from the collaborators it needs.

    Returns:
        The dispatcher, bound to live readers for the two things that change
        under it while the process runs: the work-pipeline spine and the
        operator's currency.

    Raises:
        SubsystemDeclinedError: Naming which collaborator is absent, so
            ``GET /subsystems`` answers "why can this deployment not approve a
            charter" rather than pointing at a wiring log.
    """
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        live_work_pipeline,
    )
    from synthorg.meta.charter.dispatch import CharterDispatcher  # noqa: PLC0415
    from synthorg.meta.charter.state import CharterStateSlice  # noqa: PLC0415
    from synthorg.persistence.charter_factory import (  # noqa: PLC0415
        build_charter_repository,
    )
    from synthorg.persistence.conversational_factory import (  # noqa: PLC0415
        build_conversational_repositories,
    )
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    persistence = app_state.slice(PersistenceStateSlice).backend
    if persistence is None:
        msg = "no persistence backend; the dispatcher provisions a project row"
        raise SubsystemDeclinedError(msg)
    if app_state.slice(CharterStateSlice).interview_service is None:
        msg = "no charter interview service; the dispatcher approves what it drafts"
        raise SubsystemDeclinedError(msg)
    if app_state.slice(EngineStateSlice).work_pipeline is None:
        msg = "no work pipeline; an approved charter becomes an objective on it"
        raise SubsystemDeclinedError(msg)
    budget_slice = app_state.slice(BudgetStateSlice)
    forecast_repo = budget_slice.cost_forecast_repo
    budget_config = budget_slice.budget_config
    if forecast_repo is None:
        msg = "no cost-forecast store; approval releases a forecast with the plan"
        raise SubsystemDeclinedError(msg)
    if budget_config is None:
        msg = "no budget config; the approval envelope is denominated from it"
        raise SubsystemDeclinedError(msg)
    charter_repo = build_charter_repository(persistence)
    conv_repos = build_conversational_repositories(persistence)
    if charter_repo is None or conv_repos is None:
        msg = "charter or conversational stores unavailable on this backend"
        raise SubsystemDeclinedError(msg)
    return CharterDispatcher(
        charter_repo=charter_repo,
        forecast_repo=forecast_repo,
        project_repo=persistence.projects,
        work_pipeline=live_work_pipeline(app_state),
        conversation_repo=conv_repos.conversation_repo,
        budget_currency=_budget_currency_provider(app_state, fallback=budget_config),
    )


async def attach_charter_dispatcher(app_state: AppState) -> None:
    """Attach the approve path onto the already-wired charter engine.

    The ``charter_dispatch`` subsystem's ``activate``. Its own dependencies are
    the work pipeline (an approved charter becomes an objective on it), the
    cost-forecast store and the budget config, all of which arrive with the
    runtime services after the interview service is up.

    Raises:
        SubsystemDeclinedError: Naming which collaborator is absent.
    """
    from synthorg.meta.charter.state import CharterStateSlice  # noqa: PLC0415

    if app_state.slice(CharterStateSlice).dispatcher is not None:
        return
    dispatcher = await _build_charter_dispatcher(app_state)
    # Partial-wire so the interview service the other subsystem owns is kept.
    app_state.wire(CharterStateSlice, dispatcher=dispatcher)
    logger.info(API_APP_STARTUP, service="charter_dispatch", note="attached")
