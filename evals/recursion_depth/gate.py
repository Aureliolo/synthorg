# module-kind: code
"""What judges a merge, in each arm.

The gated arm calls the shipped gate. `CompletionOracleGateService.evaluate` is
the treatment being measured, so it is reached unchanged: the harness supplies
the engine the reviewer runs on and nothing else. Selection, the exclusion of
the executor, the narrowed review session, the fail-CLOSED escalation and the
verdict's attribution all stay the product's.

What the harness DOES change is which tree the reviewer is pointed at. It gets
a detached copy, and the graded tree is the original. The reviewer is required
by the gate prompt to run a disconfirming command, so it holds the terminal
tool whatever the file tools allow, and a reviewer able to touch the tree it
judges could repair the work under review. That repair would land in the arm
whose INDEPENDENCE is the whole measurement, and the gated line would be
crediting gating for work the gate itself did.

The ungated arm spends the same budget with nobody independent in it. Its pass
is a self-review by the agent that just did the merge: same tree, same
criteria, same work, and no verdict. That is the honest control, because what
the gated arm is being credited with is INDEPENDENCE rather than effort, and an
arm that simply spent less would win or lose on spend.

An escalation is recorded, never resolved. There is no human in a sweep, so a
gate that escalated leaves the merge standing and the run carries the parked
count onto the chart: a gated line resting on unresolved escalations is a
different claim from one resting on verdicts.
"""

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from evals.errors import RecursionDepthGateUnbuildableError
from evals.harness.binding import RunBinding
from evals.harness.workspace import CellWorkspace, detach_workspace
from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.session import (
    SessionLimits,
    SessionSpend,
    SweepDeps,
    open_session,
    run_binding,
    run_session,
    watching,
)
from evals.recursion_depth.staffing import SweepRoster
from synthorg.core.agent import AgentIdentity
from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.builder import build_completion_oracle_tool_seed
from synthorg.engine.completion_oracle.config import CompletionOracleConfig
from synthorg.engine.completion_oracle.gate import CompletionOracleGateService
from synthorg.engine.completion_oracle.protocol import CompletionOracleReportRepository
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleFinding,
    CompletionOracleVerdict,
)
from synthorg.engine.completion_oracle.runner import ReviewerAgentEngineRunner
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_RECURSION_MERGE_GATED,
    EVALS_RECURSION_MERGE_PARKED,
)
from synthorg.tools.base import BaseTool

logger = get_logger(__name__)

#: The gate as this sweep runs it: on, enforcing, and judging every merge
#: whatever its stakes. Shadow mode would surface a verdict that changed
#: nothing, which is the ungated arm with a bigger bill, and a stakes floor
#: would silently leave the shallow end of the sweep unjudged.
_GATE_CONFIG: Final[CompletionOracleConfig] = CompletionOracleConfig(
    enabled=True, shadow_mode=False
)

#: Where the ungated arm's blind pass records what it made of its own work.
#: Declared so the pass has to do the reading and testing rather than answering
#: in prose, which is what makes the two arms' budgets comparable. Nothing in
#: the harness ever reads it.
SELF_REVIEW_PATH: Final[str] = ".synthorg/unit/self-review.md"

_SELF_REVIEW_BRIEF: Final[str] = (
    "You have just assembled this deliverable. Go back over it as if somebody "
    "else had written it: run it, run the tests, and try to find the case that "
    "breaks it. Agreeing with yourself is not a review. Record what you found "
    f"in `{SELF_REVIEW_PATH}`, relative to the project workspace, with the "
    "command you ran for each thing you checked."
)


@dataclass(frozen=True)
class MergeReviewRequest:
    """One merge, offered for judgement.

    Attributes:
        task: The merge task, carrying its project, stakes and complexity.
        owner: The agent that produced the merge, which may never judge it.
        workspace: The assembled tree, which the reviewer reads and runs.
        deliverable: The text the reviewer is handed on entry, alongside the
            tools to go and look for itself.
        criteria: What the merge is judged against.
        execution_id: What the ledger keys this review's spend on.
        limits: The turn and spend bounds the review session gets.
    """

    task: Task
    owner: AgentIdentity
    workspace: CellWorkspace
    deliverable: str
    criteria: tuple[NotBlankStr, ...]
    execution_id: str
    limits: SessionLimits


