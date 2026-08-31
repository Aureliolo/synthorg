# module-kind: code
"""One merge: assemble what sits below a node into a thing that runs.

This is where the experiment's question lives. Every piece below has been built
and, at a leaf, has passed its own tests in its own tree; none of that says the
pieces work together, and the whole measurement is what fraction of that leaf
work survives being brought together.

Two properties of the loop are deliberate.

Repair is ordinary, not exceptional. Contracts do not survive implementation,
so the merging agent is told in as many words that it may change a child's
interface to make the pieces fit, and is asked to record each time it does. A
run that reports no amendments is reporting that nothing was integrated.

Both arms get the same attempt budget. Repair only in the gated arm would let
it win by spending more rather than by catching anything, so the ungated arm
spends the identical number of attempts with nobody independent in the loop.
The gated arm stops early on an approval, which means it can only ever spend
LESS: a survival gap in its favour is therefore not one it bought.
"""

import asyncio
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from evals.harness.workspace import CellWorkspace, drop_escaping_links
from evals.recursion_depth.gate import MergeReview, MergeReviewer, MergeReviewRequest
from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.models import reject_negative_deltas, sum_costs
from evals.recursion_depth.session import (
    SessionLimits,
    SweepDeps,
    graded,
    run_session,
)
from evals.recursion_depth.unit import (
    UnitDelivery,
    UnitFingerprint,
    files_changed,
    probe_artifacts,
    produced_tree,
)
from synthorg.core.agent import AgentIdentity
from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_RECURSION_MERGE_ATTEMPTED,
)

logger = get_logger(__name__)

#: Where the children are placed inside the merge workspace. A dot-prefixed
#: directory so it is not itself importable: the deliverable is the tree at the
#: workspace root, and a child package left where the root package should be
#: would grade as though the merge had happened.
CHILDREN_DIR: Final[str] = ".children"

#: What a merge must produce. Both, because the stage only means something if
#: both land: the assembled thing, and the end-to-end run showing it works.
MERGE_REPORT_PATH: Final[str] = ".synthorg/merge/report.md"
MERGE_TEST_OUTPUT_PATH: Final[str] = ".synthorg/merge/end-to-end.txt"

#: How the merging agent records changing a child's interface. A marker rather
#: than prose because the count travels onto the chart, and counting sentences
#: about interfaces is not counting interface changes.
AMENDMENT_MARKER: Final[str] = "AMENDED:"

#: How many characters of a reviewer's findings reach the repair brief. Long
#: enough for the finding and its evidence, bounded so a reviewer that wrote an
#: essay cannot crowd the trusted instructions out of the prompt.
_FINDINGS_BUDGET_CHARS: Final[int] = 6000

_SLUG_ALLOWED: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")

#: Longest slug a child directory may carry.
_SLUG_MAX_CHARS: Final[int] = 40


@dataclass(frozen=True)
class MergePiece:
    """One thing to be assembled.

    Attributes:
        title: What the planner called it.
        slug: Its directory name under :data:`CHILDREN_DIR`.
        tree: The tree it produced.
        delivery: What it produced and whether that stands up. Mounted either
            way, and named either way in the brief: a merge whose inputs are
            partly broken is the ordinary case, and hiding that would brief
            the agent for a situation it is not in. What the brief must NOT do
            is collapse the two, which is why this is not a bool.
    """

    title: str
    slug: str
    tree: Path
    delivery: UnitDelivery


