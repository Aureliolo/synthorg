# module-kind: code
"""Assemble what the evaluate stage judges against.

The lead needs the objective, the criteria to judge, what was delivered, and
where the integration evidence is. All of it is agent-authored or
operator-authored text; the caller fences the assembled material, so this
module only decides what goes in.

Deliberately no verdicts, no "the plan says this is done", and no summary of
how well it went: those would give the judgement its answer. It gets the claims
and the artefacts, and is asked to check them.
"""

from typing import Final

from synthorg.core.evaluation_verdict import CriterionVerdict
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.plan_tree import PlanTree
from synthorg.engine.initiative.tail_stages import read_integration_state
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionRecord,
)
from synthorg.persistence.protocol import PersistenceBackend

#: How many recorded test runs the material carries. The judgement needs the
#: project's verdict, not its whole build history, and an unbounded list would
#: crowd the criteria it is there to judge out of the prompt.
_MAX_TEST_RUNS: Final[int] = 20


def _delivered_lines(plan: Plan) -> list[str]:
    """List what the plan's workstreams were supposed to produce.

    Workstreams rather than every item, for the reason ``_MAX_TEST_RUNS``
    states one constant below: a plan is a tree, so one line per item is a
    hundred lines at depth, and they crowd out the criteria this material
    exists to have judged. Each workstream was itself assembled by its own
    container task, which declared its own evidence, so the coarse list is
    also the one whose deliverables actually cover the whole subtree.

    Returns:
        One line per top-level work item, naming its declared deliverables so
        the lead knows where to look rather than being told they exist.
    """
    lines: list[str] = []
    for item in PlanTree.of(plan.items).workstreams:
        if item.kind is not PlanItemKind.WORK:
            continue
        artifacts = ", ".join(item.expected_artifacts)
        lines.append(f"- {item.title} (declared deliverables: {artifacts})")
    return lines


def _test_run_lines(records: tuple[CodeExecutionRecord, ...]) -> list[str]:
    """Render what the recorded test runs actually did.

    The judgement's hardest criterion is usually "the suite passes", and
    without this the only source for it is an agent's claim. The outcome
    fields are computed by the recorder at execution time, so they say how
    a run ended rather than what anyone reported.

    The command itself is not rendered. It is model-supplied text with no
    newline restriction, so a single command spelling further bullet rows
    would present them under a header vouching for their provenance; and a
    command line can carry a credential in an argument, which would then
    leave the trust boundary inside the judge's prompt. The runner name is
    computed here instead, from the same classifier that decided the row
    was a test run at all.

    Returns:
        A header plus one line per recorded run, or nothing when none ran.
        Nothing is itself a signal: a criterion resting on a suite nobody
        ran is not one the judge can mark met.
    """
    if not records:
        return []
    lines = [
        (
            "Test runs recorded by the sandbox during this initiative. The"
            " outcomes below were computed at execution time, not reported"
            " by an agent:"
        ),
    ]
    lines.extend(
        f"- run {index}: "
        f"{'passed' if record.passed else 'failed'}"
        f"{' (timed out)' if record.timed_out else ''}"
        f", exit {record.returncode}"
        for index, record in enumerate(records, start=1)
    )
    return lines


async def build_evaluation_material(
    persistence: PersistenceBackend,
    plan: Plan,
) -> str:
    """Assemble the untrusted material the evaluation judges over.

    Args:
        persistence: Backend supplying the task repository, read for the
            integration job's evidence, and the code-execution records that
            say what actually ran.
        plan: The plan being evaluated.

    Returns:
        The material, unfenced; the session's brief builder fences it.
    """
    sections = [
        f"Objective: {plan.objective_title}",
        "Success criteria, each of which you must judge:",
        *(f"- {criterion}" for criterion in plan.objective_criteria),
        "What the plan set out to deliver:",
        *_delivered_lines(plan),
    ]
    state = await read_integration_state(persistence, plan, allow_new_attempt=False)
    integration = await persistence.tasks.get(str(state.task_id))
    if integration is not None:
        expected = ", ".join(a.path for a in integration.artifacts_expected)
        sections.extend(
            [
                "The assembly job that ran over the delivered pieces:",
                f"- {integration.title} (status: {integration.status.value})",
                f"- it was required to produce: {expected}",
            ]
        )
    records = await persistence.code_execution_records.query(
        CodeExecutionFilterSpec(project_id=plan.project),
        limit=_MAX_TEST_RUNS,
    )
    sections.extend(_test_run_lines(records))
    return "\n".join(sections)


def unmet_verdict_detail(verdicts: tuple[CriterionVerdict, ...]) -> str:
    """Render the criteria a judgement did not pass, with their evidence.

    The verdict is the only account of what the delivered whole actually
    failed at, and the successor's planner needs it in those terms rather than
    as a generic "the objective was not met".

    Returns:
        One line per unmet criterion, naming the outcome and what was observed.
    """
    return "\n".join(
        f"- {verdict.criterion} [{verdict.outcome.value}]: {verdict.evidence}"
        for verdict in verdicts
    )