@dataclass(frozen=True)
class MergeReview:
    """What judgement was reached, and what reaching it cost.

    Attributes:
        approved: Whether the merge stands on its merits. ``None`` when no
            verdict was taken at all: the ungated arm by design, and the gated
            arm when it escalated with nobody to escalate to.
        parked: Whether the gate escalated. Counted, because the gated line
            has to be readable as "judged" rather than "asked and unanswered".
        findings: What a rejection said, in the reviewer's own words, which is
            what the repair attempt is briefed from.
        cost: What the review spent, ``None`` when the connection it ran on
            does not price its calls.
        tokens: What it spent in tokens, which is the arm comparison that does
            not move with a price change.
        input_tokens: The input half of ``tokens``.
        output_tokens: The output half of ``tokens``.
        reviewer: The pair the review actually ran on, absent when nothing
            judged. The gate is the treatment, so a judge that silently came up
            on the executor's own pair biases toward the null, and the pair is
            recorded per review rather than assumed from the manifest.
        verdict: The gate's own verdict string, for the record.
    """

    approved: bool | None
    parked: bool = False
    findings: tuple[str, ...] = ()
    cost: float | None = 0.0
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reviewer: ModelPair | None = None
    verdict: str | None = None


@runtime_checkable
class MergeReviewer(Protocol):
    """Whatever looks at a merge before the run moves on."""

    async def review(self, request: MergeReviewRequest) -> MergeReview:
        """Judge *request*, or spend the arm's budget without judging it."""
        ...


@dataclass(frozen=True)
class _JudgeSeat:
    """Everything one review is run under, once the copy has been taken.

    A bundle rather than five more keyword arguments, so the split that keeps
    the detached copy's lifetime visible at the call site does not widen a
    signature past the repository's cap.

    Attributes:
        report_repo: Where the verdict lands, narrowed to non-``None`` by the
            caller before the review is attempted at all.
        extra_tools: The submit tool the gate reads its verdict back through.
        binding: The reviewer's bearer facts.
        judge: The selected agent, whose pair is recorded on the verdict.
        workspace: The DETACHED copy, never the graded tree.
    """

    report_repo: CompletionOracleReportRepository
    extra_tools: tuple[BaseTool, ...]
    binding: RunBinding
    judge: AgentIdentity
    workspace: CellWorkspace


@dataclass(frozen=True)
class OracleMergeReviewer:
    """The gated arm: the shipped completion-oracle gate, unmodified.

    Attributes:
        deps: The sweep's injected collaborators.
        roster: The org the gate selects a reviewer from.
    """

    deps: SweepDeps
    roster: SweepRoster

    async def review(self, request: MergeReviewRequest) -> MergeReview:
        """Run one gate cycle over *request*.

        A fresh report repository per review, because the store is single-shot
        per execution id and a repair round reviews the same merge again.

        Returns:
            The verdict, its findings, and what it cost.
        """
        seed = build_completion_oracle_tool_seed(config=_GATE_CONFIG)
        if seed.report_repo is None:
            # Checked rather than assumed: a seed without a store would review
            # every merge and record nothing, and the run would then report the
            # ungated curve twice under two names.
            msg = (
                "the completion-oracle seed built no report repository, so the "
                "gated arm has nowhere to read a verdict from"
            )
            raise RecursionDepthGateUnbuildableError(msg)
        # Bound on a reviewer's own pair rather than the selected agent's: the
        # roster binds every holder of the role to the manifest's reviewer
        # pair, so the driver is the same whichever holder selection returns,
        # and the gate still records WHICH agent judged.
        judge = self.roster.reviewers[0]
        binding = run_binding(
            identity=judge,
            task=request.task,
            execution_id=request.execution_id,
            limits=request.limits,
        )
        # The reviewer judges a DETACHED COPY, and the graded tree is the
        # original. `REVIEW_TOOL_PERMISSIONS` narrows the reviewer to STANDARD,
        # which still admits the file and terminal tools the harness registry
        # supplies, and the strengthened gate prompt REQUIRES a disconfirming
        # command, so taking the tools away is not available either. Handed the
        # graded tree, a reviewer could repair the work it is judging, and the
        # repair would land in the arm whose independence is the whole
        # measurement: the gated line would then be crediting gating for work
        # the gate itself did.
        with tempfile.TemporaryDirectory() as scratch:
            reviewed = await asyncio.to_thread(
                detach_workspace, request.workspace, Path(scratch)
            )
            return await self._judge(
                request,
                seat=_JudgeSeat(
                    report_repo=seed.report_repo,
                    extra_tools=seed.extra_tools,
                    binding=binding,
                    judge=judge,
                    workspace=reviewed,
                ),
            )

    async def _judge(
        self, request: MergeReviewRequest, *, seat: _JudgeSeat
    ) -> MergeReview:
        """Run the gate against the copy the reviewer is allowed to change.

        Returns:
            The verdict, its findings, and what it cost.
        """
        async with open_session(
            self.deps,
            binding=seat.binding,
            workspace=seat.workspace,
            extra_tools=seat.extra_tools,
        ) as session:
            gate = CompletionOracleGateService(
                agent_runner=ReviewerAgentEngineRunner(engine=session.engine),
                report_repo=seat.report_repo,
                staffing=self.roster.staffing,
            )
            try:
                async with watching(self.deps, session):
                    result = await gate.evaluate(_review_input(request))
            finally:
                spend = await session.spend()
        return _from_gate_result(
            result.verdict,
            result.report.findings,
            spend,
            request,
            reviewer=ModelPair.of(seat.judge, self.deps.declared_pairs),
        )


