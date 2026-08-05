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
    """List what the plan's work items were supposed to produce.

    Returns:
        One line per work item, naming its declared deliverables so the lead
        knows where to look rather than being told they exist.
    """
    lines: list[str] = []
    for item in plan.items:
        if item.kind is not PlanItemKind.WORK:
            continue
        artifacts = ", ".join(item.expected_artifacts)
        lines.append(f"- {item.title} (declared deliverables: {artifacts})")
    return lines


def _test_run_lines(records: tuple[CodeExecutionRecord, ...]) -> list[str]:
    """Render what the recorded test runs actually did.

    The judgement's hardest criterion is usually "the suite passes", and
    without this the only source for it is an agent's claim. These rows are
    written by the sandbox at execution time, so they say what ran and how
    it ended rather than what anyone reported.

    Returns:
        A header plus one line per recorded run, or nothing when none ran.
        Nothing is itself a signal: a criterion resting on a suite nobody
        ran is not one the judge can mark met.
    """
    if not records:
        return []
    lines = [
        (
            "Test runs actually recorded during this initiative "
            "(written by the sandbox, not reported by an agent):"
        ),
    ]
    lines.extend(
        f"- {record.command} -> "
        f"{'passed' if record.passed else 'failed'}"
        f"{' (timed out)' if record.timed_out else ''}"
        f", exit {record.returncode}"
        for record in records
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
