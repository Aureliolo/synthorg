"""The run's ceilings become one published fact, and the loop can see them.

Before this seam existed, `agent_engine_context.py::_prepare_context` never
passed `context_capacity_tokens` or the budget checker's ceilings into
`AgentContext.from_identity`, so `ctx.token_ceiling` and
`ctx.context_capacity_tokens` were `None` for every task run: the ceiling that
actually fired lived only inside the enforcer's closure. These tests lock in
the fix: the checker publishes what it enforces, the context carries it, and
the system prompt declares it -- for a fresh run, an approval-resumed run, and
a checkpoint-resumed run alike.
"""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.tracker import CostTracker
from synthorg.core.agent import AgentIdentity
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.providers.enums import MessageRole
from tests._shared import UNWIRED_BUDGET, UNWIRED_ORG, as_uuid, engine_with
from tests.unit.budget.conftest import make_cost_record

from .conftest import make_completion_response

pytestmark = pytest.mark.unit


def _task(agent: AgentIdentity, **overrides: object) -> Task:
    return Task(
        id=as_uuid("ceiling-task"),
        title="Ship it",
        description="Deliver the slice.",
        type=TaskType.DEVELOPMENT,
        project="proj-ceiling",
        created_by="ceo",
        assigned_to=str(agent.id),
        status=TaskStatus.ASSIGNED,
        **overrides,  # type: ignore[arg-type]
    )


def _system_message_content(ctx: AgentContext) -> str:
    system_messages = [
        m.content for m in ctx.conversation if m.role is MessageRole.SYSTEM
    ]
    assert system_messages, "no system message was injected"
    return system_messages[0] or ""


