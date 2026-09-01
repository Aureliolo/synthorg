# module-kind: code
"""One leaf: the unit an agent owns end to end, its own tests included.

Splitting a unit by phase or role is the shape this experiment deliberately
does not run. One agent builds its unit and writes the tests for it, because
handing "write it" to one agent and "test it" to another divides the context
that makes either job possible and then asks them to coordinate it back.

Whether a leaf DELIVERED is decided from the tree, never from what the session
said about itself: the session took a turn, it changed something it declared,
and the tests the unit wrote for itself pass when run against its own tree. It
is not decided by the planner's declared list being complete, which is a
plan-time guess about a tree that did not exist yet. Only a delivered leaf's
claims enter the survival denominator, because work that never worked cannot be
work the merge lost.

The held-out oracle is not consulted here and its node ids never reach a brief.
An agent told which test decides a requirement builds to the test: a published
run scored near-perfect against an exposed oracle while the library it
delivered was dead outside the tested paths.
"""

import asyncio
from dataclasses import dataclass
from typing import Final, Literal

from evals.harness.workspace import CellWorkspace
from evals.recursion_depth.claims import requirement_ids_of
from evals.recursion_depth.grading import VERDICT_COLLECTED_NOTHING
from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.models import reject_negative_deltas, sum_costs
from evals.recursion_depth.session import (
    SessionLimits,
    SessionOutcome,
    SweepDeps,
    graded,
    run_session,
)
from evals.recursion_depth.tree import SpecBrief
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
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_RECURSION_UNIT_RESUMED

logger = get_logger(__name__)

#: What a unit owns under a contract, in the three states that are genuinely
#: different and were previously encoded as two.
#:
#: ``UNBOUND`` means no contract stage ran, so the unit's whole suite is the
#: evidence. An EMPTY tuple means one did run and this unit claims nothing
#: under it, so its own-test gate decides nothing and only its tree counts. A
#: non-empty tuple names the requirements to grade it on.
#:
#: A sentinel rather than ``None`` because the first two states decide who a
#: failure is filed against, and as ``tuple | None`` they were both ordinary
#: values of one type: swapping them type-checks, reads plausibly, and
#: silently files a contract's defect against a leaf that had no say in it.
type ContractClaim = Literal["unbound"] | tuple[str, ...]

#: The no-contract state. Compared by EQUALITY, never by truthiness: a
#: non-empty string is truthy and an empty tuple is not, which is exactly the
#: distinction being drawn.
UNBOUND: Final[Literal["unbound"]] = "unbound"

#: Where a unit records what it built and how it proved it, relative to the
#: project workspace. A path rather than prose because the workspace probe can
#: only ask about a path, and a unit that produced only chat has to be
#: reclassified rather than read as a clean success.
UNIT_REPORT_PATH: Final[str] = ".synthorg/unit/report.md"

_ANTI_EXPLOIT: Final[str] = (
    "Build the behaviour the requirement describes, and let the tests follow "
    "from it. Do not special-case a test, hardcode an expected value, detect "
    "that you are being run under test, or weaken an assertion to get past it. "
    "Your tests exist to find your own mistakes, so a test asserting whatever "
    "the code happens to do is worth nothing. Your work is graded by tests you "
    "will never see, against the requirements as written."
)

_OWNERSHIP: Final[str] = (
    "This unit is yours end to end. You build it AND you write the tests for "
    "it: nobody downstream is going to test your work for you, and the pieces "
    "around you are being built at the same time by others. Leave the unit in "
    "a state somebody assembling it can rely on."
)

_BOUND_OWNERSHIP: Final[str] = (
    "This unit is yours end to end. You build it AND you write the tests for "
    "it: nobody downstream is going to test your work for you.\n\n"
    "The shape is already fixed and it is already in your checkout. Modules, "
    "signatures and the types that cross between them were agreed before any "
    "unit started, and every unit building alongside you has the same ones. "
    "HONOUR WHAT YOU FIND. Import the names that are there rather than "
    "declaring your own, and implement against the signatures as written: a "
    "signature you improve is one every other unit still calls the old way, "
    "and the improvement is lost in the assembly along with the work either "
    "side of it.\n\n"
    "You will also find failing tests naming the requirements. The ones "
    "covering YOUR requirements are your definition of done: make them pass. "
    "Leave the others failing, because they belong to units that have not run "
    "yet, and do not implement behind them to make them go green.\n\n"
    "If the shape genuinely cannot express what your requirement needs, widen "
    "it rather than replacing it: add, and leave what is there callable. Say "
    "so in your report so the assembly knows to look."
)


