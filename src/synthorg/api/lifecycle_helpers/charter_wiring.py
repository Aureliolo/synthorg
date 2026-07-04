# module-kind: orchestrator
"""On-startup wiring for the deep CEO-interview charter engine.

Split out of :mod:`feature_wiring` so each module stays within its
module-size tier. ``_wire_charter_engine`` is called from
``wire_features_on_startup`` in dependency order; it is best-effort +
idempotent (an already-wired interview service short-circuits) and a
missing collaborator leaves the charter controllers to 503 rather than
poisoning startup.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from synthorg.api._feature_provider_resolution import resolve_feature_provider
from synthorg.api.state import AppState
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
    from synthorg.persistence.charter_protocol import CharterRepository
    from synthorg.persistence.conversational_factory import (
        ConversationalRepositories,
    )

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
        built = await _build_charter_interview(
            app_state,
            provider_registry=provider_registry,
            persistence=persistence,
            cost_tracker=cost_tracker,
            si_config=si_config,
        )
        if built is None:
            return
        interview_service, charter_repo, conv_repos = built
        app_state.swap_slice(CharterStateSlice(interview_service=interview_service))
        dispatcher = _build_charter_dispatcher(
            app_state,
            persistence=persistence,
            charter_repo=charter_repo,
            conv_repos=conv_repos,
        )
        if dispatcher is None:
            return
        # Partial-wire the dispatcher onto the already-published slice rather
        # than swapping a fresh instance: this preserves interview_service and
        # means a failure here cannot wipe it back to None (the slice stays in
        # the interview-only degraded state instead of becoming inconsistent).
        app_state.wire(CharterStateSlice, dispatcher=dispatcher)
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
) -> (
    tuple[CharterInterviewService, CharterRepository, ConversationalRepositories] | None
):
    """Build the charter interview service plus its stores.

    Returns:
        ``(interview_service, charter_repo, conv_repos)``, or ``None`` when the
        stores / provider are unavailable (e.g. before setup completes).
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
    strategy = build_charter_interview_strategy(
        charter_config,
        provider=provider,
        cost_tracker=cost_tracker,
    )
    interview_service = CharterInterviewService(
        strategy=strategy,
        config=charter_config,
        conversation_repo=conv_repos.conversation_repo,
        turn_repo=conv_repos.turn_repo,
        charter_repo=charter_repo,
        config_provider=_charter_config_provider(app_state, fallback=charter_config),
    )
    return interview_service, charter_repo, conv_repos


def _build_charter_dispatcher(
    app_state: AppState,
    *,
    persistence: PersistenceBackend,
    charter_repo: CharterRepository,
    conv_repos: ConversationalRepositories,
) -> CharterDispatcher | None:
    """Build the charter dispatcher (the approve path).

    Returns:
        The wired ``CharterDispatcher``, or ``None`` when the work pipeline /
        forecast repo / budget config is absent (approve stays 503).
    """
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        work_pipeline_of,
    )
    from synthorg.meta.charter.dispatch import CharterDispatcher  # noqa: PLC0415

    budget_slice = app_state.slice(BudgetStateSlice)
    forecast_repo = budget_slice.cost_forecast_repo
    budget_config = budget_slice.budget_config
    if (
        app_state.slice(EngineStateSlice).work_pipeline is None
        or forecast_repo is None
        or budget_config is None
    ):
        logger.warning(
            CHARTER_SUBSTRATE_UNAVAILABLE,
            note="charter dispatcher deps absent; approve will 503",
        )
        return None
    resolved_budget = budget_config
    return CharterDispatcher(
        charter_repo=charter_repo,
        forecast_repo=forecast_repo,
        project_repo=persistence.projects,
        work_pipeline=work_pipeline_of(app_state),
        conversation_repo=conv_repos.conversation_repo,
        budget_currency=lambda: resolved_budget.currency,
    )