class TestFreshRunStampsCeilings:
    """A fresh ``run()`` publishes the checker's ceilings onto the context."""

    async def test_token_ceiling_is_stamped_from_the_task(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        task = _task(sample_agent, hard_token_ceiling=500_000)
        provider = mock_provider_factory([make_completion_response()])
        engine = engine_with(provider)

        result = await engine.run(identity=sample_agent, task=task)

        ctx = result.execution_result.context
        assert ctx.token_ceiling == 500_000

    async def test_a_genuinely_zero_money_ceiling_stamps_as_none(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        # SessionCeilings spells "disabled" as 0; AgentContext's cost_ceiling
        # is a gt=0 field. A flat-rate connection's tightest per-run money
        # bound is genuinely 0, and stamping it verbatim would raise.
        task = _task(sample_agent, hard_token_ceiling=1_000_000, budget_limit=0.0)
        provider = mock_provider_factory([make_completion_response()])
        engine = engine_with(provider)

        result = await engine.run(identity=sample_agent, task=task)

        ctx = result.execution_result.context
        assert ctx.cost_ceiling is None
        assert ctx.token_ceiling == 1_000_000

    async def test_no_ceiling_at_all_stamps_none(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        task = _task(sample_agent, budget_limit=0.0)
        provider = mock_provider_factory([make_completion_response()])
        engine = engine_with(provider)

        result = await engine.run(identity=sample_agent, task=task)

        ctx = result.execution_result.context
        assert ctx.cost_ceiling is None
        assert ctx.token_ceiling is None

    async def test_context_capacity_is_resolved_from_the_provider(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        # MockCompletionProvider.get_model_capabilities reports 8192.
        task = _task(sample_agent, budget_limit=0.0)
        provider = mock_provider_factory([make_completion_response()])
        engine = engine_with(provider)

        result = await engine.run(identity=sample_agent, task=task)

        assert result.execution_result.context.context_capacity_tokens == 8192

    async def test_capability_lookup_failure_degrades_to_none(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        task = _task(sample_agent, budget_limit=0.0)
        provider = mock_provider_factory([make_completion_response()])
        provider.get_model_capabilities = AsyncMock(
            side_effect=ConnectionError("capability lookup unreachable")
        )
        engine = engine_with(provider)

        result = await engine.run(identity=sample_agent, task=task)

        assert result.execution_result.context.context_capacity_tokens is None
        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_the_enforcer_pair_is_published_when_wired(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        # With a BudgetEnforcer wired, the published ceiling is the enforcer's
        # own resolved pair (task override -> config fallback), not the
        # bare make_budget_checker(task) path.
        cfg = BudgetConfig(
            total_monthly=0.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
            run_hard_token_ceiling=2_000_000,
        )
        enforcer = BudgetEnforcer(
            budget_config=cfg, cost_tracker=CostTracker(budget_config=cfg)
        )
        task = _task(sample_agent, budget_limit=0.0)
        provider = mock_provider_factory([make_completion_response()])
        engine = engine_with(
            provider, budget=replace(UNWIRED_BUDGET, budget_enforcer=enforcer)
        )

        result = await engine.run(identity=sample_agent, task=task)

        assert result.execution_result.context.token_ceiling == 2_000_000


class TestFreshRunDeclaresTheIndicator:
    """The system prompt declares the budget when a ceiling exists."""

    async def test_budget_declared_when_token_ceiling_set(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        task = _task(sample_agent, hard_token_ceiling=1_500_000, budget_limit=0.0)
        provider = mock_provider_factory([make_completion_response()])
        engine = engine_with(provider)

        result = await engine.run(identity=sample_agent, task=task)

        content = _system_message_content(result.execution_result.context)
        assert "Budget:" in content
        assert "1,500,000" in content

    async def test_no_declaration_line_without_any_ceiling_or_capacity_field(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        task = _task(sample_agent, budget_limit=0.0)
        provider = mock_provider_factory([make_completion_response()])
        provider.get_model_capabilities = AsyncMock(
            side_effect=ConnectionError("capability lookup unreachable")
        )
        engine = engine_with(provider)

        result = await engine.run(identity=sample_agent, task=task)

        content = _system_message_content(result.execution_result.context)
        assert "[Context:" not in content


class TestApprovalResumeAlsoGetsAChecker:
    """A resumed parked run enforces budget too, not only a fresh run.

    Before ``_build_budget_checker`` existed, ``_execute_span`` was the
    single place that constructed a checker, reached from both the fresh-run
    and the approval-resume path via ``AgentExecuteRequest``. Moving
    construction ahead of the prompt build risks leaving the resume path with
    no checker at all unless it is also wired to build one.
    """

    async def test_resumed_run_enforces_its_token_ceiling(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        task = _task(sample_agent, hard_token_ceiling=10)
        provider = mock_provider_factory([])
        engine = engine_with(provider)
        parked_ctx = AgentContext.from_identity(
            sample_agent,
            task=task,
            token_ceiling=10,
        )
        parked_ctx = parked_ctx.model_copy(
            update={
                "accumulated_cost": parked_ctx.accumulated_cost.model_copy(
                    update={"input_tokens": 10}
                ),
            }
        )

        result = await engine.resume_parked_run(
            parked_context=parked_ctx,
            approval_id="appr-1",
            decision_message="Approved, continue.",
            approved=True,
        )

        assert (
            result.execution_result.termination_reason
            == TerminationReason.BUDGET_EXHAUSTED
        )

    async def test_resumed_run_enforces_its_project_budget(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        """The resumed checker must resolve the project budget itself,
        matching what a fresh run does, rather than defaulting it to 0.0
        (disabled) as a bare ``_build_budget_checker(task, agent_id,
        project_id=...)`` call would.
        """
        task = _task(sample_agent)
        cfg = BudgetConfig(
            total_monthly=1000.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        )
        tracker = CostTracker(budget_config=cfg)
        await tracker.record(make_cost_record(project_id=task.project, cost=50.0))
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)
        # Budget already exceeded by the recorded spend above: enforcement
        # only fires if the resumed checker actually resolves and applies it.
        project = Project(
            id=as_uuid(task.project),
            name="Test Project",
            budget=10.0,
            status=ProjectStatus.ACTIVE,
        )
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=project)
        provider = mock_provider_factory([])
        engine = engine_with(
            provider,
            budget=replace(UNWIRED_BUDGET, budget_enforcer=enforcer),
            org=replace(UNWIRED_ORG, project_repo=repo),
        )
        parked_ctx = AgentContext.from_identity(sample_agent, task=task)

        result = await engine.resume_parked_run(
            parked_context=parked_ctx,
            approval_id="appr-2",
            decision_message="Approved, continue.",
            approved=True,
        )

        assert (
            result.execution_result.termination_reason
            == TerminationReason.BUDGET_EXHAUSTED
        )


class TestApprovalResumeSyncsAChangedCeiling:
    """A ceiling changed while the run sat parked awaiting approval applies
    to the resumed dispatch, not the stale number the park was taken under
    -- the approval-resume sibling of ``TestCheckpointResumeSyncsAChangedCeiling``.
    """

    async def test_ctx_ceiling_follows_the_rebuilt_checker(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        task = _task(sample_agent, hard_token_ceiling=750_000)
        provider = mock_provider_factory([make_completion_response()])
        engine = engine_with(provider)
        parked_ctx = AgentContext.from_identity(
            sample_agent, task=task, token_ceiling=500_000
        )

        result = await engine.resume_parked_run(
            parked_context=parked_ctx,
            approval_id="appr-3",
            decision_message="Approved, continue.",
            approved=True,
        )

        assert result.execution_result.context.token_ceiling == 750_000

    async def test_ctx_ceiling_clears_when_the_checker_is_disabled(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        """A ceiling disabled entirely while the run sat parked clears the
        resumed context rather than leaving it reporting against the
        parked run's stale threshold.
        """
        task = _task(sample_agent)  # no ceilings: _build_budget_checker -> None
        provider = mock_provider_factory([make_completion_response()])
        engine = engine_with(provider)
        parked_ctx = AgentContext.from_identity(
            sample_agent, task=task, token_ceiling=500_000
        )

        result = await engine.resume_parked_run(
            parked_context=parked_ctx,
            approval_id="appr-4",
            decision_message="Approved, continue.",
            approved=True,
        )

        assert result.execution_result.context.token_ceiling is None


class TestCheckpointResumeAlsoGetsTheTurnBoundarySignals:
    """A checkpoint-resumed run must not lose the budget signal or the
    produce-early nudge -- carrying a real ``ctx.token_ceiling`` is only half
    of what renders them, since both are also gated on the parameters
    ``loop.execute`` is called with.
    """

    async def test_execute_resumed_loop_passes_both_signals(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        task = _task(sample_agent, hard_token_ceiling=500_000)
        provider = mock_provider_factory([])
        engine = engine_with(provider)
        checkpoint_ctx = AgentContext.from_identity(
            sample_agent, task=task, token_ceiling=500_000
        )

        captured: dict[str, object] = {}

        async def _fake_execute(**kwargs: object) -> ExecutionResult:
            captured.update(kwargs)
            ctx = kwargs["context"]
            assert isinstance(ctx, AgentContext)
            return ExecutionResult(
                context=ctx, termination_reason=TerminationReason.COMPLETED
            )

        engine._loop.execute = _fake_execute  # type: ignore[method-assign]

        await engine._execute_resumed_loop(
            checkpoint_ctx,
            agent_id=str(sample_agent.id),
            task_id=str(task.id),
        )

        assert captured["budget_signal_config"] is not None
        assert captured["produce_early_percent"] is not None


class TestCheckpointResumeSyncsAChangedCeiling:
    """A ceiling changed while the checkpoint sat parked applies to the
    resumed run, not the number the checkpoint was taken under: the checker
    rebuilt for resume is what ``loop.execute`` enforces, and the context
    it is handed must read the same threshold.
    """

    async def test_context_ceiling_follows_the_rebuilt_checker(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        # hard_token_ceiling is what _build_budget_checker reads with no org
        # enforcer wired -- it stands in for the ceiling having changed while
        # the checkpoint was parked, in this case raised past what the
        # checkpointed context still carries.
        task = _task(sample_agent, hard_token_ceiling=750_000)
        provider = mock_provider_factory([])
        engine = engine_with(provider)
        checkpoint_ctx = AgentContext.from_identity(
            sample_agent, task=task, token_ceiling=500_000
        )

        captured: dict[str, object] = {}

        async def _fake_execute(**kwargs: object) -> ExecutionResult:
            captured.update(kwargs)
            ctx = kwargs["context"]
            assert isinstance(ctx, AgentContext)
            return ExecutionResult(
                context=ctx, termination_reason=TerminationReason.COMPLETED
            )

        engine._loop.execute = _fake_execute  # type: ignore[method-assign]

        await engine._execute_resumed_loop(
            checkpoint_ctx,
            agent_id=str(sample_agent.id),
            task_id=str(task.id),
        )

        resumed_ctx = captured["context"]
        assert isinstance(resumed_ctx, AgentContext)
        assert resumed_ctx.token_ceiling == 750_000

    async def test_context_ceiling_clears_when_the_checker_is_disabled(
        self,
        sample_agent: AgentIdentity,
        mock_provider_factory: type,
    ) -> None:
        """A ceiling disabled entirely while the checkpoint sat parked
        clears the resumed context -- the checkpoint-resume sibling of
        ``TestApprovalResumeSyncsAChangedCeiling``'s disabled-ceiling case.
        """
        task = _task(sample_agent)  # no ceilings: _build_budget_checker -> None
        provider = mock_provider_factory([])
        engine = engine_with(provider)
        checkpoint_ctx = AgentContext.from_identity(
            sample_agent, task=task, token_ceiling=500_000
        )

        captured: dict[str, object] = {}

        async def _fake_execute(**kwargs: object) -> ExecutionResult:
            captured.update(kwargs)
            ctx = kwargs["context"]
            assert isinstance(ctx, AgentContext)
            return ExecutionResult(
                context=ctx, termination_reason=TerminationReason.COMPLETED
            )

        engine._loop.execute = _fake_execute  # type: ignore[method-assign]

        await engine._execute_resumed_loop(
            checkpoint_ctx,
            agent_id=str(sample_agent.id),
            task_id=str(task.id),
        )

        resumed_ctx = captured["context"]
        assert isinstance(resumed_ctx, AgentContext)
        assert resumed_ctx.token_ceiling is None
