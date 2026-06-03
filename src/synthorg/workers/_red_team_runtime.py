"""Red-team runtime construction for the worker boot path.

Split out of :mod:`runtime_builder` so that orchestrator stays focused on
the overall worker/coordinator wiring. Builds the optional
:class:`RedTeamRuntime` from the company's security config, pinning the
agent to the active provider and sourcing the durable report archive from
the connected persistence backend.

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
from typing import TYPE_CHECKING, Final

from synthorg.budget.state import BudgetStateSlice
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.red_team import (
    RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
)
from synthorg.persistence.state import red_team_reports_of
from synthorg.providers.state import ProvidersStateSlice
from synthorg.security.redteam.builder import (
    RedTeamRuntime,
    RedTeamToolSeed,
    build_red_team_runtime,
)
from synthorg.security.redteam.grounding.resolver import GroundingSubstrateContext

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_GROUNDING_MODEL_ID: Final[str] = "example-medium-001"
"""Vendor-agnostic model id for the grounding checker's LLM calls.

Mirrors the red-team agent identity's model; operators override via the
post-init provider swap path.
"""


def _build_grounding_substrate_resolver(
    app_state: AppState,
    *,
    provider_name: str,
) -> Callable[[], GroundingSubstrateContext | None]:
    """Build the lazy resolver for the substrate grounding checker.

    The returned closure reads the live application state each time the
    checker calls it, so a provider hot-swap or the deferred knowledge
    wiring is picked up without rebuilding the checker.

    Returns:
        A no-argument callable resolving the live substrate dependencies,
        or ``None`` when no provider is registered.
    """

    def _resolve() -> GroundingSubstrateContext | None:
        registry = app_state.slice(ProvidersStateSlice).registry
        if registry is None:
            return None
        available = registry.list_providers()
        if not available:
            return None
        if provider_name in available:
            name = provider_name
        else:
            name = available[0]
            logger.warning(
                RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
                reason="configured_provider_absent",
                configured_provider=provider_name,
                fallback_provider=name,
            )
        return GroundingSubstrateContext(
            knowledge_service=app_state.slice(KnowledgeStateSlice).service,
            provider=registry.get(name),
            model_id=NotBlankStr(_GROUNDING_MODEL_ID),
            cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
        )

    return _resolve


def build_red_team_runtime_or_none(
    *,
    app_state: AppState,
    engine: AgentEngine,
    provider_name: str,
    seed: RedTeamToolSeed,
) -> RedTeamRuntime | None:
    """Construct the red-team runtime when the gate is enabled.

    Pulls :class:`RedTeamConfig` from ``app_state.config.security.red_team``
    and pins the red-team agent's :class:`ModelConfig` to the company's
    active provider with the vendor-agnostic ``example-medium-001``
    model id; operators override via the post-init swap path. The
    ``seed`` parameter carries the per-boot
    :class:`InMemoryRedTeamReportRepository` and
    :class:`SubmitRedTeamReportTool` already registered on the engine's
    tool registry, so the runtime shares those instances rather than
    constructing fresh ones.

    The durable report archive is sourced from the connected persistence
    backend (``None`` in a persistence-less boot) so the gate's
    fail-OPEN archival write lands in the cross-process audit table the
    flight-recorder read surface consumes. The lazy grounding-substrate
    resolver is threaded so the optional substrate-backed checker can
    resolve the knowledge service that wires after this hook runs.

    Returns:
        The ``RedTeamRuntime`` when the gate is enabled, otherwise
        ``None``.
    """
    from synthorg.core.agent import ModelConfig  # noqa: PLC0415

    return build_red_team_runtime(
        config=app_state.config.security.red_team,
        engine=engine,
        model=ModelConfig(
            provider=provider_name,
            model_id=_GROUNDING_MODEL_ID,
        ),
        seed=seed,
        report_archive=red_team_reports_of(app_state),
        clock=app_state.clock,
        grounding_substrate_resolver=_build_grounding_substrate_resolver(
            app_state,
            provider_name=provider_name,
        ),
    )
