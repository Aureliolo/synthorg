# module-kind: code
"""The stage between planning a tree and building it: what the units agree on.

An approved plan says what the units ARE. It does not say what they build
AGAINST, and prose cannot: a brief describing a seam in paragraphs leaves each
unit to invent its own reading of it, and the first thing that reconciles those
readings is the merge at the very end, which is where a measured corpus lost its
work. Every shared module in three recorded cells was defined by more than one
child and every one of them disagreed on its exports (11 of 11, 10 of 11, 11 of
12), because the seed fixture is a README and nothing else: a leaf opening its
workspace finds no name to import and no signature to honour, so inventing one
is not a failure of instruction-following, it is the only move available.

The contract runs ONCE per cell, before any leaf opens, and its tree becomes the
seed every unit is recreated from. So the agreement does not have to be handed
to anybody or read out of a sibling's workspace: it is already in the checkout,
which is the same property the product gets from cutting each unit's worktree
from the trunk commit its ``SKELETON`` stage wrote.

What it writes is deliberately not an implementation. Signatures, module layout,
and one FAILING test per requirement. The failing test is what makes this a
contract rather than a document: a live cell spent a whole leaf titled "Decide
engine architecture and shared contracts", produced one prose file, and
delivered nothing, because a document nobody must satisfy constrains nobody.
A leaf's job then stops being "invent an interface and implement it" and becomes
"make my own failing tests pass without changing the shared signatures", which
is a question with one answer instead of eight.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Final

from evals.harness.workspace import CellWorkspace
from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.session import (
    SessionLimits,
    SweepDeps,
    graded,
    run_session,
)
from evals.recursion_depth.tree import SpecBrief
from evals.recursion_depth.unit import UnitFingerprint, files_changed, produced_tree
from synthorg.core.agent import AgentIdentity
from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.observability import get_logger

logger = get_logger(__name__)

#: What the contract session leaves behind naming the shape it fixed, relative
#: to the project workspace. Declared as an artifact so the zero-artifact guard
#: arms: a contract session that produced only chat has to be reclassified
#: rather than read as a clean success, which is precisely how the prose-only
#: "shared contracts" leaf came to be recorded as having done its job.
CONTRACT_PATH: Final[str] = "CONTRACT.md"

#: Names the contract's own tree under its cell. Not a leaf key, because it is
#: not a unit: it produces no requirement and is never graded against one.
CONTRACT_UNIT_KEY: Final[str] = "contract"

_WHAT_A_CONTRACT_IS: Final[str] = (
    "You are fixing the SHAPE this project is built to, not building it. Every "
    "unit after you is recreated from the tree you leave, and each one is told "
    "to honour what it finds rather than redefine it, so a name you choose here "
    "is the name all of them import. This is the only chance the project gets "
    "to agree on anything: the units run at the same time as each other, in "
    "separate checkouts, and none of them can see any other's work."
)

_WHAT_TO_WRITE: Final[str] = (
    "Write the package skeleton and nothing else:\n"
    "- Every module the plan names, laid out as it will ship, importable.\n"
    "- The public functions, classes, exceptions and constants each module "
    "owns, with COMPLETE type signatures and a docstring saying what each one "
    "means. Give each body a bare `raise NotImplementedError` and no more.\n"
    "- The types that cross a module boundary, defined once, in full. A value "
    "two modules pass between them is the thing most worth pinning here, and "
    "the thing least likely to survive being described in prose.\n"
    "- One test per specification requirement, named for the requirement id, "
    "asserting the behaviour that requirement describes THROUGH the public "
    "interface you just declared. Every one of them must FAIL when you are "
    "done, and fail on its assertion or on `NotImplementedError`, never on an "
    "import error or a collection error: a suite that cannot be collected "
    "tells the units nothing about what they owe."
)

_WHAT_NOT_TO_WRITE: Final[str] = (
    "Do NOT implement any behaviour. A body you fill in here is a body the unit "
    "that owns it will not write, and you are not the one being graded on it. "
    "Resist finishing a function because it looked small: the measurement is "
    "whether the units agree, and a contract that quietly became half an "
    "implementation is one nobody can tell apart from the units' own work."
)

_HOW_TO_WORK: Final[str] = (
    "Write ONE FILE PER TURN. Write it, then move to the next; do not compose "
    "the whole skeleton in a single reply.\n\n"
    "This is not a style preference. A reply has a hard token ceiling, and a "
    "skeleton covering every module and every requirement does not fit under "
    "it: what happens instead is that the reply is cut off mid-file, and a "
    "truncated reply delivers NOTHING, because none of it was written to disk. "
    "A file you have written is finished work that survives whatever happens "
    "next. Measured on the first run of this stage: a single reply attempting "
    "the whole skeleton ran 23 minutes and had still written no file.\n\n"
    "Work in the order the units will need: the shared types first, then the "
    "modules that pass them, then the tests."
)


@dataclass(frozen=True)
class ContractOutcome:
    """What the contract stage produced, and what it cost.

    Attributes:
        workspace: The tree every unit of this cell is then recreated from.
        delivered: Whether it left a tree that imports and whose declared file
            exists. A contract that failed this is still USED, because the
            alternative is seeding every unit from the bare README the run was
            already shown to diverge from; the flag is what tells a reader
            which of those two a cell got.
        turns: Agent turns it took.
        cost: What it spent, ``None`` on a connection that does not price.
        tokens: What it spent in tokens.
        input_tokens: The input half of ``tokens``.
        output_tokens: The output half of ``tokens``.
        executor: The pair it ran on.
        termination: How its session ended.
        detail: Why it is not delivered, for a human reading the run.
        files_written: How many files it left that the seed did not hold.
    """

    workspace: CellWorkspace
    delivered: bool
    turns: int
    cost: float | None
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    executor: ModelPair | None = None
    termination: str = ""
    detail: str = ""
    files_written: int = 0


def contract_task(
    task: Task, *, spec: SpecBrief, owner: AgentIdentity, units: tuple[str, ...]
) -> Task:
    """Turn the cell's objective into the task the contract session runs.

    Args:
        task: The objective the tree hangs off, for its identity and project.
        spec: The specification, for the requirements the tests must cover.
        owner: The agent that writes the contract.
        units: The titles the plan named, so the skeleton covers the modules
            the units will actually be asked for rather than a shape the
            contract invented on its own.

    Returns:
        The task, briefed and assigned.
    """
    return task.model_copy(
        update={
            "description": NotBlankStr(contract_brief(spec, units)),
            "artifacts_expected": (
                ExpectedArtifact(type=ArtifactType.DOCUMENTATION, path=CONTRACT_PATH),
            ),
            "assigned_to": str(owner.id),
            "status": TaskStatus.ASSIGNED,
        }
    )


def contract_brief(spec: SpecBrief, units: tuple[str, ...]) -> str:
    """Compose the brief the contract session is executed against.

    The planner's unit titles are agent-authored text on their way into another
    agent's prompt, so they are fenced. Everything outside the fence is the
    harness's own instruction and the only trusted span in the brief.

    Args:
        spec: The specification the tests are written from.
        units: The unit titles the plan named.

    Returns:
        The brief.
    """
    requirements = "\n".join(f"- {key}: {title}" for key, title in spec.titles.items())
    return "\n\n".join(
        [
            _WHAT_A_CONTRACT_IS,
            _WHAT_TO_WRITE,
            _WHAT_NOT_TO_WRITE,
            _HOW_TO_WORK,
            (
                f"Record the shape you fixed in `{CONTRACT_PATH}`, relative to "
                "the project workspace: each module, what it owns, and the "
                "types that cross between them. A unit that is unsure what it "
                "may assume reads that file, so write it for them and not for "
                "a reviewer. The path is checked."
            ),
            "Every requirement needs one failing test, named for its id:",
            requirements,
            "The specification you are fixing the shape of:",
            spec.prose,
            wrap_untrusted(
                TAG_TASK_DATA,
                "The units this plan will build, which your layout must cover:\n"
                + "\n".join(f"- {title}" for title in units),
            ),
        ]
    )


async def run_contract(
    deps: SweepDeps,
    *,
    task: Task,
    owner: AgentIdentity,
    workspace: CellWorkspace,
    execution_id: str,
    limits: SessionLimits,
) -> ContractOutcome:
    """Run the contract session and judge the tree it left.

    Judged by whether the suite it wrote COLLECTS, not by whether it passes:
    a contract's tests are supposed to fail, so a green suite means it
    implemented the thing it was told not to, and a suite that cannot be
    collected means the units inherit a checkout whose shared names do not
    resolve. Both are reported; neither stops the cell, because seeding the
    units from a flawed contract still beats seeding them from a bare README,
    and which one a cell got is the thing worth being able to read afterwards.

    Args:
        deps: The sweep's injected collaborators.
        task: The briefed, assigned contract task.
        owner: The agent that writes it.
        workspace: Its own recreated tree.
        execution_id: What the ledger keys this stage's spend on.
        limits: The turn and spend bounds it gets.

    Returns:
        The stage's outcome.
    """
    baseline = await asyncio.to_thread(produced_tree, workspace)
    outcome = await run_session(
        deps,
        identity=owner,
        task=task,
        workspace=workspace,
        execution_id=execution_id,
        limits=limits,
    )
    written, detail = await _judge(deps, task, workspace, baseline, turns=outcome.turns)
    return ContractOutcome(
        workspace=workspace,
        delivered=not detail,
        turns=outcome.turns,
        cost=outcome.cost,
        tokens=outcome.tokens,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        executor=ModelPair.of(owner, deps.declared_pairs),
        termination=outcome.termination,
        detail=detail,
        files_written=written,
    )


async def _judge(
    deps: SweepDeps,
    task: Task,
    workspace: CellWorkspace,
    baseline: UnitFingerprint,
    *,
    turns: int,
) -> tuple[int, str]:
    """Say how much the contract wrote, and why it does not stand up.

    Returns:
        The file count it added, and the reason it is not a clean contract,
        empty when it is.
    """
    if turns == 0:
        return 0, "the contract session ran no turns, so nothing was fixed"
    after = await asyncio.to_thread(produced_tree, workspace)
    if after == baseline:
        return 0, "the contract session left its tree exactly as it found it"
    written = files_changed(baseline, after)
    async with graded(deps, workspace, owner=f"contract:{task.id}") as grader:
        passed, report = await grader.own_tests_pass(workspace.project_dir)
    if passed:
        # Green is the FAILURE here, and it is not a technicality: a suite that
        # passes against bodies that should all raise means the session
        # implemented the project it was told to leave alone, and every unit
        # after it inherits work it will be graded for not having done.
        return written, "the contract's own suite passes, so it was implemented"
    if _uncollectable(report):
        return written, f"the contract's suite does not collect: {report}"
    return written, ""


#: What the grader says when a suite produced no verdict at all, rather than a
#: verdict of failure. Taken from ``grading.read_verdict``, which is the ONLY
#: thing that writes these strings, and matched against them rather than
#: against pytest's own words: the grader reads a junit report and reports
#: counts, so a check written against "collection error" or "ModuleNotFoundError"
#: matches nothing it can ever emit and quietly classifies every contract as
#: fine. That is the defect this whole stage exists to catch, one layer up.
_NOTHING_MEASURED: Final[tuple[str, ...]] = (
    "collected no tests",
    "wrote no report",
    "was not readable",
    "did not finish",
)

#: How the grader words a suite that ran: ``"3 failed and 1 errored of 42"``.
#: The errored count is the half that matters here, because an ERROR is pytest
#: failing to get into the test at all.
_COUNTS: Final[re.Pattern[str]] = re.compile(r"(\d+) failed and (\d+) errored of (\d+)")


def _uncollectable(report: str) -> bool:
    """Whether a failing suite failed for the wrong reason.

    A contract's tests must fail on their ASSERTIONS, or on the
    ``NotImplementedError`` its own bodies raise. Both are ordinary failures
    and both are what the stage is supposed to produce.

    Anything else means the units are about to be seeded from a tree whose
    shared names do not resolve, which is the divergence this stage exists to
    prevent, arriving one stage earlier and unannounced. Two shapes say that: a
    run that measured nothing at all, and a run whose tests ERRORED, since an
    error is pytest failing to reach the test rather than the test failing.

    Returns:
        True when the suite failed for a reason a contract may not have.
    """
    lowered = report.lower()
    if any(marker in lowered for marker in _NOTHING_MEASURED):
        return True
    found = _COUNTS.search(lowered)
    return found is not None and int(found.group(2)) > 0


__all__ = [
    "CONTRACT_PATH",
    "CONTRACT_UNIT_KEY",
    "ContractOutcome",
    "contract_brief",
    "contract_task",
    "run_contract",
]
