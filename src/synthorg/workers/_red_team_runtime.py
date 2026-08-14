"""Red-team runtime construction for the worker boot path.

Split out of :mod:`runtime_builder` so that orchestrator stays focused on
the overall worker/coordinator wiring. Builds the optional
:class:`RedTeamRuntime` from the company's security config, binding the
agent to the ``(provider, model)`` pair the operator chose for it and
sourcing the durable report archive from the connected persistence backend.

It also builds the lazy :data:`GroundingSubstrateResolver` closure passed
to the runtime. The substrate-backed grounding checker is constructed here
(at ``_install_runtime_services`` time) BEFORE the knowledge substrate
wires in ``_wire_knowledge_engine``, so the checker cannot capture the
service by value. The closure reads the live application state on every
``check()`` instead: it resolves the current provider registry, the wired
``KnowledgeService`` (``None`` until it wires), and the cost tracker, and
returns ``None`` when no provider is available so the checker degrades to
the heuristic.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from synthorg.budget.state import BudgetStateSlice
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.hr.role_staffing import RoleStaffingService
from synthorg.hr.state import agent_registry_of
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.red_team import (
    RED_TEAM_GROUNDING_MODEL_UNSET,
    RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
)
from synthorg.persistence.state import project_repository_of, red_team_reports_of
from synthorg.providers.state import ProvidersStateSlice
from synthorg.security.redteam.builder import (
    RedTeamRuntime,
    RedTeamToolSeed,
    build_red_team_runtime,
)
from synthorg.security.redteam.grounding.resolver import GroundingSubstrateContext
from synthorg.settings.bound_model import resolve_bound_model
from synthorg.settings.model_ref import ModelRef

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


def _build_grounding_substrate_resolver(
    app_state: AppState,
    *,
    model: ModelRef,
) -> Callable[[], GroundingSubstrateContext | None]:
    """Build the lazy resolver for the substrate grounding checker.

    The returned closure reads the live application state each time the
    checker calls it, so a provider hot-swap or the deferred knowledge
    wiring is picked up without rebuilding the checker.

    Returns:
        A no-argument callable resolving the live substrate dependencies,
        or ``None`` when the bound provider connection is not registered.
    """

    def _resolve() -> GroundingSubstrateContext | None:
        registry = app_state.slice(ProvidersStateSlice).registry
        if registry is None:
            return None
        if model.provider not in registry.list_providers():
            # The connection the operator bound is gone (hot-swapped away). Do
            # NOT fall back to another one: a provider is a distinct connection
            # with its own credentials and endpoint, so substituting it would
            # bill and route somewhere nobody chose. The checker stays inert.
            logger.warning(
                RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
                reason="configured_provider_absent",
                configured_provider=model.provider,
            )
            return None
        return GroundingSubstrateContext(
            knowledge_service=app_state.slice(KnowledgeStateSlice).service,
            provider=registry.get(model.provider),
            model_id=NotBlankStr(model.model_id),
            cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
        )

    return _resolve


async def build_red_team_runtime_or_none(
    *,
    app_state: AppState,
    engine: AgentEngine,
    seed: RedTeamToolSeed,
) -> RedTeamRuntime | None:
    """Construct the red-team runtime when the gate is enabled.

    Pulls :class:`RedTeamConfig` from ``app_state.config.security.red_team``.
    The adversary itself is no longer bound here: the gate selects a roster
    holder of the ``Red Team`` role per evaluation, and that agent runs on
    its own operator-chosen pair. "Unarmed because no adversary model is set"
    has stopped existing as a state; "unarmed because nobody holds the role"
    replaces it, and that one is visible in the roster.

    The ``seed`` parameter carries the per-boot
    :class:`InMemoryRedTeamReportRepository` and
    :class:`SubmitRedTeamReportTool` already registered on the engine's
    tool registry, so the runtime shares those instances rather than
    constructing fresh ones.

    The durable report archive is sourced from the connected persistence
    backend (``None`` in a persistence-less boot) so the gate's
    fail-OPEN archival write lands in the cross-process audit table the
    flight-recorder read surface consumes. The lazy grounding-substrate
    resolver is threaded so the optional substrate-backed checker can
    resolve the knowledge service that wires after this hook runs; its own
    ``(provider, model)`` pair is a separate dispatch and reads its own
    ``security.grounding_model`` setting.

    Returns:
        The ``RedTeamRuntime`` when the gate is enabled, otherwise
        ``None``.
    """
    config = app_state.config.security.red_team
    # Gate first, resolve second. The other order reports an unbound
    # grounding checker on every boot and every runtime reload of a
    # deployment that never turned the gate on, which is not a
    # misconfiguration.
    if not config.enabled:
        return None
    grounding_model = await resolve_bound_model(
        app_state,
        namespace="security",
        key="grounding_model",
        unset_event=RED_TEAM_GROUNDING_MODEL_UNSET,
    )
    return build_red_team_runtime(
        config=config,
        engine=engine,
        staffing=RoleStaffingService(registry=agent_registry_of(app_state)),
        seed=seed,
        project_repo=project_repository_of(app_state),
        report_archive=red_team_reports_of(app_state),
        clock=app_state.clock,
        # ``None`` leaves the substrate checker without a dispatch target, so
        # grounding degrades to the heuristic. That degradation is the
        # existing documented one; it no longer disarms the whole gate.
        grounding_substrate_resolver=(
            None
            if grounding_model is None
            else _build_grounding_substrate_resolver(app_state, model=grounding_model)
        ),
    )
