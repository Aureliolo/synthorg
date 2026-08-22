# module-kind: tests
"""The sweep measures the shipped planner or it measures nothing.

The premise of the whole experiment is that what recursion does to a plan here
is what it does in the product. That holds only while the plan came from the
planner the product runs. The substitution is silent by design everywhere else,
because a product that cannot plan as an owner is better off with a single-shot
plan than with none, and it went unnoticed through two live recordings: one
reported `strategy=llm` from end to end.
"""

from datetime import date

import pytest

from evals.errors import RecursionDepthPlannerSubstitutedError
from evals.recursion_depth.tree import build_tree
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit


def _task(title: str) -> Task:
    """Build the objective being decomposed.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid(f"task:{title}"),
        title=NotBlankStr(title),
        description=NotBlankStr(f"Do {title}."),
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project=NotBlankStr(sid("project:recursion-depth-planner")),
        created_by=NotBlankStr("test"),
        status=TaskStatus.CREATED,
        acceptance_criteria=(AcceptanceCriterion(description=NotBlankStr("It runs")),),
    )


def _owner() -> AgentIdentity:
    """Build the lead the planning session runs as.

    Returns:
        The identity.
    """
    return AgentIdentity(
        id=as_uuid("identity:lead"),
        name=NotBlankStr("Lead"),
        role=NotBlankStr("Developer"),
        department=NotBlankStr("Engineering"),
        model=ModelConfig(
            provider=NotBlankStr("example-provider"),
            model_id=NotBlankStr("example-capable-001"),
            capability="capable",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _result(task: Task, *, strategy: str | None, depth: int = 0) -> DecompositionResult:
    """Build a tree node whose plan names *strategy*, or names none.

    Returns:
        The node.
    """
    child = _task(f"child of {task.title}")
    return DecompositionResult(
        plan=DecompositionPlan(
            parent_task_id=NotBlankStr(str(task.id)),
            subtasks=(
                SubtaskDefinition(
                    id=NotBlankStr(str(child.id)),
                    title=NotBlankStr("A unit"),
                    description=NotBlankStr("Build a unit."),
                    expected_artifacts=(NotBlankStr("unit.py"),),
                ),
            ),
            planning_strategy=NotBlankStr(strategy) if strategy else None,
            # Resolved rather than AUTO: DecompositionResult refuses a plan
            # whose structure nothing has decided yet.
            task_structure=TaskStructure.SEQUENTIAL,
        ),
        created_tasks=(child,),
        dependency_edges=(),
        depth=depth,
        children=(),
    )


class _Service:
    """A decomposition service that answers with a prepared tree."""

    def __init__(self, answer: DecompositionResult) -> None:
        self._answer = answer
        self.context: DecompositionContext | None = None

    async def decompose_task(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionResult:
        """Record the context it was asked with and answer.

        Returns:
            The prepared tree.
        """
        del task
        self.context = context
        return self._answer


class TestTheOwnerReachesThePlanner:
    """Without one the shipped strategy has nobody to plan as."""

    async def test_the_owner_is_passed_into_the_decomposition_context(self) -> None:
        task = _task("Build it")
        service = _Service(_result(task, strategy=None))
        owner = _owner()

        await build_tree(
            service=service,  # type: ignore[arg-type]  # the seam is the service call
            task=task,
            depth_cap=3,
            workspace_summary="Nothing is implemented.",
            available_roles=(NotBlankStr("Developer"),),
            owner=owner,
        )

        assert service.context is not None
        assert service.context.owner_identity == owner


class TestASubstitutePlannerIsRefused:
    """A curve about the fallback is not the curve this sweep claims."""

    async def test_a_root_produced_by_a_substitute_is_refused(self) -> None:
        task = _task("Build it")
        service = _Service(_result(task, strategy="llm"))

        with pytest.raises(RecursionDepthPlannerSubstitutedError, match="llm"):
            await build_tree(
                service=service,  # type: ignore[arg-type]  # the seam is the service call
                task=task,
                depth_cap=3,
                workspace_summary="Nothing is implemented.",
                available_roles=(NotBlankStr("Developer"),),
                owner=_owner(),
            )

    async def test_a_substitute_at_any_level_is_refused(self) -> None:
        # Recursion plans each level in its own session, so only the levels
        # that failed to staff an owner substitute. A part-researched,
        # part-single-shot tree is the shape hardest to notice.
        task = _task("Build it")
        root = _result(task, strategy=None)
        nested = root.model_copy(
            update={"children": (_result(task, strategy="llm", depth=1),)}
        )
        service = _Service(nested)

        with pytest.raises(RecursionDepthPlannerSubstitutedError):
            await build_tree(
                service=service,  # type: ignore[arg-type]  # the seam is the service call
                task=task,
                depth_cap=3,
                workspace_summary="Nothing is implemented.",
                available_roles=(NotBlankStr("Developer"),),
                owner=_owner(),
            )

    async def test_a_tree_the_shipped_planner_produced_passes(self) -> None:
        task = _task("Build it")
        service = _Service(_result(task, strategy=None))

        result = await build_tree(
            service=service,  # type: ignore[arg-type]  # the seam is the service call
            task=task,
            depth_cap=3,
            workspace_summary="Nothing is implemented.",
            available_roles=(NotBlankStr("Developer"),),
            owner=_owner(),
        )

        assert result.plan.planning_strategy is None
