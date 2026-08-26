# module-kind: code
"""Realising one planned subtask as an executable task.

Split from the service so the mapping from what the planner WROTE to what the
loop RUNS sits on its own: it is the point where a plan's premises become part
of each child's brief, and where the expected artifacts that arm the
zero-artifact guard downstream are attached.
"""

from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._artifacts import expected_artifact_from_spec
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.decomposition.models import DecompositionPlan, SubtaskDefinition
from synthorg.engine.decomposition.plan_context import with_plan_context


def task_from_subtask(
    parent: Task,
    plan: DecompositionPlan,
    subtask_def: SubtaskDefinition,
) -> Task:
    """Build the executable task one subtask definition describes.

    Args:
        parent: The task being decomposed.
        plan: The plan the definition came from, for its plan-level facts.
        subtask_def: The definition to realise.

    Returns:
        The child :class:`Task`.
    """
    return Task(
        id=subtask_uuid(subtask_def.id),
        title=subtask_def.title,
        description=NotBlankStr(
            with_plan_context(
                subtask_def.description,
                assumptions=plan.assumptions,
                open_questions=plan.open_questions,
            )
        ),
        type=parent.type,
        priority=parent.priority,
        project=parent.project,
        created_by=parent.created_by,
        parent_task_id=str(parent.id),
        delegation_chain=parent.delegation_chain,
        dependencies=subtask_def.dependencies,
        acceptance_criteria=tuple(
            AcceptanceCriterion(description=c) for c in subtask_def.acceptance_criteria
        ),
        artifacts_expected=tuple(
            expected_artifact_from_spec(a) for a in subtask_def.expected_artifacts
        ),
        status=TaskStatus.CREATED,
        estimated_complexity=subtask_def.estimated_complexity,
        stakes=subtask_def.stakes,
    )


__all__ = ["task_from_subtask"]
