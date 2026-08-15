# module-kind: service
"""Boot-time builder for the completion-oracle peer-review subsystem.

Two-phase, mirroring the red-team builder for the same reason: the submit
tool must land on the agent engine's tool registry BEFORE the engine is
constructed, so :func:`build_completion_oracle_tool_seed` runs first and
:func:`build_completion_oracle_runtime` consumes the same instances after the
engine exists (the tool the reviewer calls and the repo the gate reads are the
SAME objects).
"""

from typing import TYPE_CHECKING, NamedTuple

from synthorg.core.clock import Clock
from synthorg.core.task_enums import Stakes
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.completion_oracle.config import CompletionOracleConfig
from synthorg.engine.completion_oracle.errors import (
    CompletionOracleRuntimeSeedIncompleteError,
)
from synthorg.engine.completion_oracle.gate import CompletionOracleGateService
from synthorg.engine.completion_oracle.protocol import CompletionOracleReportRepository
from synthorg.engine.completion_oracle.report_repo import (
    InMemoryCompletionOracleReportRepository,
)
from synthorg.engine.completion_oracle.runner import ReviewerAgentEngineRunner
from synthorg.engine.completion_oracle.tools.submit_verdict import (
    SubmitCompletionOracleVerdictTool,
)
from synthorg.hr.role_staffing import RoleStaffingService
from synthorg.observability import get_logger
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_GATE_BUILD_FAILED,
    COMPLETION_ORACLE_GATE_SKIPPED,
)
from synthorg.tools.base import BaseTool

if TYPE_CHECKING:
    from synthorg.persistence.completion_oracle_report_protocol import (
        CompletionOracleReportArchiveRepository,
    )
    from synthorg.persistence.task_protocol import TaskRepository

logger = get_logger(__name__)


class CompletionOracleToolSeed(NamedTuple):
    """Early-boot bundle: tool + repo to register on the engine.

    Built BEFORE the agent engine so the submit tool lands on the engine's
    tool registry at construction time. When the gate is disabled, all fields
    are empty / ``None``.
    """

    report_repo: CompletionOracleReportRepository | None
    submit_tool: SubmitCompletionOracleVerdictTool | None
    extra_tools: tuple[BaseTool, ...]


def build_completion_oracle_tool_seed(
    *, config: CompletionOracleConfig
) -> CompletionOracleToolSeed:
    """Build the construction-phase boot seed for the verdict tool + repo.

    Returns:
        A ``CompletionOracleToolSeed``; empty when the gate is disabled.
    """
    if not config.enabled:
        return CompletionOracleToolSeed(
            report_repo=None, submit_tool=None, extra_tools=()
        )
    report_repo = InMemoryCompletionOracleReportRepository()
    submit_tool = SubmitCompletionOracleVerdictTool(report_repo=report_repo)
    return CompletionOracleToolSeed(
        report_repo=report_repo,
        submit_tool=submit_tool,
        extra_tools=(submit_tool,),
    )


class CompletionOracleRuntime(NamedTuple):
    """The peer-review subsystem's boot-time bundle.

    Attributes:
        submit_tool: Register on the agent engine's tool registry.
        gate: Inject into :class:`ReviewGateService` so the gate fires at the
            IN_REVIEW -> COMPLETED transition.
        report_repo: Per-execution verdict storage (same instance the gate
            reads and the tool writes).
        runner: Production :class:`ReviewerAgentRunner`.
        shadow_mode: When true, the gate's verdict is surfaced but not enforced.
        min_stakes: The gate runs only for tasks at or above this stakes level.
    """

    submit_tool: SubmitCompletionOracleVerdictTool
    gate: CompletionOracleGateService
    report_repo: CompletionOracleReportRepository
    runner: ReviewerAgentEngineRunner
    shadow_mode: bool
    min_stakes: Stakes


def build_completion_oracle_runtime(
    *,
    config: CompletionOracleConfig,
    engine: AgentEngine,
    staffing: RoleStaffingService,
    seed: CompletionOracleToolSeed,
    task_repo: TaskRepository | None = None,
    report_archive: CompletionOracleReportArchiveRepository | None = None,
    clock: Clock | None = None,
) -> CompletionOracleRuntime | None:
    """Build the peer-review runtime if the feature is enabled.

    Takes no model: the reviewer is a roster agent chosen per review, and it
    dispatches on the ``(provider, model)`` pair an operator bound to it.

    Args:
        config: The gate's behaviour config. ``enabled=False`` returns ``None``.
        engine: Boot :class:`AgentEngine` the reviewer runner delegates to.
        staffing: Answers which holder of the Completion Reviewer role should
            judge each deliverable.
        seed: The construction-phase seed from
            :func:`build_completion_oracle_tool_seed`; its repo / tool are
            reused so the gate reads through the repo the tool wrote to.
        task_repo: Reads the reviewed initiative's tasks so selection can prefer
            a holder who already worked it; ``None`` on a persistence-less boot.
        report_archive: Optional durable cross-process verdict archive; ``None``
            on a persistence-less boot (archival then skipped, fail-OPEN).
        clock: Clock seam. Defaults to :class:`SystemClock` inside the gate.

    Returns:
        :class:`CompletionOracleRuntime` on success, ``None`` when disabled.

    Raises:
        CompletionOracleRuntimeSeedIncompleteError: If ``config.enabled`` is
            True but the seed is missing its ``report_repo`` / ``submit_tool``.
    """
    if not config.enabled:
        logger.info(
            COMPLETION_ORACLE_GATE_SKIPPED,
            reason="config.disabled",
            note="completion_oracle_enabled is False; gate not constructed",
        )
        return None
    if seed.report_repo is None or seed.submit_tool is None:
        msg = (
            "build_completion_oracle_runtime called with config.enabled=True "
            "but a seed missing report_repo / submit_tool. Build the seed via "
            "build_completion_oracle_tool_seed(config=...) before the engine."
        )
        logger.error(
            COMPLETION_ORACLE_GATE_BUILD_FAILED,
            reason="seed_incomplete",
            has_report_repo=seed.report_repo is not None,
            has_submit_tool=seed.submit_tool is not None,
            note=msg,
        )
        raise CompletionOracleRuntimeSeedIncompleteError(msg)

    runner = ReviewerAgentEngineRunner(engine=engine)
    gate = CompletionOracleGateService(
        agent_runner=runner,
        report_repo=seed.report_repo,
        staffing=staffing,
        task_repo=task_repo,
        report_archive=report_archive,
        clock=clock,
    )
    return CompletionOracleRuntime(
        submit_tool=seed.submit_tool,
        gate=gate,
        report_repo=seed.report_repo,
        runner=runner,
        shadow_mode=config.shadow_mode,
        min_stakes=config.min_stakes,
    )
