# module-kind: code
"""Build the brief the integration task is executed against.

The assembling agent needs three things the plan alone does not state: that the
pieces already exist and are individually verified, that its job is to make
them run as one thing, and that the objective's success criteria are what
"working" means here.

Objective title, item titles, and criteria are agent-authored or
operator-authored text reaching an agent prompt, so they are fenced with
:func:`wrap_untrusted` under ``TAG_TASK_DATA``; the instructions around the
fence are the only trusted text in the brief.
"""

from typing import Final

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanItemKind
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted

#: Where the integration task records what it did, relative to the project
#: workspace. Paths rather than prose, because the workspace probe can only
#: ask about a path: a declaration like "the integrated deliverable" is not
#: probeable, so it contributes nothing to the check and an integration run
#: that produced only chat would reach review with the guard never armed.
#: The stage cannot know where a given objective's deliverable lives, so it
#: does not guess: it names two files of its own that the brief instructs the
#: agent to write, and checks those.
INTEGRATION_REPORT_PATH: Final[str] = ".synthorg/integration/report.md"
INTEGRATION_TEST_OUTPUT_PATH: Final[str] = ".synthorg/integration/end-to-end.txt"

#: What the integration task must produce. Two declarations, because the stage
#: only means something if both land: the assembled thing that runs, and the
#: end-to-end run that shows it does. They also arm the fail-loud zero-artifact
#: guard, so a chat-only integration run terminates NO_OP rather than passing.
INTEGRATION_ARTIFACTS: Final[tuple[str, ...]] = (
    INTEGRATION_REPORT_PATH,
    INTEGRATION_TEST_OUTPUT_PATH,
)


def integration_title(plan: Plan) -> str:
    """Return the board title for *plan*'s integration task.

    Returns:
        A title naming the objective being integrated.
    """
    return f"Integrate: {plan.objective_title}"


def build_integration_brief(plan: Plan) -> str:
    """Compose the brief the integration task runs against.

    Returns:
        The brief: trusted framing around a fenced statement of the pieces to
        assemble and the criteria the whole must satisfy.
    """
    work = [item.title for item in plan.items if item.kind is PlanItemKind.WORK]
    report = [f"Objective: {plan.objective_title}", "The delivered pieces:"]
    report.extend(f"- {title}" for title in work)
    if plan.objective_criteria:
        report.append("The whole is only working when all of these hold:")
        report.extend(f"- {criterion}" for criterion in plan.objective_criteria)
    return "\n".join(
        [
            (
                "Every piece of this objective has been built and has passed its "
                "own review. None of that shows they work together, which is what "
                "this job is for."
            ),
            (
                "Assemble the delivered work into one deliverable that actually "
                "runs, fix whatever only shows up once the pieces meet, and prove "
                "it end to end by running it. A run that produces no integrated "
                "deliverable and no test evidence is not an integration."
            ),
            (
                f"Record what you did in `{INTEGRATION_REPORT_PATH}`: what you "
                "assembled, where the runnable deliverable is, and what you had "
                "to fix. Put the end-to-end run's own output, verbatim, in "
                f"`{INTEGRATION_TEST_OUTPUT_PATH}`. Both paths are relative to "
                "the project workspace, and both are checked: a run that leaves "
                "them empty is recorded as having delivered nothing, whatever "
                "it says here."
            ),
            wrap_untrusted(TAG_TASK_DATA, "\n".join(report)),
        ]
    )
