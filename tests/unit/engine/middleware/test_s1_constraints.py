"""Tests for S1 constraint middleware implementations."""

from datetime import date
from typing import cast
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.middleware_config import (
    AuthorityDeferenceConfig,
    ClarificationGateConfig,
)
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.engine.context import AgentContext
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.middleware.coordination_protocol import (
    CoordinationMiddlewareContext,
)
from synthorg.engine.middleware.errors import ClarificationRequiredError
from synthorg.engine.middleware.models import (
    AgentMiddlewareContext,
    AssumptionViolationEvent,
    AssumptionViolationType,
)
from synthorg.engine.middleware.protocol import AgentMiddleware
from synthorg.engine.middleware.s1_constraints import (
    AssumptionViolationMiddleware,
    AuthorityDeferenceCoordinationMiddleware,
    AuthorityDeferenceGuard,
    ClarificationGateMiddleware,
    DelegationChainHashMiddleware,
    compute_task_content_hash,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from tests._shared import as_uuid

# ── Test helpers ──────────────────────────────────────────────────


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _task(
    *,
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = (),
    parent_task_id: str | None = None,
    delegation_chain: tuple[str, ...] = (),
) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Test task",
        description="A test task for S1 constraints",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="test-project",
        created_by="test-creator",
        acceptance_criteria=acceptance_criteria,
        parent_task_id=parent_task_id,
        delegation_chain=delegation_chain,
    )


def _mw_context(
    *,
    messages: tuple[ChatMessage, ...] = (),
    task: Task | None = None,
    metadata: dict[str, object] | None = None,
) -> AgentMiddlewareContext:
    identity = _identity()
    ctx = AgentContext.from_identity(identity)
    # Add messages to conversation
    for msg in messages:
        ctx = ctx.with_message(msg)
    t = task or _task()
    mw_ctx = AgentMiddlewareContext(
        agent_context=ctx,
        identity=identity,
        task=t,
        agent_id=str(identity.id),
        task_id=str(t.id),
        execution_id="exec-1",
    )
    if metadata:
        for k, v in metadata.items():
            mw_ctx = mw_ctx.with_metadata(k, v)
    return mw_ctx


def _coord_context(
    *,
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = (),
) -> CoordinationMiddlewareContext:
    task = _task(acceptance_criteria=acceptance_criteria)
    coord = CoordinationContext(
        task=task,
        available_agents=(_identity(),),
    )
    return CoordinationMiddlewareContext(
        coordination_context=coord,
    )


# ── AuthorityDeferenceGuard ──────────────────────────────────────


@pytest.mark.unit
class TestAuthorityDeferenceGuard:
    """AuthorityDeferenceGuard agent middleware."""

    def test_satisfies_protocol(self) -> None:
        mw = AuthorityDeferenceGuard()
        assert isinstance(mw, AgentMiddleware)

    def test_name(self) -> None:
        assert AuthorityDeferenceGuard().name == "authority_deference"

    async def test_disabled_passthrough(self) -> None:
        cfg = AuthorityDeferenceConfig(enabled=False)
        mw = AuthorityDeferenceGuard(config=cfg)
        ctx = _mw_context()
        result = await mw.before_agent(ctx)
        assert result is ctx

    async def test_strips_authority_cues(self) -> None:
        mw = AuthorityDeferenceGuard()
        ctx = _mw_context(
            messages=(
                ChatMessage(
                    role=MessageRole.USER,
                    content="You must complete this now.",
                ),
            )
        )
        result = await mw.before_agent(ctx)
        meta = cast("dict[str, object]", result.metadata["authority_deference"])
        assert cast("int", meta["detected_count"]) > 0

    async def test_no_cues_zero_count(self) -> None:
        mw = AuthorityDeferenceGuard()
        ctx = _mw_context(
            messages=(
                ChatMessage(
                    role=MessageRole.USER,
                    content="Please complete the task.",
                ),
            )
        )
        result = await mw.before_agent(ctx)
        meta = cast("dict[str, object]", result.metadata["authority_deference"])
        assert cast("int", meta["detected_count"]) == 0

    async def test_records_justification_header(self) -> None:
        mw = AuthorityDeferenceGuard()
        ctx = _mw_context()
        result = await mw.before_agent(ctx)
        meta = cast("dict[str, object]", result.metadata["authority_deference"])
        assert "merit" in cast("str", meta["justification_header"])


