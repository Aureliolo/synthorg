"""Meta feature state slice.

Holds the self-improvement meta-loop services: signals, experiments,
the self-improvement service, reports + analytics services, and the
Chief of Staff proposer. All are wired lazily once persistence (and,
for the proposer, a provider) are available; readers guard on a
``None`` field.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.experiments import ExperimentService
from synthorg.meta.analytics.service import AnalyticsService  # noqa: TC001
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat  # noqa: TC001
from synthorg.meta.chief_of_staff.propose import (
    ChiefOfStaffProposer,  # noqa: TC001
)
from synthorg.meta.reports.service import ReportsService  # noqa: TC001
from synthorg.meta.service import SelfImprovementService  # noqa: TC001
from synthorg.meta.signals.service import SignalsService  # noqa: TC001
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalRepository,  # noqa: TC001
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.api.state_slices import AppStateSliceMixin


class MetaStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the meta feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signals_service: SignalsService | None = None
    experiment_service: ExperimentService | None = None
    self_improvement_service: SelfImprovementService | None = None
    reports_service: ReportsService | None = None
    analytics_service: AnalyticsService | None = None
    chief_of_staff_proposer: ChiefOfStaffProposer | None = None
    chief_of_staff_chat: ChiefOfStaffChat | None = None
    conversational_proposal_repo: ConversationalProposalRepository | None = None


def signals_service_of(app_state: AppStateSliceMixin) -> SignalsService:
    """Resolve the signals service from its slice, or raise 503.

    Returns:
        The wired signals service.
    """
    return require_service(
        app_state.slice(MetaStateSlice).signals_service, "Signals Service"
    )


def experiment_service_of(app_state: AppState) -> ExperimentService:
    """Resolve the A/B experiment service, wiring the in-memory default.

    Returns the durable service when one was installed at startup;
    otherwise lazily composes an in-memory-backed service so the
    ``/experiments`` controller works in dev / smoke-test runs without a
    persistence backend.

    Returns:
        The wired or lazily-composed experiment service.
    """
    existing = app_state.slice(MetaStateSlice).experiment_service
    if existing is not None:
        return existing
    from synthorg.experiments.in_memory_repository import (  # noqa: PLC0415
        InMemoryExperimentRepository,
    )

    # Concurrent first-readers race here; ``wire_if_field_absent`` makes
    # the check + install atomic so they all converge on one shared
    # ``InMemoryExperimentRepository`` rather than each composing their
    # own and losing experiments registered against the discarded copies.
    candidate = ExperimentService(
        repository=InMemoryExperimentRepository(),
        clock=app_state.clock,
    )
    app_state.wire_if_field_absent(MetaStateSlice, "experiment_service", candidate)
    return app_state.slice(MetaStateSlice).experiment_service or candidate


def self_improvement_service_of(
    app_state: AppStateSliceMixin,
) -> SelfImprovementService:
    """Resolve the self-improvement service from its slice, or raise 503.

    Returns:
        The wired self-improvement service.
    """
    return require_service(
        app_state.slice(MetaStateSlice).self_improvement_service,
        "Self-Improvement Service",
    )


def analytics_service_of(app_state: AppStateSliceMixin) -> AnalyticsService:
    """Resolve the analytics service from its slice, or raise 503.

    Returns:
        The wired analytics service.
    """
    return require_service(
        app_state.slice(MetaStateSlice).analytics_service, "Analytics Service"
    )


def reports_service_of(app_state: AppStateSliceMixin) -> ReportsService:
    """Resolve the reports service from its slice, or raise 503.

    Returns:
        The wired reports service.
    """
    return require_service(
        app_state.slice(MetaStateSlice).reports_service, "Reports Service"
    )


def chief_of_staff_chat_of(app_state: AppStateSliceMixin) -> ChiefOfStaffChat:
    """Resolve the Chief of Staff chat backend from its slice, or raise 503.

    Returns:
        The wired Chief of Staff chat backend.
    """
    return require_service(
        app_state.slice(MetaStateSlice).chief_of_staff_chat, "Chief of Staff Chat"
    )


def chief_of_staff_proposer_of(
    app_state: AppStateSliceMixin,
) -> ChiefOfStaffProposer:
    """Resolve the Chief of Staff proposer from its slice, or raise 503.

    Returns:
        The wired Chief of Staff proposer.
    """
    return require_service(
        app_state.slice(MetaStateSlice).chief_of_staff_proposer,
        "Chief of Staff Proposer",
    )


def conversational_proposal_repo_of(
    app_state: AppStateSliceMixin,
) -> ConversationalProposalRepository:
    """Resolve the conversational proposal repo from its slice, or raise 503.

    Returns:
        The wired conversational proposal repository.
    """
    return require_service(
        app_state.slice(MetaStateSlice).conversational_proposal_repo,
        "Conversational Proposal Repository",
    )
