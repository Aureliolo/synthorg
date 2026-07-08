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
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.api.state import AppState


def wire_conflict_resolution_service(  # noqa: PLR0913 -- keyword-only collaborator DI
    app_state: AppState,
    *,
    effective_config: RootConfig,
    config: ConflictResolutionConfig,
    message_bus: MessageBus | None,
    escalation_store: EscalationQueueStore,
    escalation_processor: DecisionProcessor,
    escalation_registry: PendingFuturesRegistry,
    provider_registry: ProviderRegistry | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
) -> None:
    """Build + install the conflict-resolution service on the comms slice.

    The hierarchy is built from the boot-time company snapshot (same
    lifecycle as the escalation wiring), and the human resolver reuses the
    escalation store/processor/registry already wired. When a provider is
    registered, an :class:`LlmJudgeEvaluator` is built and shared by the
    debate and hybrid resolvers so their auto-resolution arms are live.
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
        judge_evaluator=_build_judge_evaluator(provider_registry, cost_tracker),
    )
    app_state.wire(
        CommunicationStateSlice,
        conflict_resolution_service=service,
    )


def _build_judge_evaluator(
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTrackerProtocol | None,
) -> JudgeEvaluator | None:
    """Build the LLM judge from the provider serving the pinned model.

    The judge is a system actor, not a company agent, so it resolves its
    provider by the pinned ``CONFLICT_JUDGE`` model through the shared
    model-aware helper (:func:`resolve_feature_provider`). A naive
    first-registered pick would route the call to a driver that does not serve
    the provider-agnostic pinned model once more than one provider is
    registered, surfacing as a request-time model-not-found error.

    Returns:
        A wired :class:`LlmJudgeEvaluator`, or ``None`` when no registered
        provider serves the pinned model (the debate/hybrid resolvers then
        fall back to authority).
    """
    if provider_registry is None:
        return None
    from synthorg.api._feature_provider_resolution import (  # noqa: PLC0415
        resolve_feature_provider,
    )
    from synthorg.communication.conflict_resolution.llm_judge_evaluator import (  # noqa: PLC0415
        LlmJudgeEvaluator,
    )
    from synthorg.llm.model_pins import pin_for  # noqa: PLC0415
    from synthorg.llm.prompt_purpose import PromptPurposeId  # noqa: PLC0415

    model = pin_for(PromptPurposeId.CONFLICT_JUDGE).model
    provider = resolve_feature_provider(
        provider_registry,
        model,
        feature="conflict_judge",
    )
    if provider is None:
        return None
    return LlmJudgeEvaluator(
        provider=provider,
        model=model,
        cost_tracker=cost_tracker,
    )


__all__ = ["wire_conflict_resolution_service"]