# ── AuthorityDeferenceCoordinationMiddleware ──────────────────────


@pytest.mark.unit
class TestAuthorityDeferenceCoordination:
    """Coordination-level authority deference."""

    async def test_disabled_passthrough(self) -> None:
        cfg = AuthorityDeferenceConfig(enabled=False)
        mw = AuthorityDeferenceCoordinationMiddleware(config=cfg)
        ctx = _coord_context()
        result = await mw.before_update_parent(ctx)
        assert result is ctx

    async def test_no_rollup_zero_count(self) -> None:
        mw = AuthorityDeferenceCoordinationMiddleware()
        ctx = _coord_context()
        result = await mw.before_update_parent(ctx)
        meta = cast(
            "dict[str, object]",
            result.metadata["authority_deference_coordination"],
        )
        assert cast("int", meta["detected_count"]) == 0


# ── AssumptionViolationMiddleware ─────────────────────────────────


@pytest.mark.unit
class TestAssumptionViolationMiddleware:
    """AssumptionViolationMiddleware detects broken assumptions."""

    def test_satisfies_protocol(self) -> None:
        mw = AssumptionViolationMiddleware()
        assert isinstance(mw, AgentMiddleware)

    def test_name(self) -> None:
        assert AssumptionViolationMiddleware().name == "assumption_violation"

    async def test_no_messages_passthrough(self) -> None:
        mw = AssumptionViolationMiddleware()
        ctx = _mw_context()
        result = await mw.after_model(ctx)
        assert result is ctx

    async def test_detects_precondition_changed(self) -> None:
        mw = AssumptionViolationMiddleware()
        ctx = _mw_context(
            messages=(
                ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=("The preconditions changed since the task was assigned."),
                ),
            )
        )
        result = await mw.after_model(ctx)
        violations = cast(
            "tuple[AssumptionViolationEvent, ...]",
            result.metadata["assumption_violations"],
        )
        assert len(violations) >= 1
        assert (
            violations[0].violation_type == AssumptionViolationType.PRECONDITION_CHANGED
        )

    async def test_detects_dependency_failed(self) -> None:
        mw = AssumptionViolationMiddleware()
        ctx = _mw_context(
            messages=(
                ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content="The dependency failed completely.",
                ),
            )
        )
        result = await mw.after_model(ctx)
        violations = cast(
            "tuple[AssumptionViolationEvent, ...]",
            result.metadata["assumption_violations"],
        )
        assert len(violations) >= 1
        assert violations[0].violation_type == AssumptionViolationType.DEPENDENCY_FAILED

    async def test_no_violation_clean_response(self) -> None:
        mw = AssumptionViolationMiddleware()
        ctx = _mw_context(
            messages=(
                ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content="Task completed successfully.",
                ),
            )
        )
        result = await mw.after_model(ctx)
        assert "assumption_violations" not in result.metadata


# ── ClarificationGateMiddleware ───────────────────────────────────