@dataclass(frozen=True)
class LeafOutcome:
    """What one leaf produced.

    Attributes:
        workspace: The tree it left behind, which its parent merge assembles.
        delivered: Whether it changed anything it declared and its own tests
            pass in its own tree.
        attempts: Sessions consumed. One: a leaf gets no repair round, because
            repair is the treatment being measured and giving it to leaves in
            both arms would move the difference off the merge.
        turns: Agent turns across those sessions.
        cost: What it spent, ``None`` when the connection it ran on does not
            price its calls.
        tokens: What it spent in tokens.
        input_tokens: The input half of ``tokens``.
        output_tokens: The output half of ``tokens``.
        executor: The pair it actually ran on.
        produced: Whether its own tree changed. Separate from ``delivered``
            because this is the half its parent's brief renders: a leaf that
            built modules and failed a check is a different input from one
            that built nothing, and one word for both is what told a live
            root merge that four subtrees holding 169 modules had failed.
        missing_declared_paths: Declared paths ABSENT from the finished tree.
            Recorded because a planner over-declaring is worth seeing; it does
            not decide delivery, which is read off the produced tree.
            Diagnosis, never a verdict: the declaration is the planner's guess,
            written per node at whatever granularity it chose, so an
            over-declaring planner is worth seeing and must not be able to
            zero a unit that did the work.
        detail: Why it is not delivered, for a human reading the run.
        terminations: How each of its sessions ended, in order.
        workspace_files_changed: The symmetric difference between the tree
            before and after, so "spent turns, changed nothing" is readable
            from the record without a transcript.
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
    missing_declared_paths: tuple[str, ...] = ()
    detail: str = ""
    terminations: tuple[str, ...] = ()
    workspace_files_changed: int | None = None


def leaf_task(
    task: Task,
    *,
    definition: SubtaskDefinition,
    spec: SpecBrief,
    owner: AgentIdentity,
    bound: bool = False,
) -> Task:
    """Turn a decomposed subtask into the task a leaf session runs.

    The harness declares the unit report itself rather than trusting the
    planner's own ``expected_artifacts`` to be probeable. A declaration like
    "a working parser" names nothing the workspace can be asked about, so the
    zero-artifact guard would never arm and a session that wrote nothing would
    read as a clean success.

    Args:
        task: The task decomposition built for this subtask.
        definition: The planner's own definition, for what it claims.
        spec: The specification, for the requirement text.
        owner: The agent this unit is dispatched to.
        bound: Whether the cell ran a contract stage, so the unit's checkout
            already holds the shape it builds against. Changes what the leaf is
            TOLD, which is the whole of the leaf-side change: without it the
            brief instructs the unit to leave something an assembler can rely
            on, which is an instruction to guess, and with it the instruction is
            to honour what is already there.

    Returns:
        The task, briefed and assigned.
    """
    return task.model_copy(
        update={
            "description": NotBlankStr(leaf_brief(task, definition, spec, bound=bound)),
            "artifacts_expected": (
                *task.artifacts_expected,
                ExpectedArtifact(
                    type=ArtifactType.DOCUMENTATION, path=UNIT_REPORT_PATH
                ),
            ),
            "assigned_to": str(owner.id),
            "status": TaskStatus.ASSIGNED,
        }
    )


def leaf_brief(
    task: Task, definition: SubtaskDefinition, spec: SpecBrief, *, bound: bool = False
) -> str:
    """Compose the brief one leaf is executed against.

    The planner's own words are fenced: they are agent-authored text on their
    way into another agent's prompt, and everything outside the fence is the
    only trusted instruction in the brief.

    Defence in depth rather than the load-bearing fence. This brief becomes
    ``task.description``, and ``prompt_render`` fences that unconditionally at
    the LLM boundary, so the outer fence is what actually protects the prompt.
    This one is kept because it marks WHICH span is untrusted: the outer fence
    wraps the whole brief, including the harness's own instructions, and a
    reader of the rendered prompt could not otherwise tell them apart.

    Args:
        task: The unit's task, for what decomposition wrote into it.
        definition: The planner's definition, for what the unit claims.
        spec: The specification the claims index into.
        bound: Whether a contract stage put the shape in the unit's checkout.

    Returns:
        The brief.
    """
    # Resolves a second time, after the tree-wide pre-check the runner makes
    # before any leaf session opens. Cannot raise here by construction; kept
    # rather than threaded so this function needs only the definition it is
    # already handed.
    claimed = [
        f"- {identifier}: {spec.titles[identifier]}"
        for identifier in requirement_ids_of(
            definition.satisfies,
            known=spec.requirement_ids,
            unit=definition.title,
        )
    ]
    stated = [f"Your unit: {definition.title}", str(task.description)]
    if claimed:
        stated.append("The specification requirements this unit is answerable for:")
        stated.extend(claimed)
    return "\n\n".join(
        [
            _BOUND_OWNERSHIP if bound else _OWNERSHIP,
            _ANTI_EXPLOIT,
            (
                f"Record what you built and how you proved it in "
                f"`{UNIT_REPORT_PATH}`, relative to the project workspace: what "
                "you implemented, where it lives, what you assumed about the "
                "pieces around you, and the command that runs your tests. The "
                "path is checked, and a unit that leaves it empty is recorded "
                "as having delivered nothing whatever it says elsewhere."
            ),
            "The specification you are building against:",
            spec.prose,
            wrap_untrusted(TAG_TASK_DATA, "\n".join(stated)),
        ]
    )


async def run_leaf(
    deps: SweepDeps,
    *,
    task: Task,
    owner: AgentIdentity,
    workspace: CellWorkspace,
    execution_id: str,
    limits: SessionLimits,
    owned: ContractClaim = UNBOUND,
) -> LeafOutcome:
    """Run one leaf and decide, from its tree, whether it delivered.

    Args:
        deps: The sweep's injected collaborators.
        task: The briefed, assigned leaf task.
        owner: The agent that builds it.
        workspace: Its own recreated tree.
        execution_id: What the ledger keys this unit's spend on.
        limits: The turn and spend bounds this session gets.
        owned: Requirement ids this leaf answers for, or ``UNBOUND`` where no
            contract seeded its tree. See :func:`_delivery`.

    Returns:
        The leaf's outcome.
    """
    # Before the session, because delivery is a question about what THIS run
    # produced: the workspace is recreated from the committed seed, and what
    # the seed already held is not work this unit did.
    baseline = await asyncio.to_thread(produced_tree, workspace)
    outcome = await run_session(
        deps,
        identity=owner,
        task=task,
        workspace=workspace,
        execution_id=execution_id,
        limits=limits,
    )
    attempts = 1
    # The attempt that died is still spend: its calls were billed and its turns
    # were taken. Each figure below is what ONE session added, because the
    # ledger read is a delta past the count standing when that session opened
    # and the loop's turn list is its own, so reporting the resume's alone
    # would file a 30-turn failure followed by a 4-turn resume as a 4-turn unit.
    spent = _Spend.of(outcome)
    if _died_in_flight(outcome):
        # RESUMED, not re-run. The engine replays the conversation this
        # execution_id checkpointed and continues from the last turn, so an
        # infrastructure failure costs the turns still in flight rather than
        # every turn the unit had already paid for. Re-running from scratch
        # would cost the same money twice and discard the tree built so far.
        logger.warning(
            EVALS_RECURSION_UNIT_RESUMED,
            execution_id=execution_id,
            task_id=str(task.id),
            termination=outcome.termination,
            turns=outcome.turns,
        )
        outcome = await run_session(
            deps,
            identity=owner,
            task=task,
            workspace=workspace,
            execution_id=execution_id,
            limits=limits,
            resume=True,
        )
        attempts = 2
        spent = spent.plus(outcome)
    delivery = await _delivery(
        deps, task, workspace, outcome, baseline, turns=spent.turns, owned=owned
    )
    final = await asyncio.to_thread(probe_artifacts, task, workspace)
    return LeafOutcome(
        workspace=workspace,
        delivered=delivery.delivered,
        produced=delivery.produced,
        attempts=attempts,
        turns=spent.turns,
        cost=spent.cost,
        tokens=spent.tokens,
        input_tokens=spent.input_tokens,
        output_tokens=spent.output_tokens,
        executor=ModelPair.of(owner, deps.declared_pairs),
        missing_declared_paths=final.missing,
        detail=delivery.reason or delivery.note,
        terminations=spent.terminations,
        workspace_files_changed=delivery.workspace_files_changed,
    )


@dataclass(frozen=True, slots=True)
class _Spend:
    """What a leaf has cost across every session it took.

    Attributes:
        turns: Turns taken, summed over the attempts.
        cost: Money booked, summed over the attempts, ``None`` once any
            attempt's own cost is: a leaf resumed onto a second attempt after
            an infrastructure failure has one connection throughout, so this
            can only change between attempts if the connection's own pricing
            does, and folding with ``sum_costs`` reports that honestly rather
            than silently dropping the unpriced half.
        tokens: Tokens booked, summed over the attempts.
        input_tokens: The input half of ``tokens``.
        output_tokens: The output half of ``tokens``.
        terminations: How each session ended, in the order they ran. Kept as a
            sequence rather than a last-one-wins field because the attempts
            fail for different reasons and the earlier one is usually the one
            worth reading.
    """

    turns: int
    cost: float | None
    tokens: int
    input_tokens: int
    output_tokens: int
    terminations: tuple[str, ...]

    @classmethod
    def of(cls, outcome: SessionOutcome) -> _Spend:
        """Open the running total on one session's figures.

        Args:
            outcome: The session that has run.

        Returns:
            The total so far.
        """
        return cls(
            turns=outcome.turns,
            cost=outcome.cost,
            tokens=outcome.tokens,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            terminations=(outcome.termination,),
        )

    def plus(self, outcome: SessionOutcome) -> _Spend:
        """Add a further session's figures.

        Args:
            outcome: The session that has just run.

        Returns:
            The total including it.

        Raises:
            ValueError: Any of ``outcome``'s deltas is negative.
        """
        reject_negative_deltas(
            "leaf",
            cost=outcome.cost,
            turns=outcome.turns,
            tokens=outcome.tokens,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
        )
        return _Spend(
            turns=self.turns + outcome.turns,
            cost=sum_costs((self.cost, outcome.cost)),
            tokens=self.tokens + outcome.tokens,
            input_tokens=self.input_tokens + outcome.input_tokens,
            output_tokens=self.output_tokens + outcome.output_tokens,
            terminations=(*self.terminations, outcome.termination),
        )


def _died_in_flight(outcome: SessionOutcome) -> bool:
    """Whether the session was cut off rather than reaching an end it chose.

    ``ERROR`` alone. Every other termination is the run deciding something:
    ``MAX_TURNS`` and ``BUDGET_EXHAUSTED`` spent what they were given,
    ``NO_OP`` produced nothing and that IS the measurement, and ``COMPLETED``
    is a delivery whose artifacts are judged separately. Resuming any of those
    would buy a second opinion on a verdict the sweep exists to record.

    ``ERROR`` is the one that is never about the work: the loop returns it when
    a provider call fails past its retries, which says nothing about whether
    the unit could have delivered. It is also the only reason whose turns are
    worth continuing rather than re-running, because the conversation was
    making progress when the infrastructure went away.

    Args:
        outcome: How the session ended.

    Returns:
        True when a resume is worth an attempt.
    """
    return outcome.termination == TerminationReason.ERROR.value


async def _delivery(
    deps: SweepDeps,
    task: Task,
    workspace: CellWorkspace,
    outcome: SessionOutcome,
    baseline: UnitFingerprint,
    *,
    turns: int,
    owned: ContractClaim = UNBOUND,
) -> UnitDelivery:
    """Say what *task* produced, and separately whether it stands up.

    The no-turn case is separated because it is a different fact about a
    different subsystem. A session that took no turn at all was refused before
    it began (an exhausted quota, a provider that would not answer), and
    reporting that as missing artifacts sends an operator to read the agent's
    work when there is none to read. A live run recorded three consecutive
    leaves at zero turns and zero tokens, all of them saying the agent had
    written no files.

    Args:
        deps: The sweep's injected collaborators.
        task: The leaf whose tree is being judged.
        workspace: Its own recreated tree.
        outcome: The session that ran last, for the ending it reports.
        baseline: What the tree held before the unit ran.
        turns: Turns the UNIT took, summed over its attempts. Separate from
            ``outcome.turns`` because the guard below asks whether anything
            ran at all, which is a fact about the unit: a resumed attempt
            that errors immediately takes no turn of its own, and reading its
            count alone would file a leaf that built for thirty turns as
            never having started, skipping the artifact and own-test checks
            that decide whether it delivered.
        owned: Requirement ids this unit answers for, or ``UNBOUND`` where no
            contract seeded the tree and the suite in it is therefore all the
            unit's own. The empty TUPLE is a third state and not the same as
            ``UNBOUND``: it says a contract seeded the tree and this unit
            claims nothing in it, so it owns no test. Under a contract the
            checkout
            carries a failing test for EVERY requirement and each unit is told
            to leave the others failing, so grading one on the whole suite
            does not misjudge occasionally, it marks every unit undelivered
            whatever it built (measured: three of three, against six of six
            delivering in the arm with no contract).

    Returns:
        What it produced and why that is or is not a clean delivery. The two
        are separate because the parent's brief renders the first: a leaf that
        wrote modules and failed its own suite is an input a merge can still
        assemble from, and telling the merge it received nothing is a lie it
        acts on.
    """
    if turns == 0:
        return UnitDelivery(
            produced=False,
            reason=(
                f"the unit ran no turns, so nothing was built and this is not "
                f"a delivery failure: it terminated {outcome.termination}"
            ),
            workspace_files_changed=0,
        )
    after = await asyncio.to_thread(produced_tree, workspace)
    if after == baseline:
        return UnitDelivery(
            produced=False,
            reason="the session left its tree exactly as it found it",
            workspace_files_changed=0,
        )
    changed = files_changed(baseline, after)
    if owned != UNBOUND and not owned:
        # A note, not a reason: the sentence says the gate decided nothing,
        # and putting that in `reason` made `delivered` read it as the gate
        # deciding against the unit. The tree changed, which is the evidence
        # this branch names.
        return UnitDelivery(
            produced=True,
            reason="",
            note=(
                "the unit claims no requirement, so under a contract it owns "
                "no test and its own-test gate decided nothing; only its tree "
                "is evidence"
            ),
            workspace_files_changed=changed,
        )
    selecting: tuple[str, ...] = () if owned == UNBOUND else owned
    async with graded(deps, workspace, owner=f"grade:{task.id}") as grader:
        passed, report = await grader.own_tests_pass(
            workspace.project_dir, selecting=selecting
        )
    reason, note = _why_not(report, passed=passed, owned=owned)
    return UnitDelivery(
        produced=True,
        reason=reason,
        note=note,
        workspace_files_changed=changed,
    )


def _why_not(report: str, *, passed: bool, owned: ContractClaim) -> tuple[str, str]:
    """Why the graded run is not a clean delivery, empty when it is.

    Two answers rather than one, because the middle branch below is not a
    verdict against the unit and ``UnitDelivery.delivered`` reads any reason
    as one.

    Returns:
        The reason, and the note that is not a reason.
    """
    if passed:
        return "", ""
    if owned != UNBOUND and owned and VERDICT_COLLECTED_NOTHING in report:
        # The selection is the CONTRACT's promise, not this unit's: it owed a
        # test named for every requirement and this unit's ids reached none of
        # them. Failing the unit for that files a contract defect against the
        # party that had no say in it, which is the misattribution the whole
        # ownership branch exists to avoid, so the tree is the evidence left.
        return "", (
            f"no test in the seeded tree is named for any requirement this "
            f"unit claims ({', '.join(owned)}), so its own-test gate decided "
            f"nothing and only its tree is evidence"
        )
    return f"the unit's own tests did not pass: {report}", ""


__all__ = [
    "UNBOUND",
    "UNIT_REPORT_PATH",
    "ContractClaim",
    "LeafOutcome",
    "leaf_brief",
    "leaf_task",
    "run_leaf",
]
