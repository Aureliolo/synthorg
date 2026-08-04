"""Construction-phase wiring for the conflict-resolution service.

Extracted from :mod:`synthorg.api.construction_phase` to keep that
orchestrator under its size budget. Builds the boot-time company snapshot,
hands it to the conflict-resolution factory, and installs the resulting
service on the communication slice. Lives in the api layer because it
touches :class:`AppState`; the resolver-assembly knowledge stays in
:mod:`synthorg.communication.conflict_resolution.factory`.
"""

from typing import TYPE_CHECKING

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.conflict_resolution.config import (
    ConflictResolutionConfig,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    DecisionProcessor,
    EscalationQueueStore,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.communication.conflict_resolution.protocol import JudgeEvaluator
from synthorg.config.schema import RootConfig
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


def wire_conflict_resolution_service(  # noqa: PLR0913 -- keyword-only collaborator DI
    app_state: AppState,
    *,
    effective_config: RootConfig,
    config: ConflictResolutionConfig,
    message_bus: MessageBus | None,
    escalation_store: EscalationQueueStore,
    escalation_processor: DecisionProcessor,
    escalation_registry: PendingFuturesRegistry,
    cost_tracker: CostTrackerProtocol | None = None,
    config_resolver: ConfigResolverProtocol | None = None,
) -> None:
    """Build + install the conflict-resolution service on the comms slice.

    The hierarchy is built from the boot-time company snapshot (same
    lifecycle as the escalation wiring), and the human resolver reuses the
    escalation store/processor/registry already wired. An
    :class:`LlmJudgeEvaluator` is shared by the debate and hybrid resolvers
    so their auto-resolution arms are live.
    """
    from synthorg.communication.conflict_resolution.factory import (  # noqa: PLC0415
        build_conflict_resolution_service,
    )
    from synthorg.communication.state import CommunicationStateSlice  # noqa: PLC0415
    from synthorg.core.company import Company  # noqa: PLC0415

    company = Company(
        name=effective_config.company_name,
        type=effective_config.company_type,
        departments=effective_config.departments,
        config=effective_config.config,
        workflow_handoffs=effective_config.workflow_handoffs,
        escalation_paths=effective_config.escalation_paths,
    )
    service = build_conflict_resolution_service(
        config=config,
        company=company,
        escalation_store=escalation_store,
        escalation_processor=escalation_processor,
        escalation_registry=escalation_registry,
        event_hub=app_state.slice(CommunicationStateSlice).event_stream_hub,
        message_bus=message_bus,
        judge_evaluator=_build_judge_evaluator(
            app_state, cost_tracker, config_resolver
        ),
    )
    app_state.wire(
        CommunicationStateSlice,
        conflict_resolution_service=service,
    )


def _build_judge_evaluator(
    app_state: AppState,
    cost_tracker: CostTrackerProtocol | None,
    config_resolver: ConfigResolverProtocol | None,
) -> JudgeEvaluator:
    """Build the LLM judge over its own operator-chosen connection.

    The judge names its pair through ``communication.conflict_judge_model``
    and re-reads it per judgement, so what it holds is a *selector* over the
    live registry rather than one client captured at boot: an operator
    reassigning the judge arms the next arbitration instead of the next
    boot, and a provider reload that swaps the registry does not strand it
    on a dead one. An unset pair, or a registry that is not wired yet,
    raises at the call, where the debate/hybrid resolvers fall back to
    authority.

    Returns:
        A wired :class:`LlmJudgeEvaluator`.
    """
    from synthorg.communication.conflict_resolution.llm_judge_evaluator import (  # noqa: PLC0415
        LlmJudgeEvaluator,
    )
    from synthorg.providers.state import provider_registry_of  # noqa: PLC0415

    def _connection(name: str) -> CompletionProvider:
        """Resolve *name* against the registry wired right now.

        Returns:
            The registered completion client.
        """
        return provider_registry_of(app_state).get(name)

    logger.info(
        API_APP_STARTUP,
        service="conflict_judge",
        note="judge resolves its (provider, model) pair per judgement",
    )
    return LlmJudgeEvaluator(
        connections=_connection,
        cost_tracker=cost_tracker,
        config_resolver=config_resolver,
    )


__all__ = ["wire_conflict_resolution_service"]