@pytest.mark.unit
class TestClarificationGateMiddleware:
    """ClarificationGateMiddleware validates acceptance criteria."""

    def test_name(self) -> None:
        assert ClarificationGateMiddleware().name == "clarification_gate"

    async def test_disabled_passthrough(self) -> None:
        cfg = ClarificationGateConfig(enabled=False)
        mw = ClarificationGateMiddleware(config=cfg)
        ctx = _coord_context()
        result = await mw.before_decompose(ctx)
        assert result is ctx

    async def test_rejects_no_criteria(self) -> None:
        mw = ClarificationGateMiddleware()
        ctx = _coord_context(acceptance_criteria=())
        with pytest.raises(ClarificationRequiredError) as exc_info:
            await mw.before_decompose(ctx)
        assert "no acceptance criteria" in exc_info.value.reasons[0]

    async def test_rejects_too_short_criteria(self) -> None:
        mw = ClarificationGateMiddleware()
        ctx = _coord_context(
            acceptance_criteria=(AcceptanceCriterion(description="short"),),
        )
        with pytest.raises(ClarificationRequiredError) as exc_info:
            await mw.before_decompose(ctx)
        assert any("too short" in r for r in exc_info.value.reasons)

    async def test_rejects_generic_criteria(self) -> None:
        mw = ClarificationGateMiddleware()
        ctx = _coord_context(
            acceptance_criteria=(AcceptanceCriterion(description="done"),),
        )
        with pytest.raises(ClarificationRequiredError) as exc_info:
            await mw.before_decompose(ctx)
        assert any("generic" in r for r in exc_info.value.reasons)

    async def test_accepts_specific_criteria(self) -> None:
        mw = ClarificationGateMiddleware()
        ctx = _coord_context(
            acceptance_criteria=(
                AcceptanceCriterion(
                    description="All unit tests pass with 80% coverage",
                ),
                AcceptanceCriterion(
                    description="API endpoint returns 200 OK",
                ),
            ),
        )
        result = await mw.before_decompose(ctx)
        assert result is ctx


# ── DelegationChainHashMiddleware ─────────────────────────────────


@pytest.mark.unit
class TestDelegationChainHashMiddleware:
    """DelegationChainHashMiddleware records content hashes."""

    def test_satisfies_protocol(self) -> None:
        mw = DelegationChainHashMiddleware()
        assert isinstance(mw, AgentMiddleware)

    def test_name(self) -> None:
        assert DelegationChainHashMiddleware().name == "delegation_chain_hash"

    async def test_records_hash(self) -> None:
        mw = DelegationChainHashMiddleware()
        ctx = _mw_context()
        result = await mw.before_agent(ctx)
        assert "delegation_chain_hash" in result.metadata
        assert (
            len(cast("str", result.metadata["delegation_chain_hash"])) == 64
        )  # SHA-256 hex

    async def test_hash_deterministic(self) -> None:
        mw = DelegationChainHashMiddleware()
        ctx = _mw_context()
        r1 = await mw.before_agent(ctx)
        r2 = await mw.before_agent(ctx)
        assert (
            r1.metadata["delegation_chain_hash"] == r2.metadata["delegation_chain_hash"]
        )

    async def test_different_tasks_different_hashes(self) -> None:
        mw = DelegationChainHashMiddleware()
        ctx1 = _mw_context()
        ctx2 = _mw_context(
            task=Task(
                id=as_uuid("task-2"),
                title="Different task",
                description="Different description",
                type=TaskType.DEVELOPMENT,
                priority=Priority.MEDIUM,
                project="test-project",
                created_by="test-creator",
            ),
        )
        r1 = await mw.before_agent(ctx1)
        r2 = await mw.before_agent(ctx2)
        assert (
            r1.metadata["delegation_chain_hash"] != r2.metadata["delegation_chain_hash"]
        )


@pytest.mark.unit
class TestComputeTaskContentHash:
    """compute_task_content_hash utility."""

    def test_deterministic(self) -> None:
        h1 = compute_task_content_hash("t", "d", ("c1",))
        h2 = compute_task_content_hash("t", "d", ("c1",))
        assert h1 == h2

    def test_different_input_different_hash(self) -> None:
        h1 = compute_task_content_hash("t1", "d", ())
        h2 = compute_task_content_hash("t2", "d", ())
        assert h1 != h2

    def test_hex_format(self) -> None:
        h = compute_task_content_hash("t", "d", ())
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
