# module-kind: code
"""The brief the skeleton task runs against, and what it must leave behind.

The skeleton is the one job in the run whose output is read by every other job,
so the brief is written for a reader who will never see it: a leaf agent that
gets a signature and a failing test instead of a paragraph. What it asks for is
therefore narrow and mechanical, and it says out loud what it does NOT want,
because the failure mode here is an agent that starts implementing.

Three things are committed, and the third is the one that is easy to leave out.
The module layout and the criterion tests are obviously the contract. The gate
configuration is the contract too: a definition of done with nowhere to live is
a definition of done nobody enforces, and every gate the units are later judged
against is one this file declares.
"""

from typing import Final

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.plan_tree import PlanTree
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted

#: The manifest carrying the project's gate configuration and its pending set.
#: Named here because it is what the skeleton must write and what every later
#: stage reads; the strategy that provisions from it owns the same name.
MANIFEST_PATH: Final[str] = "synthorg.env.yaml"

#: What the skeleton task declares it will produce, so the zero-artifact guard
#: arms on it exactly as it does on ordinary work.
SKELETON_ARTIFACTS: Final[tuple[str, ...]] = (MANIFEST_PATH,)

_TITLE_PREFIX: Final[str] = "Skeleton"


def skeleton_title(plan: Plan) -> str:
    """Return the board title for *plan*'s skeleton task.

    Returns:
        A title naming the objective whose contract is being written.
    """
    return f"{_TITLE_PREFIX}: {plan.objective_title}"


def _criteria_block(plan: Plan) -> str:
    """Render the objective criteria the skeleton must write a test for.

    Returns:
        One numbered line per criterion, or a line saying there are none.
    """
    if not plan.objective_criteria:
        return "(none declared)"
    return "\n".join(
        f"{index}. {criterion}"
        for index, criterion in enumerate(plan.objective_criteria, start=1)
    )


def _workstream_block(plan: Plan) -> str:
    """Render the workstreams the module layout has to accommodate.

    Workstreams rather than every item, for the same reason the assembly brief
    names them: a plan is a tree, and a hundred titles for a layout that has to
    separate five tracks is noise the reader pays for on every turn.

    Returns:
        One line per work workstream, or a line saying there are none.
    """
    tracks = [
        str(item.title)
        for item in PlanTree.of(plan.items).workstreams
        if item.kind is PlanItemKind.WORK
    ]
    if not tracks:
        return "(none declared)"
    return "\n".join(f"- {title}" for title in tracks)


def build_skeleton_brief(plan: Plan) -> str:
    """Compose the brief the skeleton task runs against.

    The plan's own text is agent-authored and reaches an LLM boundary here, so
    it is fenced rather than interpolated raw.

    Returns:
        The brief.
    """
    body = f"""Objective: {plan.objective_title}

Write the contract for this objective as code, and stop there. Do not implement
any of it. Every unit that follows is briefed from what you commit, so the value
of this job is entirely in what it makes decidable for them.

Commit exactly three things.

1. MODULE LAYOUT AND TYPE SIGNATURES.
   Enough structure that two units working on different tracks touch different
   files. Signatures and types only: every function body raises or returns a
   placeholder. It must import cleanly, because a skeleton that does not load
   fails every check below for a reason nobody can act on.

   The tracks this layout has to keep apart:
{_workstream_block(plan)}

2. ONE PENDING TEST PER ACCEPTANCE CRITERION.
   Each test asserts its criterion against the contract and therefore FAILS,
   because nothing implements it yet. That is the intended state and it is
   recorded in the manifest, so the failing suite still reads as a green trunk.

   A pending test must fail on ITS OWN ASSERTION. A test that errors, that
   cannot be collected, or that is skipped, reads as a broken skeleton rather
   than as an unimplemented contract, and it is the one outcome that fails this
   job. Write the assertion so it is reached.

   The criteria:
{_criteria_block(plan)}

3. THE GATE CONFIGURATION, in `{MANIFEST_PATH}`.
   How a fresh clone is set up and booted, how it runs its tests, how it lints
   and formats, its coverage floor, its dependency policy, where the test runner
   writes a machine-readable per-test report, and the pending set pairing each
   criterion with the test id that will decide it.

   The report path is load-bearing. An exit status says a run failed, never why
   one test did, so without it every pending test has to be read as a failure
   rather than as the contract being unimplemented.

You are done when those three are committed and the suite runs. You are NOT
done when the criteria pass: making them pass is the work of the units that
come after you, and a criterion that already passes here means its test is not
asserting what it claims."""
    return wrap_untrusted(TAG_TASK_DATA, body)
