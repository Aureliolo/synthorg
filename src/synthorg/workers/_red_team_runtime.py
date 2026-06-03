"""Red-team runtime construction for the worker boot path.

Split out of :mod:`runtime_builder` so that orchestrator stays focused on
the overall worker/coordinator wiring. Builds the optional
:class:`RedTeamRuntime` from the company's security config, pinning the
agent to the active provider and sourcing the durable report archive from
the connected persistence backend.
"""

from typing import TYPE_CHECKING

from synthorg.engine.agent_engine import AgentEngine
from synthorg.persistence.state import red_team_reports_of
from synthorg.security.redteam.builder import (
    RedTeamRuntime,
    RedTeamToolSeed,
    build_red_team_runtime,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState


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
    flight-recorder read surface consumes.

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
            model_id="example-medium-001",
        ),
        seed=seed,
        report_archive=red_team_reports_of(app_state),
        clock=app_state.clock,
    )
