"""Boot-time builder for the adversarial red-team subsystem.

Single factory function callers (the workers runtime builder) invoke
once at startup. Returns ``None`` when
:attr:`SecurityConfig.red_team.enabled` is ``False`` (the opt-in
default), otherwise returns a :class:`RedTeamRuntime` carrying:

* the :class:`SubmitRedTeamReportTool` instance to register on the
  agent engine's tool registry,
* the gate service the review-gate consumes,
* the in-memory report repository,
* the production :class:`AgentEngineRunner` that wraps the engine.

The function is the single ENFORCED construction site for every
red-team symbol in the ghost-wiring manifest.
"""

from typing import TYPE_CHECKING, Literal, NamedTuple

from synthorg.core.clock import Clock
from synthorg.observability import get_logger
from synthorg.observability.events.red_team import (
    RED_TEAM_GATE_BUILD_FAILED,
    RED_TEAM_GATE_SKIPPED,
)
from synthorg.security.redteam.agent import build_red_team_agent_identity
from synthorg.security.redteam.errors import RedTeamRuntimeSeedIncompleteError
from synthorg.security.redteam.gate import RedTeamGateService
from synthorg.security.redteam.grounding.factory import build_grounding_checker
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository
from synthorg.security.redteam.runner import AgentEngineRunner
from synthorg.security.redteam.tools.submit_report import SubmitRedTeamReportTool
from synthorg.tools.base import BaseTool

if TYPE_CHECKING:
    from synthorg.core.agent import ModelConfig
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.persistence.red_team_report_protocol import (
        RedTeamReportArchiveRepository,
    )
    from synthorg.security.config import RedTeamConfig

logger = get_logger(__name__)


class RedTeamToolSeed(NamedTuple):
    """Early-boot bundle: tool + repo to register on the engine.

    Built BEFORE the agent engine so the
    :class:`SubmitRedTeamReportTool` lands on the engine's tool
    registry at engine construction time. After the engine exists,
    :func:`build_red_team_runtime` consumes the same instances to
    build the gate, ensuring the tool the agent calls and the repo
    the gate reads are the SAME objects.

    When the gate is disabled, ``report_repo`` and ``submit_tool``
    are ``None`` and ``extra_tools`` is an empty tuple.
    """

    report_repo: InMemoryRedTeamReportRepository | None
    submit_tool: SubmitRedTeamReportTool | None
    extra_tools: tuple[BaseTool, ...]


def build_red_team_tool_seed(*, config: RedTeamConfig) -> RedTeamToolSeed:
    """Build the boot-phase-1 seed for the red-team tool + repo.

    The runtime-builder appends ``extra_tools`` to its config-driven
    tool list before constructing the agent engine, so the seed must be
    built first.

    Returns:
        A ``RedTeamToolSeed``; empty ``extra_tools`` (and ``None`` for
        repo / tool) when the gate is disabled.
    """
    if not config.enabled:
        return RedTeamToolSeed(
            report_repo=None,
            submit_tool=None,
            extra_tools=(),
        )
    report_repo = InMemoryRedTeamReportRepository()
    submit_tool = SubmitRedTeamReportTool(report_repo=report_repo)
    return RedTeamToolSeed(
        report_repo=report_repo,
        submit_tool=submit_tool,
        extra_tools=(submit_tool,),
    )


class RedTeamRuntime(NamedTuple):
    """The red-team subsystem's boot-time bundle.

    Attributes:
        submit_tool: Register this on the agent engine's tool registry
            at construction time so the red-team agent's tool calls
            resolve.
        gate: Inject this into :class:`ReviewGateService` so the gate
            fires at the IN_REVIEW -> COMPLETED transition.
        report_repo: Per-execution storage for the structured report.
            Same instance the gate reads from; same instance the tool
            writes to.
        runner: Production :class:`AgentRunner`. Wraps the boot
            :class:`AgentEngine` and the red-team :class:`AgentIdentity`.
        on_missing_deliverable: Security posture forwarded to the review
            gate for the case where a configured gate cannot retrieve a
            deliverable to inspect (``"block"`` fail-closed default).
    """

    submit_tool: SubmitRedTeamReportTool
    gate: RedTeamGateService
    report_repo: InMemoryRedTeamReportRepository
    runner: AgentEngineRunner
    on_missing_deliverable: Literal["block", "skip"]


def build_red_team_runtime(  # noqa: PLR0913 -- boot-time builder inputs, all required
    *,
    config: RedTeamConfig,
    engine: AgentEngine,
    model: ModelConfig,
    seed: RedTeamToolSeed,
    report_archive: RedTeamReportArchiveRepository | None = None,
    clock: Clock | None = None,
) -> RedTeamRuntime | None:
    """Build the red-team runtime if the feature is enabled.

    Args:
        config: The :class:`RedTeamConfig` slice of
            :class:`SecurityConfig`. ``enabled=False`` (default) returns
            ``None`` immediately.
        engine: Boot :class:`AgentEngine`. The runner delegates to its
            :meth:`AgentEngine.run`.
        model: :class:`ModelConfig` for the red-team agent identity.
            Operators pin the same provider / model the rest of the
            company uses, unless they want a separate red-team budget.
        seed: The boot-phase-1 :class:`RedTeamToolSeed` returned by
            :func:`build_red_team_tool_seed`. Its ``report_repo`` and
            ``submit_tool`` (built BEFORE the engine, so they land on
            the engine's tool registry at construction time) are reused
            here; the gate writes through the same repo the tool wrote
            to.
        report_archive: Optional durable cross-process archive for the
            merged report + verdict. Wired from the connected persistence
            backend; ``None`` in a persistence-less boot (archival then
            skipped). The gate's archive write is fail-OPEN.
        clock: Clock seam. Defaults to :class:`SystemClock` inside the
            gate when ``None``.

    Returns:
        :class:`RedTeamRuntime` on success, ``None`` when the gate is
        disabled. ``None`` is the safe default: the review gate's
        constructor accepts ``red_team_gate=None`` as "feature off".

    Raises:
        RedTeamRuntimeSeedIncompleteError: If ``config.enabled`` is True
            but the seed is missing its ``report_repo`` / ``submit_tool``.
    """
    if not config.enabled:
        logger.info(
            RED_TEAM_GATE_SKIPPED,
            reason="config.disabled",
            note="red_team.enabled is False; gate not constructed",
        )
        return None

    if seed.report_repo is None or seed.submit_tool is None:
        msg = (
            "build_red_team_runtime called with config.enabled=True but a "
            "seed missing report_repo / submit_tool. Build the seed via "
            "build_red_team_tool_seed(config=...) before the engine so the "
            "tool is registered on the engine's tool registry."
        )
        logger.error(
            RED_TEAM_GATE_BUILD_FAILED,
            reason="seed_incomplete",
            has_report_repo=seed.report_repo is not None,
            has_submit_tool=seed.submit_tool is not None,
            note=msg,
        )
        raise RedTeamRuntimeSeedIncompleteError(msg)

    grounding = build_grounding_checker(config.grounding_checker_kind)
    identity = build_red_team_agent_identity(model=model, clock=clock)
    runner = AgentEngineRunner(engine=engine, identity=identity)
    gate = RedTeamGateService(
        agent_runner=runner,
        report_repo=seed.report_repo,
        grounding_checker=grounding,
        report_archive=report_archive,
        clock=clock,
    )
    return RedTeamRuntime(
        submit_tool=seed.submit_tool,
        gate=gate,
        report_repo=seed.report_repo,
        runner=runner,
        on_missing_deliverable=config.on_missing_deliverable,
    )