@dataclass(frozen=True)
class BlindMergeReviewer:
    """The ungated arm: the same budget, spent with nobody independent in it.

    Attributes:
        deps: The sweep's injected collaborators.
    """

    deps: SweepDeps

    async def review(self, request: MergeReviewRequest) -> MergeReview:
        """Spend one pass over *request* and take no verdict from it.

        Returns:
            A review carrying no verdict and no findings, and what it cost.
        """
        outcome = await run_session(
            self.deps,
            identity=request.owner,
            task=_self_review_task(request),
            workspace=request.workspace,
            execution_id=request.execution_id,
            limits=request.limits,
        )
        return MergeReview(
            approved=None,
            cost=outcome.cost,
            tokens=outcome.tokens,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
        )


def _review_input(request: MergeReviewRequest) -> CompletionOracleReviewInput:
    """Describe *request* as the gate's entry value.

    Returns:
        The review input.
    """
    return CompletionOracleReviewInput(
        task_id=NotBlankStr(str(request.task.id)),
        execution_id=NotBlankStr(request.execution_id),
        deliverable_content=NotBlankStr(request.deliverable),
        acceptance_criteria=request.criteria,
        executor_agent_id=NotBlankStr(str(request.owner.id)),
        stakes=request.task.stakes,
        estimated_complexity=request.task.estimated_complexity,
        project_id=NotBlankStr(request.task.project),
    )


def _self_review_task(request: MergeReviewRequest) -> Task:
    """Build the transient task the blind pass runs.

    Returns:
        The task, assigned back to the agent that produced the merge.
    """
    return request.task.model_copy(
        update={
            "description": NotBlankStr(_SELF_REVIEW_BRIEF),
            "artifacts_expected": (
                ExpectedArtifact(
                    type=ArtifactType.DOCUMENTATION, path=SELF_REVIEW_PATH
                ),
            ),
            "assigned_to": str(request.owner.id),
            "status": TaskStatus.ASSIGNED,
        }
    )


def _from_gate_result(
    verdict: CompletionOracleVerdict,
    findings: tuple[CompletionOracleFinding, ...],
    spend: SessionSpend,
    request: MergeReviewRequest,
    *,
    reviewer: ModelPair,
) -> MergeReview:
    """Turn one gate result into the sweep's own record of it.

    Returns:
        The review.
    """
    parked = verdict is CompletionOracleVerdict.ESCALATE
    approved: bool | None = None
    if verdict in (
        CompletionOracleVerdict.APPROVE,
        CompletionOracleVerdict.APPROVE_WITH_NOTES,
    ):
        approved = True
    elif verdict is CompletionOracleVerdict.REJECT:
        approved = False
    if parked:
        # Never a silent pass, and never a silent stop: the merge stands
        # because there is nobody here to decide, and the count travels with
        # the chart so a reader can see how much of the gated line was judged.
        logger.warning(
            EVALS_RECURSION_MERGE_PARKED,
            execution_id=request.execution_id,
            task_id=str(request.task.id),
        )
    logger.info(
        EVALS_RECURSION_MERGE_GATED,
        execution_id=request.execution_id,
        task_id=str(request.task.id),
        verdict=verdict.value,
        finding_count=len(findings),
        cost=spend.cost,
        tokens=spend.tokens,
        reviewer=reviewer.label,
    )
    return MergeReview(
        approved=approved,
        parked=parked,
        findings=tuple(_finding_text(finding) for finding in findings),
        cost=spend.cost,
        tokens=spend.tokens,
        input_tokens=spend.input_tokens,
        output_tokens=spend.output_tokens,
        reviewer=reviewer,
        verdict=verdict.value,
    )


def _finding_text(finding: CompletionOracleFinding) -> str:
    """Render one finding as the line a repair attempt is briefed from.

    Returns:
        The rendered finding.
    """
    parts = [f"[{finding.severity.value}] {finding.description}"]
    if finding.criterion is not None:
        parts.append(f"(criterion: {finding.criterion})")
    if finding.build_or_test_reference is not None:
        parts.append(f"(grounded in: {finding.build_or_test_reference})")
    if finding.suggested_fix is not None:
        parts.append(f"Suggested: {finding.suggested_fix}")
    return " ".join(parts)


__all__ = [
    "SELF_REVIEW_PATH",
    "BlindMergeReviewer",
    "MergeReview",
    "MergeReviewRequest",
    "MergeReviewer",
    "OracleMergeReviewer",
]