@dataclass(frozen=True)
class MergePlan:
    """One node's assembly, as the loop needs to see it.

    Attributes:
        task: The parent task being assembled, carrying its project, stakes
            and complexity.
        owner: The agent that does the assembling, and that may never judge it.
        workspace: The merge's own recreated tree.
        pieces: What goes into it.
        criteria: What the whole is judged against.
        execution_prefix: Stem for each session's execution id, which the
            attempt number is appended to so no two sessions share a ledger.
        merge_limits: The turn and spend bounds each ASSEMBLING session gets.
            Sized by fan-in: a merge with many pieces must read all of them
            before it can write, which a flat leaf-sized budget cannot cover.
        review_limits: The turn and spend bounds each REVIEW gets. A separate
            field rather than a shared ``limits``, because the reviewer reads
            the same pieces on a different schedule from the assembler and
            handing it the merge's own bound by omission is how the gate
            starved: the review is what actually decides the arm.
        attempts: How many merge attempts this node gets, in either arm.
    """

    task: Task
    owner: AgentIdentity
    workspace: CellWorkspace
    pieces: tuple[MergePiece, ...]
    criteria: tuple[NotBlankStr, ...]
    execution_prefix: str
    merge_limits: SessionLimits
    review_limits: SessionLimits
    attempts: int


@dataclass(frozen=True)
class MergeOutcome:
    """What one merge produced.

    Attributes:
        workspace: The assembled tree, which its own parent assembles next and
            which, at the root, is what the held-out oracle grades.
        delivered: Whether an attempt changed the assembled tree AND the
            merged tree's own tests pass. The scoring flag, and deliberately
            not what the parent's brief renders: see ``produced``.
        produced: Whether an attempt changed the assembled tree at all. This
            is the half the parent is briefed on, because a subtree that
            assembled a package and failed a check is an input the parent can
            still build from. Briefing the two as one word told a live root
            merge that four subtrees holding 169 modules had delivered
            nothing, and it then wrote nothing itself.
        attempts: Sessions the merge consumed, repair rounds included.
        turns: Agent turns across the assembling sessions. A review's turns
            are not observable through the gate's dispatch seam, which answers
            with the pair it ran on and nothing else; its SPEND is, and spend
            is what the equal-budget question is about.
        cost: What the merge and its reviews spent together, ``None`` once any
            one session's own cost is: an assembling session on a priced
            connection reviewed on an unpriced one has no honest total, only a
            partial sum wearing the shape of one.
        tokens: What they spent in tokens.
        input_tokens: The input half of ``tokens``.
        output_tokens: The output half of ``tokens``.
        executor: The pair the assembling sessions ran on.
        reviewer: The pair that JUDGED, absent in the ungated arm. Recorded
            here rather than only in the sweep provenance because the gate is
            the treatment: a reviewer that came up on the executor's own pair
            biases straight toward the null, and per-merge is the only place
            that is visible.
        verdict: The last verdict taken, absent in the ungated arm.
        parked: Whether the LAST attempt's review escalated with nobody to
            escalate to. A merge parked on attempt 1 and approved on attempt
            2 reads ``False`` here, correctly: it was judged in the end.
        parked_attempts: How many repair ROUNDS parked (not sessions, unlike
            ``attempts``). ``parked_attempts == len(terminations)`` (both
            non-zero) means every round that ran asked for a verdict and got
            none, which is the case ``emit.py`` excludes from the judged
            curve: distinguishable from "asked and unanswered once" and from
            the ungated arm, which never parks by construction.
        amendments: How many child-interface changes the agent recorded.
        missing_declared_paths: Declared paths ABSENT from the assembled tree.
            Recorded because a planner over-declaring is worth seeing; it does
            not decide delivery, which is read off the assembled tree.
            Diagnosis, never a verdict, for the reason the leaf's own field
            says.
        detail: Why it is not delivered, for a human reading the run.
        terminations: How each ASSEMBLING session ended, in attempt order. A
            review's ending is not observable through the gate's dispatch seam,
            on the same rule as ``turns``. Recorded because "produced nothing"
            and "was stopped before it could" look identical in every other
            field, and telling them apart otherwise means reading the
            transcripts.
        workspace_files_changed: The symmetric difference between the
            assembled tree before the first attempt and after the last, so
            "read every child for N turns and wrote nothing" is readable from
            the record without a transcript. This is exactly the signal the
            stopped depth-4 recording needed: four merges spent 167 tool calls
            between them and left it at ``0``.
    """

    workspace: CellWorkspace
    delivered: bool
    attempts: int
    turns: int
    cost: float | None
    produced: bool = False
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    executor: ModelPair | None = None
    reviewer: ModelPair | None = None
    verdict: str | None = None
    parked: bool = False
    parked_attempts: int = 0
    amendments: int = 0
    missing_declared_paths: tuple[str, ...] = ()
    detail: str = ""
    terminations: tuple[str, ...] = ()
    workspace_files_changed: int | None = None


