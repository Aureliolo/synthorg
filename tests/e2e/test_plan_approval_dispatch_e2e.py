"""Acceptance: approving a parked plan files its child tasks.

The loop's headline promise, end to end through the REAL components on
the seam under test: a durable ``PENDING_REVIEW`` plan, a parked
``PLAN_REVIEW`` approval, and the production resume path
(``try_plan_review_resume``) building the graph against the runtime
``build_runtime_services`` assembles. Nothing on that path is mocked.

What it pins is the failure the plan's own status cannot express: an
approved plan opening its contract stage with zero children and no
explanation, because workspace provisioning failed before a single task
was created. So the assertions are the two halves of "approval made the
work durable":

* every WORK item of the approved plan became a persisted child task
  carrying its ``plan_id`` / ``plan_item_id``, and
* the plan and its project moved to the statuses that hand the
  initiative to the rollup.

Running the waves is deliberately not asserted here, because approval no
longer does it: it opens ``SKELETON`` and the rollup owns every stage
from there, which is where that half is covered.

The shipped image's half of that criterion cannot live here: this test
passes on any machine with ``git`` on PATH, which is every developer
machine and every CI runner. It is covered where the built image
actually exists, by the manifest check in
``.github/actions/smoke-test-backend-image``, which runs the shipped
preflight inside the shipped image on every build.

Zero real LLM spend: the provider is scripted and the plan is
precomputed, so no decomposition call is made at all.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._plan_review_resume import try_plan_review_resume
from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.approval import ApprovalItem
from synthorg.core.completion_enums import FinishReason
from synthorg.core.lifecycle_transition import LifecycleEntityKind
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.role import Authority, Skill
from synthorg.core.task import AcceptanceCriterion
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Priority,
    TaskStructure,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionFilterSpec,
)
from synthorg.persistence.task_protocol import TaskFilterSpec
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.audit import AuditLog
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.workers.runtime_builder import build_runtime_services
from tests._shared import (
    FakeClock,
    as_uuid,
    make_app_state,
    sid,
    wire_decomposition_model,
)
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
_PROJECT = "proj-dispatch"
_ROLE = "developer"
_BUILD_SKILL = "build"


class _PlainTurnStrategy:
    """Every agent turn finishes; no decomposition call is ever made.

    The approved plan is dispatched precomputed, so a decomposition tool
    reaching the provider at all would mean the resume path re-planned
    instead of building what the operator approved.
    """

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        del messages, tools, config
        return CompletionResponse(
            content="Work complete.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=8, output_tokens=4, cost=0.0001),
            model=model,
        )


def _agent(name: str) -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role=_ROLE,
        department="engineering",
        skills=SkillSet(primary=(Skill(id=_BUILD_SKILL, name=_BUILD_SKILL),)),
        authority=Authority(budget_limit=10.0),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


def _plan(parent_task_id: str) -> Plan:
    items = tuple(
        PlanItem(
            id=NotBlankStr(sid(f"item-{n}")),
            title=NotBlankStr(f"Build part {n}"),
            description=NotBlankStr(f"Implement part {n} of the board."),
            owner=NotBlankStr(_ROLE),
            acceptance_criteria=(NotBlankStr(f"part {n} renders"),),
            expected_artifacts=(NotBlankStr(f"web/src/part_{n}.tsx"),),
            required_skills=(NotBlankStr(_BUILD_SKILL),),
        )
        for n in (1, 2)
    )
    return Plan(
        project=NotBlankStr(sid(_PROJECT)),
        project_name=NotBlankStr("Games"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the board"),
        parent_task_id=NotBlankStr(parent_task_id),
        items=items,
        task_structure=TaskStructure.PARALLEL,
        coordination_topology=CoordinationTopology.CENTRALIZED,
        status=PlanStatus.PENDING_REVIEW,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _parked_approval(*, task_id: str, plan_id: str) -> ApprovalItem:
    return ApprovalItem(
        action_type=NotBlankStr("plan:approve"),
        title=NotBlankStr("Approve plan"),
        description=NotBlankStr("2 subtask(s)"),
        requested_by=NotBlankStr("operator"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=ApprovalSource.PLAN_REVIEW,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        task_id=NotBlankStr(task_id),
        metadata={PLAN_ID_METADATA_KEY: plan_id},
    )


@pytest.fixture
async def persistence() -> AsyncGenerator[FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def task_engine(
    persistence: FakePersistenceBackend,
) -> AsyncGenerator[TaskEngine]:
    engine = TaskEngine(persistence=persistence)
    await engine.start()
    yield engine
    await engine.stop()


async def _why_no_children(
    persistence: FakePersistenceBackend, parent_id: str, plan_id: str
) -> str:
    """Report the state the dispatch left behind, for the assertion message.

    "assert 0 == 2" is the symptom this test exists to catch and says
    nothing about the cause, which is the very gap it is pinning. The
    engine logs the failing coordination phase, but structlog does not
    route through the stdlib handlers ``caplog`` attaches to and an xdist
    worker's stdout never reaches the CI report, so the durable state is
    the only diagnosis that survives to a failure message.

    The two statuses separate the cases that matter: a FAILED parent means
    dispatch raised and was caught, BLOCKED means coordination ran and
    every subtask was unroutable or its workspace never came up, and an
    untouched parent means nothing dispatched at all.

    Returns:
        A one-line description of the parent task and plan statuses.
    """
    parent = await persistence.tasks.get(parent_id)
    plan = await persistence.plans.get(NotBlankStr(plan_id))
    parent_status = parent.status.value if parent is not None else "<missing>"
    plan_status = plan.status.value if plan is not None else "<missing>"
    return (
        f"no children were persisted; parent task is {parent_status} "
        f"and the plan is {plan_status}"
    )


async def test_approving_a_plan_dispatches_its_child_tasks(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """An approved plan leaves persisted children, not an empty SKELETON."""
    await persistence.projects.create(
        Project(name=NotBlankStr("Board"), id=as_uuid(_PROJECT))
    )
    parent = await task_engine.create_task(
        CreateTaskData(
            title="Ship the board",
            description="The objective the plan decomposes.",
            type=TaskType.DEVELOPMENT,
            project=sid(_PROJECT),
            created_by="operator",
            priority=Priority.MEDIUM,
            estimated_complexity=Complexity.MEDIUM,
            acceptance_criteria=(AcceptanceCriterion(description="board ships"),),
        ),
        requested_by="operator",
    )
    plan = _plan(str(parent.id))
    await persistence.plans.save(plan)

    store = ApprovalStore()
    approval = _parked_approval(task_id=str(parent.id), plan_id=str(plan.id))
    await store.add(approval)

    provider = ScriptedDriver("test-provider", strategy=_PlainTurnStrategy())
    agent_registry = AgentRegistryService()
    await agent_registry.register(_agent("alice"))
    await agent_registry.register(_agent("bob"))
    root_config = RootConfig(company_name="plan-approval-dispatch-test")
    settings_service = SettingsService(
        repository=persistence.settings, registry=get_registry()
    )
    await wire_decomposition_model(settings_service)
    app_state = make_app_state(
        provider_registry=ProviderRegistry({"test-provider": provider}),
        config=root_config,
        config_resolver=ConfigResolver(
            settings_service=settings_service, config=root_config
        ),
        task_engine=task_engine,
        agent_registry=agent_registry,
        approval_store=store,
        audit_log=AuditLog(),
        clock=FakeClock(),
        agent_workspace_root=tmp_path,
        persistence=persistence,
        cost_tracker=CostTracker(),
    )
    runtime = await build_runtime_services(app_state, workspace_root=tmp_path)
    # Wired even though approval runs no wave, because the app state the
    # resume path reads is the production one and a coordinator missing here
    # would mean the runtime failed to assemble rather than that this seam
    # stopped needing it.
    assert runtime.coordinator is not None, "the runtime failed to assemble"
    app_state.set_coordinator_if_absent(runtime.coordinator)

    handled = await try_plan_review_resume(
        app_state, NotBlankStr(str(approval.id)), approved=True, decided_by="operator"
    )

    assert handled is True
    # The resume returns once the graph is connected; asking the rollup to
    # drive the contract stage happens on a background task it registers.
    # Drained through the app's own seam rather than waited out, so this
    # asserts on a hand-off that finished rather than on whichever half of it
    # won a race.
    await app_state.drain_entry_background_tasks()

    # The headline assertion: the approved items became real work. A plan
    # that opens its contract stage with no children is the failure this
    # guards, and it is invisible from the plan's status alone.
    children = await persistence.tasks.query(TaskFilterSpec(plan=plan.id))
    assert len(children) == len(plan.items), await _why_no_children(
        persistence, str(parent.id), str(plan.id)
    )
    assert all(task.plan_item_id is not None for task in children)
    # One child per item, and each a different item: a count alone passes a
    # tree that filed one item twice and another not at all, which is the
    # same "approved work nobody is doing" this asserts against.
    assert {str(task.plan_item_id) for task in children} == {
        str(subtask_uuid(item.id)) for item in plan.items
    }
    assert all(task.parent_task_id == str(parent.id) for task in children)

    # The plan reached SKELETON, which is the half that says the contract
    # stage is open and the graph behind it is durable. Read from the
    # transition ledger as well as the row: the hop is what approval owes,
    # and a row written straight to SKELETON without it leaves the stage
    # unobservable to everything that reads the ledger.
    reached = await persistence.lifecycle_transitions.query(
        LifecycleTransitionFilterSpec(
            entity_kind=LifecycleEntityKind.PLAN,
            entity_id=NotBlankStr(str(plan.id)),
        )
    )
    to_statuses = {row.to_status for row in reached}
    assert PlanStatus.APPROVED.value in to_statuses
    assert PlanStatus.SKELETON.value in to_statuses
    dispatched = await persistence.plans.get(NotBlankStr(str(plan.id)))
    assert dispatched is not None
    assert dispatched.status is PlanStatus.SKELETON
    # Nothing failed the plan on the way. The runtime this test builds is
    # tailless, so the rollup that fires the contract job is absent and the
    # recovery sweep is what re-asks; a plan that arrives here FAILED means
    # approval broke the graph rather than handing it over.
    assert dispatched.failure_reason is None
    project = await persistence.projects.get(sid(_PROJECT))
    assert project is not None
    assert project.plan_id == plan.id
    assert project.status is ProjectStatus.ACTIVE
