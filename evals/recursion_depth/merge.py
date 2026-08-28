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
from evals.recursion_depth.session import (
    SessionLimits,
    SweepDeps,
    graded,
    probe_artifacts,
    run_session,
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

#: What sits in a merge's tree without being anything the merge assembled: the
#: children it was handed, its own paperwork, and the brief it started from.
#: Everything else at the root IS the assembly.
_NOT_ASSEMBLY: Final[frozenset[str]] = frozenset(
    {CHILDREN_DIR, ".synthorg", "README.md"}
)

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
        delivered: Whether it delivered. Mounted either way, and named either
            way in the brief: a merge whose inputs are partly broken is the
            ordinary case, and hiding that would brief the agent for a
            situation it is not in.
    """

    title: str
    slug: str
    tree: Path
    delivered: bool


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
        limits: The turn and spend bounds each session gets.
        attempts: How many merge attempts this node gets, in either arm.
    """

    task: Task
    owner: AgentIdentity
    workspace: CellWorkspace
    pieces: tuple[MergePiece, ...]
    criteria: tuple[NotBlankStr, ...]
    execution_prefix: str
    limits: SessionLimits
    attempts: int


@dataclass(frozen=True)
class MergeOutcome:
    """What one merge produced.

    Attributes:
        workspace: The assembled tree, which its own parent assembles next and
            which, at the root, is what the held-out oracle grades.
        delivered: Whether an attempt changed the assembled tree and the merged
            tree's own tests pass. Judged on the tree, never on the merge's own
            paperwork: this verdict is briefed to the PARENT as
            ``[DID NOT DELIVER]``, so a false negative here misleads every
            assembly above it.
        attempts: Sessions the merge consumed, repair rounds included.
        turns: Agent turns across the assembling sessions. A review's turns
            are not observable through the gate's dispatch seam, which answers
            with the pair it ran on and nothing else; its SPEND is, and spend
            is what the equal-budget question is about.
        cost: What the merge and its reviews spent together.
        tokens: What they spent in tokens.
        executor: The pair the assembling sessions ran on.
        reviewer: The pair that JUDGED, absent in the ungated arm. Recorded
            here rather than only in the sweep provenance because the gate is
            the treatment: a reviewer that came up on the executor's own pair
            biases straight toward the null, and per-merge is the only place
            that is visible.
        verdict: The last verdict taken, absent in the ungated arm.
        parked: Whether the gate escalated with nobody to escalate to.
        amendments: How many child-interface changes the agent recorded.
        undeclared_paths: Declared paths absent from the assembled tree.
            Diagnosis, never a verdict, for the reason the leaf's own field
            says.
        detail: Why it is not delivered, for a human reading the run.
    """

    workspace: CellWorkspace
    delivered: bool
    attempts: int
    turns: int
    cost: float
    tokens: int = 0
    executor: ModelPair | None = None
    reviewer: ModelPair | None = None
    verdict: str | None = None
    parked: bool = False
    amendments: int = 0
    undeclared_paths: tuple[str, ...] = ()
    detail: str = ""


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
        f"- `{CHILDREN_DIR}/{piece.slug}/`: {piece.title}"
        + ("" if piece.delivered else "  [DID NOT DELIVER]")
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
            "Every piece of this has been built and, where it was a unit of "
            "work, has passed its own tests in its own tree. None of that "
            "shows they work together, which is what this job is for."
        ),
        (
            f"The pieces are copies under `{CHILDREN_DIR}/`, for you to read "
            "and take from. The deliverable is the tree at the workspace root: "
            "a piece left where it was mounted is not assembled, and nothing "
            f"under `{CHILDREN_DIR}/` is graded."
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
    assembled_before = await asyncio.to_thread(assembled_tree, plan.workspace)
    findings: tuple[str, ...] = ()
    review = MergeReview(approved=None)
    sessions = 0
    turns = 0
    cost = 0.0
    tokens = 0
    for attempt in range(1, plan.attempts + 1):
        outcome = await run_session(
            deps,
            identity=plan.owner,
            task=_attempt_task(plan, findings),
            workspace=plan.workspace,
            execution_id=f"{plan.execution_prefix}-attempt{attempt}",
            limits=plan.limits,
        )
        sessions += 1
        turns += outcome.turns
        cost += outcome.cost
        tokens += outcome.tokens
        review = await reviewer.review(_review_request(plan, attempt))
        sessions += 1
        cost += review.cost
        tokens += review.tokens
        logger.info(
            EVALS_RECURSION_MERGE_ATTEMPTED,
            task_id=str(plan.task.id),
            attempt=attempt,
            of=plan.attempts,
            verdict=review.verdict,
            parked=review.parked,
        )
        if review.approved is True or review.parked:
            break
        findings = _trim(review.findings)
    detail = await _undelivered_reason(
        deps, plan, turns=turns, baseline=assembled_before
    )
    amendments = await asyncio.to_thread(count_amendments, plan.workspace)
    final = await asyncio.to_thread(
        probe_artifacts, _attempt_task(plan, ()), plan.workspace
    )
    return MergeOutcome(
        workspace=plan.workspace,
        delivered=not detail,
        attempts=sessions,
        turns=turns,
        cost=cost,
        tokens=tokens,
        executor=ModelPair.of(plan.owner, deps.declared_pairs),
        reviewer=review.reviewer,
        verdict=review.verdict,
        parked=review.parked,
        amendments=amendments,
        undeclared_paths=final.missing,
        detail=detail,
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
        limits=plan.limits,
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


async def _undelivered_reason(
    deps: SweepDeps,
    plan: MergePlan,
    *,
    turns: int,
    baseline: frozenset[tuple[str, int]],
) -> str:
    """Say why the merged tree is not a delivery, or nothing when it is.

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
        The reason, empty when the merge delivered.
    """
    if turns == 0:
        return (
            "no assembly attempt ran a single turn, so nothing was assembled "
            "and this is not an assembly failure"
        )
    if await asyncio.to_thread(assembled_tree, plan.workspace) == baseline:
        return "no assembly attempt changed the tree outside the pieces it was given"
    async with graded(
        deps, plan.workspace, owner=f"grade:{plan.execution_prefix}"
    ) as grader:
        passed, report = await grader.own_tests_pass(plan.workspace.project_dir)
    if not passed:
        return f"the merged tree's own tests did not pass: {report}"
    return ""


def assembled_tree(workspace: CellWorkspace) -> frozenset[tuple[str, int]]:
    """Fingerprint what the merge has assembled, ignoring what it was handed.

    A merge is judged on the tree it produces, never on its own paperwork. The
    first version declared the report and the end-to-end output as the merge's
    expected artifacts and asked the shared artifact probe about those, so a
    merge that assembled the whole package and skipped one markdown file was
    recorded as having changed nothing.

    That verdict does not stay local: :func:`merge_brief` marks a child
    ``[DID NOT DELIVER]`` for its parent, so the false negative is briefed
    upward. Measured on a live cap-2 cell, the root merge was told four of its
    seven subtrees had failed when every one of them had assembled a package,
    and it scored zero against an oracle that passed 35 to 38 at cap 1, where
    the tree has no intermediate merges to mislabel. A defect that only fires
    below the root is one that reads exactly like depth not working.

    Args:
        workspace: The merge's tree.

    Returns:
        Each assembled file as ``(relative path, size)``. Size rather than a
        digest because this only has to answer whether the assembly MOVED, and
        it runs over trees holding hundreds of files.
    """
    root = workspace.project_dir
    if not root.is_dir():
        return frozenset()
    return frozenset(
        (str(path.relative_to(root).as_posix()), path.stat().st_size)
        for entry in root.iterdir()
        if entry.name not in _NOT_ASSEMBLY
        for path in (entry.rglob("*") if entry.is_dir() else (entry,))
        if path.is_file()
    )


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