@dataclass(frozen=True, slots=True)
class _MergeSpend:
    """Running spend across one merge's assembling and reviewing sessions.

    Immutable and returned fresh from :meth:`plus`, on the same pattern as
    ``execute.py``'s ``_Spend``: the loop below folds two different session
    kinds per attempt, and a mutable accumulator here would be one more shape
    for this repository's immutability convention to make an exception for.

    Attributes:
        sessions: How many sessions, assembling and reviewing both, have run.
        turns: Turns taken by the ASSEMBLING sessions. A review's turns are
            not observable through the gate's dispatch seam.
        cost: Money booked across both kinds, folded with ``sum_costs`` so a
            single unpriced session poisons the running total rather than
            being silently skipped.
        tokens: Tokens booked across both kinds.
        input_tokens: The input half of ``tokens``.
        output_tokens: The output half of ``tokens``.
    """

    sessions: int = 0
    turns: int = 0
    cost: float | None = 0.0
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def plus(
        self,
        *,
        turns: int = 0,
        cost: float | None,
        tokens: int,
        input_tokens: int,
        output_tokens: int,
    ) -> _MergeSpend:
        """Add one further session's figures.

        Returns:
            The total including it.

        Raises:
            ValueError: Any of the deltas is negative.
        """
        reject_negative_deltas(
            "merge",
            cost=cost,
            turns=turns,
            tokens=tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return _MergeSpend(
            sessions=self.sessions + 1,
            turns=self.turns + turns,
            cost=sum_costs((self.cost, cost)),
            tokens=self.tokens + tokens,
            input_tokens=self.input_tokens + input_tokens,
            output_tokens=self.output_tokens + output_tokens,
        )


def piece_slug(title: str, *, index: int) -> str:
    """Derive the directory name one piece is mounted under.

    The title is planner-authored, so it reaches the filesystem sanitised
    rather than trusted, and the index keeps two pieces that sanitise to the
    same thing apart.

    Args:
        title: The piece's title.
        index: Its position among the pieces.

    Returns:
        The slug.
    """
    stem = _SLUG_ALLOWED.sub("-", title.lower()).strip("-")[:_SLUG_MAX_CHARS]
    return f"{index:02d}-{stem}" if stem else f"{index:02d}"


def mount_children(workspace: CellWorkspace, pieces: tuple[MergePiece, ...]) -> None:
    """Place each piece's tree under the merge workspace's children directory.

    Copies rather than links, so a merge that edits a child changes only its
    own copy and the child's own record of what it delivered stays intact for
    the report.

    Symlinks are copied AS LINKS and then dropped unless they resolve inside
    the piece. The child's tree is agent-authored on a bind mount, so a link in
    it is a host path the agent chose: followed, ``copytree`` would read the
    target on the HOST, where the workspace sits several levels under the
    repository root, and deliver whatever it found to the merging agent, which
    routes what it reads into a report and from there into the reviewer's
    prompt. That is both an exfiltration path and a way to hand a unit the
    held-out oracle. A link cycle is also unbounded disk.

    Args:
        workspace: The merge's tree.
        pieces: What to place in it.
    """
    root = workspace.project_dir / CHILDREN_DIR
    for piece in pieces:
        if not piece.tree.is_dir():
            continue
        destination = root / piece.slug
        shutil.copytree(
            piece.tree,
            destination,
            symlinks=True,
            ignore_dangling_symlinks=True,
            dirs_exist_ok=True,
        )
        drop_escaping_links(destination, anchor=piece.tree)


def _piece_state(piece: MergePiece) -> str:
    """Say what state *piece* arrived in, in words the merge can act on.

    Three states, because there are three, and a brief that offers two puts
    the third somewhere it does not belong. A piece that built nothing cannot
    be assembled from and the merge has to cover the gap itself. A piece that
    built something whose own suite did not pass is still the work, and the
    merge assembles it and fixes what is wrong. Collapsing the second into the
    first is what made a live root merge, handed 277 modules across seven
    subtrees, write nothing at all across six attempts.

    Returns:
        The annotation, empty for a piece that arrived clean.
    """
    if not piece.delivery.produced:
        return "  [BUILT NOTHING]"
    if piece.delivery.reason:
        return f"  [BUILT, BUT NOT SIGNED OFF: {piece.delivery.reason}]"
    return ""


def merge_brief(plan: MergePlan, findings: tuple[str, ...]) -> str:
    """Compose the brief one merge attempt runs against.

    Args:
        plan: The node being assembled.
        findings: What the last review said, empty on the first attempt and in
            the ungated arm.

    Returns:
        The brief.
    """
    stated = [f"Objective: {plan.task.title}", "The pieces, and where they are:"]
    stated.extend(
        f"- `{CHILDREN_DIR}/{piece.slug}/`: {piece.title}{_piece_state(piece)}"
        for piece in plan.pieces
    )
    if plan.criteria:
        stated.append("The whole is only working when all of these hold:")
        stated.extend(f"- {criterion}" for criterion in plan.criteria)
    if findings:
        stated.append("An independent reviewer rejected the last attempt:")
        stated.extend(f"- {finding}" for finding in findings)
    sections = [
        (
            "Each piece was built on its own. Any state noted against a piece "
            "above is that piece's own, and none of it says whether they work "
            "together, which is what this job is for. A piece marked as not "
            "signed off is still the work: assemble it and fix what is wrong "
            "with it, rather than writing it again."
        ),
        (
            f"The pieces are copies under `{CHILDREN_DIR}/`, for you to read "
            "and take from. The deliverable is the tree at the workspace root: "
            "a piece left where it was mounted is not assembled, and nothing "
            f"under `{CHILDREN_DIR}/` is graded."
        ),
        (
            "That applies to the tests as much as to the code. The assembled "
            "tree is checked by running its suite from the workspace root, "
            f"and `{CHILDREN_DIR}/` is not searched, so a test you leave "
            "behind in a piece is a test nothing runs. Bring the pieces' "
            "tests up with their code and make them pass against the "
            "assembly."
        ),
        (
            "You may change a child's interface to make the pieces fit. That "
            "is expected: contracts written before the code was written do not "
            "survive it. Every time you do, record it in the report on its own "
            f"line beginning `{AMENDMENT_MARKER}` and say what you changed and "
            "why."
        ),
        (
            f"Record what you did in `{MERGE_REPORT_PATH}` and put the "
            "end-to-end run's own output, verbatim, in "
            f"`{MERGE_TEST_OUTPUT_PATH}`. Both paths are relative to the "
            "project workspace, and both are checked: a run that leaves them "
            "empty is recorded as having delivered nothing, whatever it says."
        ),
        (
            "Do not special-case a test, hardcode an expected value or weaken "
            "an assertion to get past it. The assembled deliverable is graded "
            "by tests you will never see."
        ),
        wrap_untrusted(TAG_TASK_DATA, "\n".join(stated)),
    ]
    return "\n\n".join(sections)


async def run_merge(
    deps: SweepDeps, plan: MergePlan, reviewer: MergeReviewer
) -> MergeOutcome:
    """Assemble one node, review it, and repair it while the budget lasts.

    Args:
        deps: The sweep's injected collaborators.
        plan: The node being assembled.
        reviewer: What looks at each attempt, which is what tells the arms
            apart.

    Returns:
        The merge's outcome.
    """
    # Offloaded: a whole-tree copytree per child, on the gateway's loop.
    await asyncio.to_thread(mount_children, plan.workspace, plan.pieces)
    # After the children are mounted and before the first attempt, so what a
    # child already delivered is not credited to the assembly that received it.
    # The declared paths are still probed at the end, because a planner
    # over-declaring is worth seeing; they just do not decide delivery.
    assembled_before = await asyncio.to_thread(produced_tree, plan.workspace)
    findings: tuple[str, ...] = ()
    review = MergeReview(approved=None)
    spend = _MergeSpend()
    parked_attempts = 0
    terminations: tuple[str, ...] = ()
    for attempt in range(1, plan.attempts + 1):
        outcome = await run_session(
            deps,
            identity=plan.owner,
            task=_attempt_task(plan, findings),
            workspace=plan.workspace,
            execution_id=f"{plan.execution_prefix}-attempt{attempt}",
            limits=plan.merge_limits,
        )
        spend = spend.plus(
            turns=outcome.turns,
            cost=outcome.cost,
            tokens=outcome.tokens,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
        )
        terminations = (*terminations, outcome.termination)
        review = await reviewer.review(_review_request(plan, attempt))
        spend = spend.plus(
            cost=review.cost,
            tokens=review.tokens,
            input_tokens=review.input_tokens,
            output_tokens=review.output_tokens,
        )
        logger.info(
            EVALS_RECURSION_MERGE_ATTEMPTED,
            task_id=str(plan.task.id),
            attempt=attempt,
            of=plan.attempts,
            verdict=review.verdict,
            parked=review.parked,
        )
        if review.parked:
            parked_attempts += 1
        if review.approved is True:
            break
        findings = _trim(review.findings)
    delivery = await _delivery(deps, plan, turns=spend.turns, baseline=assembled_before)
    amendments = await asyncio.to_thread(count_amendments, plan.workspace)
    final = await asyncio.to_thread(
        probe_artifacts, _attempt_task(plan, ()), plan.workspace
    )
    return MergeOutcome(
        workspace=plan.workspace,
        delivered=delivery.delivered,
        produced=delivery.produced,
        attempts=spend.sessions,
        turns=spend.turns,
        cost=spend.cost,
        tokens=spend.tokens,
        input_tokens=spend.input_tokens,
        output_tokens=spend.output_tokens,
        executor=ModelPair.of(plan.owner, deps.declared_pairs),
        reviewer=review.reviewer,
        verdict=review.verdict,
        parked=review.parked,
        parked_attempts=parked_attempts,
        amendments=amendments,
        missing_declared_paths=final.missing,
        detail=delivery.reason,
        terminations=terminations,
        workspace_files_changed=delivery.workspace_files_changed,
    )


def count_amendments(workspace: CellWorkspace) -> int:
    """Count the child-interface changes the merge report records.

    Args:
        workspace: The merge's tree.

    Returns:
        How many marked lines the report carries, zero when there is no report.
    """
    report = workspace.project_dir / MERGE_REPORT_PATH
    if not report.is_file():
        return 0
    text = report.read_text(encoding="utf-8", errors="replace")
    return sum(
        1 for line in text.splitlines() if line.strip().startswith(AMENDMENT_MARKER)
    )


def _attempt_task(plan: MergePlan, findings: tuple[str, ...]) -> Task:
    """Build the task one merge attempt runs.

    Returns:
        The briefed, assigned task.
    """
    return plan.task.model_copy(
        update={
            "description": NotBlankStr(merge_brief(plan, findings)),
            "artifacts_expected": (
                ExpectedArtifact(
                    type=ArtifactType.DOCUMENTATION, path=MERGE_REPORT_PATH
                ),
                ExpectedArtifact(
                    type=ArtifactType.DOCUMENTATION, path=MERGE_TEST_OUTPUT_PATH
                ),
            ),
            "assigned_to": str(plan.owner.id),
            "status": TaskStatus.ASSIGNED,
        }
    )


def _review_request(plan: MergePlan, attempt: int) -> MergeReviewRequest:
    """Describe one attempt to whatever reviews it.

    Returns:
        The review request.
    """
    return MergeReviewRequest(
        task=plan.task,
        owner=plan.owner,
        workspace=plan.workspace,
        deliverable=_deliverable_summary(plan),
        criteria=plan.criteria,
        execution_id=f"{plan.execution_prefix}-review{attempt}",
        limits=plan.review_limits,
    )


def _deliverable_summary(plan: MergePlan) -> str:
    """What the reviewer is handed on entry, before it goes and looks.

    The merge's own report when it wrote one, and an honest statement that it
    did not when it did not: an empty deliverable would be refused at the
    review input's non-blank boundary, and inventing text there would hand the
    reviewer a description of work that does not exist.

    Returns:
        The deliverable text.
    """
    report = plan.workspace.project_dir / MERGE_REPORT_PATH
    if report.is_file():
        body = report.read_text(encoding="utf-8", errors="replace").strip()
        if body:
            return body
    return (
        f"The assembly of {plan.task.title!r} wrote no report at "
        f"{MERGE_REPORT_PATH}. The tree is in the workspace; read and run it."
    )


async def _delivery(
    deps: SweepDeps,
    plan: MergePlan,
    *,
    turns: int,
    baseline: UnitFingerprint,
) -> UnitDelivery:
    """Say what the merge assembled, and separately whether it stands up.

    A merge whose every attempt took no turn was refused before it began
    rather than having assembled badly, and the two send an operator to
    different subsystems. See the leaf's own reason for the case this covers.

    Args:
        deps: The sweep's injected collaborators.
        plan: The node being assembled.
        turns: Turns across every attempt.
        baseline: The assembled tree before the first attempt, so the question
            stays what THIS assembly produced rather than what it was handed.

    Returns:
        What it assembled and why that is or is not a clean delivery. The
        suite is run from the workspace root and never descends into
        ``.children/``, so a merge that assembled the code and left the
        pieces' tests where it found them reports a failing check while
        holding a complete package. That is why the two travel separately.
    """
    if turns == 0:
        return UnitDelivery(
            produced=False,
            reason=(
                "no assembly attempt ran a single turn, so nothing was "
                "assembled and this is not an assembly failure"
            ),
            workspace_files_changed=0,
        )
    after = await asyncio.to_thread(produced_tree, plan.workspace)
    if after == baseline:
        return UnitDelivery(
            produced=False,
            reason=(
                "no assembly attempt changed the tree outside the pieces it was given"
            ),
            workspace_files_changed=0,
        )
    changed = files_changed(baseline, after)
    async with graded(
        deps, plan.workspace, owner=f"grade:{plan.execution_prefix}"
    ) as grader:
        passed, report = await grader.own_tests_pass(plan.workspace.project_dir)
    if not passed:
        return UnitDelivery(
            produced=True,
            reason=f"the merged tree's own tests did not pass: {report}",
            workspace_files_changed=changed,
        )
    return UnitDelivery(produced=True, reason="", workspace_files_changed=changed)


def _trim(findings: tuple[str, ...]) -> tuple[str, ...]:
    """Bound how much reviewer text reaches the next repair brief.

    Returns:
        As many findings as fit, in the reviewer's own order.
    """
    kept: list[str] = []
    spent = 0
    for finding in findings:
        if spent + len(finding) > _FINDINGS_BUDGET_CHARS:
            break
        kept.append(finding)
        spent += len(finding)
    return tuple(kept)


__all__ = [
    "AMENDMENT_MARKER",
    "CHILDREN_DIR",
    "MERGE_REPORT_PATH",
    "MERGE_TEST_OUTPUT_PATH",
    "MergeOutcome",
    "MergePiece",
    "MergePlan",
    "count_amendments",
    "merge_brief",
    "mount_children",
    "piece_slug",
    "run_merge",
]
