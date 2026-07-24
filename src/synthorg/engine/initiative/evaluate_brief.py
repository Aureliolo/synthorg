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

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanItemKind
from synthorg.engine.initiative.evaluate_models import CriterionVerdict
from synthorg.engine.initiative.tail_stages import read_integration_state
from synthorg.persistence.protocol import PersistenceBackend


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


async def build_evaluation_material(
    persistence: PersistenceBackend,
    plan: Plan,
) -> str:
    """Assemble the untrusted material the evaluation judges over.

    Args:
        persistence: Backend supplying the task repository, read for the
            integration job's evidence.
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
