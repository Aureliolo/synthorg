"""Tests for the real ShadowEvaluationGuard."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import TaskType
from synthorg.engine.evolution.config import ShadowEvaluationConfig
from synthorg.engine.evolution.guards.shadow_evaluation import (
    ShadowEvaluationGuard,
)
from synthorg.engine.evolution.guards.shadow_protocol import (
    ShadowTaskOutcome,
)
from synthorg.engine.evolution.guards.shadow_providers import (
    ConfiguredShadowTaskProvider,
)
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationProposal,
    AdaptationSource,
)
from synthorg.versioning.models import VersionSnapshot
from tests._shared import as_uuid


def _make_identity(agent_id: str = "agent-001") -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="test-role",
        department="test-dept",
        model=ModelConfig(
            provider="test-provider",
            model_id="test-medium-001",
        ),
        hiring_date=datetime.now(UTC).date(),
    )


def _make_task(task_id: str) -> Task:
    return Task(
        id=as_uuid(task_id),
        title=f"Task {task_id}",
        description=f"Description for {task_id}",
        type=TaskType.DEVELOPMENT,
        project="proj-shadow",
        created_by="test-creator",
        acceptance_criteria=(AcceptanceCriterion(description="Criterion 1"),),
    )


def _make_proposal(
    *,
    agent_id: str = "agent-001",
    axis: AdaptationAxis = AdaptationAxis.IDENTITY,
    changes: dict[str, object] | None = None,
) -> AdaptationProposal:
    return AdaptationProposal(
        agent_id=agent_id,
        axis=axis,
        description="Test proposal",
        changes=changes or {"name": "Evolved"},
        confidence=0.9,
        source=AdaptationSource.SUCCESS,
    )


class _FakeIdentityStore:
    """Minimal IdentityVersionStore stub returning a fixed identity."""

    def __init__(
        self,
        *,
        identity: AgentIdentity | None,
    ) -> None:
        self._identity = identity

    async def put(
        self,
        agent_id: str,
        identity: AgentIdentity,
        *,
        saved_by: str,
    ) -> VersionSnapshot[AgentIdentity]:
        msg = "put() not used by ShadowEvaluationGuard"
        raise NotImplementedError(msg)

    async def get_current(self, agent_id: str) -> AgentIdentity | None:
        return self._identity

    async def get_version(
        self,
        agent_id: str,
        version: int,
    ) -> AgentIdentity | None:
        return None

    async def list_versions(
        self,
        agent_id: str,
    ) -> tuple[VersionSnapshot[AgentIdentity], ...]:
        return ()

    async def set_current(
        self,
        agent_id: str,
        version: int,
    ) -> AgentIdentity:
        msg = "set_current() not used by ShadowEvaluationGuard"
        raise NotImplementedError(msg)


OutcomeFactory = Callable[[bool, AdaptationProposal | None, Task], ShadowTaskOutcome]


class _ScriptedRunner:
    """Configurable runner used in the tests.

    Callers supply an ``outcome_fn(proposal_is_adapted, proposal, task)``
    that returns a ``ShadowTaskOutcome``.  The runner records every call
    for assertions.
    """

    def __init__(self, outcome_fn: OutcomeFactory) -> None:
        self._outcome_fn = outcome_fn
        self.calls: list[tuple[bool, str]] = []

    async def run(
        self,
        *,
        identity: AgentIdentity,
        proposal: AdaptationProposal | None,
        task: Task,
        timeout_seconds: float,
    ) -> ShadowTaskOutcome:
        self.calls.append((proposal is not None, str(task.id)))
        return self._outcome_fn(proposal is not None, proposal, task)


def _baseline_better_scripts(
    *,
    baseline_quality: float,
    adapted_quality: float,
) -> OutcomeFactory:
    def _fn(
        adapted: bool,
        proposal: AdaptationProposal | None,
        task: Task,
    ) -> ShadowTaskOutcome:
        return ShadowTaskOutcome(
            success=True,
            quality_score=adapted_quality if adapted else baseline_quality,
        )

    return _fn


def _config(
    *,
    probe_tasks: tuple[Task, ...] | None = None,
    score_tol: float = 0.05,
    pass_tol: float = 0.10,
) -> ShadowEvaluationConfig:
    tasks = (
        probe_tasks
        if probe_tasks is not None
        else (_make_task("probe-1"), _make_task("probe-2"))
    )
    return ShadowEvaluationConfig(
        probe_tasks=tasks,
        sample_size=5,
        timeout_per_task_seconds=5.0,
        score_regression_tolerance=score_tol,
        pass_rate_regression_tolerance=pass_tol,
    )


def _build_guard(
    *,
    config: ShadowEvaluationConfig,
    runner: _ScriptedRunner,
    identity: AgentIdentity | None,
) -> ShadowEvaluationGuard:
    provider = ConfiguredShadowTaskProvider(config=config)
    store = _FakeIdentityStore(identity=identity)
    return ShadowEvaluationGuard(
        config=config,
        task_provider=provider,
        runner=runner,
        identity_store=store,
    )


@pytest.mark.unit
class TestShadowEvaluationGuardApproval:
    async def test_approves_when_adapted_matches_baseline(self) -> None:
        runner = _ScriptedRunner(
            _baseline_better_scripts(
                baseline_quality=0.8,
                adapted_quality=0.8,
            )
        )
        guard = _build_guard(
            config=_config(),
            runner=runner,
            identity=_make_identity(),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is True
        assert "Shadow eval passed" in decision.reason

    async def test_approves_when_adapted_beats_baseline(self) -> None:
        runner = _ScriptedRunner(
            _baseline_better_scripts(
                baseline_quality=0.6,
                adapted_quality=0.9,
            )
        )
        guard = _build_guard(
            config=_config(),
            runner=runner,
            identity=_make_identity(),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is True

    async def test_approves_within_tolerance(self) -> None:
        runner = _ScriptedRunner(
            _baseline_better_scripts(
                baseline_quality=0.8,
                adapted_quality=0.77,
            )
        )
        guard = _build_guard(
            config=_config(score_tol=0.05),
            runner=runner,
            identity=_make_identity(),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is True


@pytest.mark.unit
class TestShadowEvaluationGuardRejection:
    async def test_rejects_score_regression_beyond_tolerance(self) -> None:
        runner = _ScriptedRunner(
            _baseline_better_scripts(
                baseline_quality=0.9,
                adapted_quality=0.5,
            )
        )
        guard = _build_guard(
            config=_config(score_tol=0.05),
            runner=runner,
            identity=_make_identity(),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is False
        assert "mean quality dropped" in decision.reason

    async def test_rejects_pass_rate_regression(self) -> None:
        # Baseline: 2/2 pass, Adapted: 0/2 pass -> pass rate drops 100%
        def _fn(
            adapted: bool,
            proposal: AdaptationProposal | None,
            task: Task,
        ) -> ShadowTaskOutcome:
            if adapted:
                return ShadowTaskOutcome(
                    success=False,
                    error="adapted failure",
                )
            return ShadowTaskOutcome(success=True, quality_score=0.9)

        runner = _ScriptedRunner(_fn)
        guard = _build_guard(
            config=_config(pass_tol=0.1),
            runner=runner,
            identity=_make_identity(),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is False
        assert "pass rate dropped" in decision.reason

    async def test_rejects_empty_suite(self) -> None:
        """Guard must reject when the task provider returns an empty tuple.

        Uses an ``_EmptyTaskProvider`` because the cross-field validator
        on ``ShadowEvaluationConfig`` prevents constructing a
        ``task_provider='configured'`` config with empty
        ``probe_tasks``; the runtime empty-suite path still needs
        coverage for the ``recent_history`` branch.
        """

        class _EmptyTaskProvider:
            @property
            def name(self) -> str:
                return "empty"

            async def sample(
                self,
                *,
                agent_id: str,
                sample_size: int,
            ) -> tuple[Task, ...]:
                return ()

        runner = _ScriptedRunner(
            _baseline_better_scripts(
                baseline_quality=0.9,
                adapted_quality=0.9,
            )
        )
        config = _config()
        guard = ShadowEvaluationGuard(
            config=config,
            task_provider=_EmptyTaskProvider(),
            runner=runner,
            identity_store=_FakeIdentityStore(identity=_make_identity()),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is False
        assert "probe task suite is empty" in decision.reason
        assert runner.calls == []  # runner never invoked

    async def test_rejects_when_identity_missing(self) -> None:
        runner = _ScriptedRunner(
            _baseline_better_scripts(
                baseline_quality=0.9,
                adapted_quality=0.9,
            )
        )
        guard = _build_guard(
            config=_config(),
            runner=runner,
            identity=None,
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is False
        assert "baseline identity not found" in decision.reason
        assert runner.calls == []

    async def test_rejects_when_baseline_all_fail(self) -> None:
        def _fn(
            adapted: bool,
            proposal: AdaptationProposal | None,
            task: Task,
        ) -> ShadowTaskOutcome:
            return ShadowTaskOutcome(success=False, error="always fails")

        runner = _ScriptedRunner(_fn)
        guard = _build_guard(
            config=_config(),
            runner=runner,
            identity=_make_identity(),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is False
        assert "baseline had zero successful runs" in decision.reason

    async def test_counts_runner_exception_as_adapted_failure(self) -> None:
        def _fn(
            adapted: bool,
            proposal: AdaptationProposal | None,
            task: Task,
        ) -> ShadowTaskOutcome:
            if adapted:
                msg = "boom"
                raise RuntimeError(msg)
            return ShadowTaskOutcome(success=True, quality_score=0.9)

        runner = _ScriptedRunner(_fn)
        guard = _build_guard(
            config=_config(pass_tol=0.0),
            runner=runner,
            identity=_make_identity(),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is False
        assert "pass rate dropped" in decision.reason


@pytest.mark.unit
class TestShadowEvaluationGuardMetadata:
    async def test_name_is_shadow_evaluation_guard(self) -> None:
        runner = _ScriptedRunner(
            _baseline_better_scripts(
                baseline_quality=0.9,
                adapted_quality=0.9,
            )
        )
        guard = _build_guard(
            config=_config(),
            runner=runner,
            identity=_make_identity(),
        )
        assert guard.name == "ShadowEvaluationGuard"

    async def test_runs_baseline_and_adapted_in_parallel(self) -> None:
        barrier = asyncio.Barrier(2)

        async def _delayed_outcome(
            adapted: bool,
            proposal: AdaptationProposal | None,
            task: Task,
        ) -> ShadowTaskOutcome:
            return ShadowTaskOutcome(success=True, quality_score=0.9)

        async def _run(
            *,
            identity: AgentIdentity,
            proposal: AdaptationProposal | None,
            task: Task,
            timeout_seconds: float,
        ) -> ShadowTaskOutcome:
            # Both baseline and adapted must reach the barrier for this
            # to progress; if the guard ran them sequentially the test
            # would hang.
            async with asyncio.timeout(1.0):
                await barrier.wait()
            return await _delayed_outcome(proposal is not None, proposal, task)

        class _ParallelRunner:
            async def run(
                self,
                *,
                identity: AgentIdentity,
                proposal: AdaptationProposal | None,
                task: Task,
                timeout_seconds: float,
            ) -> ShadowTaskOutcome:
                return await _run(
                    identity=identity,
                    proposal=proposal,
                    task=task,
                    timeout_seconds=timeout_seconds,
                )

        config = _config(probe_tasks=(_make_task("only"),))
        provider = ConfiguredShadowTaskProvider(config=config)
        guard = ShadowEvaluationGuard(
            config=config,
            task_provider=provider,
            runner=_ParallelRunner(),
            identity_store=_FakeIdentityStore(identity=_make_identity()),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is True


@pytest.mark.unit
class TestShadowEvaluationGuardAxes:
    async def test_strategy_selection_axis_runs_real_evaluation(self) -> None:
        runner = _ScriptedRunner(
            _baseline_better_scripts(
                baseline_quality=0.8,
                adapted_quality=0.9,
            )
        )
        guard = _build_guard(
            config=_config(),
            runner=runner,
            identity=_make_identity(),
        )
        decision = await guard.evaluate(
            _make_proposal(axis=AdaptationAxis.STRATEGY_SELECTION)
        )
        assert decision.approved is True
        assert any(is_adapted for is_adapted, _ in runner.calls)
        assert any(not is_adapted for is_adapted, _ in runner.calls)

    async def test_prompt_template_axis_runs_real_evaluation(self) -> None:
        runner = _ScriptedRunner(
            _baseline_better_scripts(
                baseline_quality=0.8,
                adapted_quality=0.9,
            )
        )
        guard = _build_guard(
            config=_config(),
            runner=runner,
            identity=_make_identity(),
        )
        decision = await guard.evaluate(
            _make_proposal(axis=AdaptationAxis.PROMPT_TEMPLATE)
        )
        assert decision.approved is True


@pytest.mark.unit
class TestShadowEvaluationGuardResilience:
    async def test_runner_timeout_counts_as_adapted_failure(self) -> None:
        """A runner that exceeds ``timeout_per_task_seconds`` is treated
        as a failed adapted task -- the guard enforces its own timeout
        via ``asyncio.timeout`` so a misbehaving runner cannot hang."""

        async def _slow_runner_run(
            *,
            identity: AgentIdentity,
            proposal: AdaptationProposal | None,
            task: Task,
            timeout_seconds: float,
        ) -> ShadowTaskOutcome:
            if proposal is not None:
                # Adapted side blocks indefinitely on a never-set Event
                # so the guard's ``asyncio.timeout`` is the only thing
                # that can unblock the task -- cancel-safe and
                # deterministic (no wall-clock flakiness).
                await asyncio.Event().wait()
            return ShadowTaskOutcome(success=True, quality_score=0.9)

        class _SlowRunner:
            async def run(
                self,
                *,
                identity: AgentIdentity,
                proposal: AdaptationProposal | None,
                task: Task,
                timeout_seconds: float,
            ) -> ShadowTaskOutcome:
                return await _slow_runner_run(
                    identity=identity,
                    proposal=proposal,
                    task=task,
                    timeout_seconds=timeout_seconds,
                )

        config = ShadowEvaluationConfig(
            probe_tasks=(_make_task("probe-1"),),
            sample_size=5,
            timeout_per_task_seconds=0.05,
            score_regression_tolerance=0.0,
            pass_rate_regression_tolerance=0.0,
        )
        provider = ConfiguredShadowTaskProvider(config=config)
        guard = ShadowEvaluationGuard(
            config=config,
            task_provider=provider,
            runner=_SlowRunner(),
            identity_store=_FakeIdentityStore(identity=_make_identity()),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is False
        assert "pass rate dropped" in decision.reason

    async def test_retry_exhausted_error_propagates(self) -> None:
        """Provider-level ``RetryExhaustedError`` must propagate so
        infrastructure outages are not misattributed as regressions."""
        from synthorg.providers.errors import ProviderTimeoutError
        from synthorg.providers.resilience.errors import RetryExhaustedError

        def _fn(
            adapted: bool,
            proposal: AdaptationProposal | None,
            task: Task,
        ) -> ShadowTaskOutcome:
            if adapted:
                raise RetryExhaustedError(
                    ProviderTimeoutError("upstream timeout"),
                )
            return ShadowTaskOutcome(success=True, quality_score=0.9)

        runner = _ScriptedRunner(_fn)
        guard = _build_guard(
            config=_config(),
            runner=runner,
            identity=_make_identity(),
        )
        with pytest.raises(ExceptionGroup) as exc_info:
            await guard.evaluate(_make_proposal())
        # TaskGroup wraps the RetryExhaustedError in an ExceptionGroup.
        assert any(
            isinstance(e, RetryExhaustedError)
            for e in _flatten_exceptions(exc_info.value)
        )


def _flatten_exceptions(exc: BaseException) -> list[BaseException]:
    """Recursively flatten ``ExceptionGroup`` into a list of leaves."""
    if isinstance(exc, BaseExceptionGroup):
        out: list[BaseException] = []
        for inner in exc.exceptions:
            out.extend(_flatten_exceptions(inner))
        return out
    return [exc]


@pytest.mark.unit
class TestShadowEvaluationGuardBoundaries:
    """Exercise the tolerance comparisons at their exact boundaries."""

    async def _run_with_scores(  # noqa: PLR0913
        self,
        *,
        baseline_score: float,
        adapted_score: float,
        baseline_pass: bool,
        adapted_pass: bool,
        score_tol: float,
        pass_rate_tol: float,
    ) -> bool:
        def _fn(
            adapted: bool,
            proposal: AdaptationProposal | None,
            task: Task,
        ) -> ShadowTaskOutcome:
            if adapted:
                return ShadowTaskOutcome(
                    success=adapted_pass,
                    quality_score=adapted_score,
                )
            return ShadowTaskOutcome(
                success=baseline_pass,
                quality_score=baseline_score,
            )

        runner = _ScriptedRunner(_fn)
        config = ShadowEvaluationConfig(
            probe_tasks=(_make_task("probe-1"),),
            sample_size=1,
            score_regression_tolerance=score_tol,
            pass_rate_regression_tolerance=pass_rate_tol,
        )
        guard = _build_guard(
            config=config,
            runner=runner,
            identity=_make_identity(),
        )
        return (await guard.evaluate(_make_proposal())).approved

    async def test_score_regression_exactly_at_tolerance_approves(self) -> None:
        # Exact binary fractions: 0.5 - 0.25 = 0.25 (no fp drift),
        # tolerance 0.25, delta > tolerance -> False, so approve.
        approved = await self._run_with_scores(
            baseline_score=0.5,
            adapted_score=0.25,
            baseline_pass=True,
            adapted_pass=True,
            score_tol=0.25,
            pass_rate_tol=0.0,
        )
        assert approved is True

    async def test_score_regression_just_over_tolerance_rejects(self) -> None:
        # delta 0.375 > tolerance 0.25 -> reject.
        approved = await self._run_with_scores(
            baseline_score=0.5,
            adapted_score=0.125,
            baseline_pass=True,
            adapted_pass=True,
            score_tol=0.25,
            pass_rate_tol=0.0,
        )
        assert approved is False

    async def test_pass_rate_regression_exactly_at_budget_approves(self) -> None:
        # Single task: baseline passes, adapted passes -> delta=0, budget=0
        approved = await self._run_with_scores(
            baseline_score=0.5,
            adapted_score=0.5,
            baseline_pass=True,
            adapted_pass=True,
            score_tol=0.25,
            pass_rate_tol=0.10,
        )
        assert approved is True


@pytest.mark.unit
class TestRecentTaskHistoryProvider:
    """Edge cases for the ``recent_history`` strategy."""

    async def test_empty_history_rejects_proposal(self) -> None:
        from synthorg.engine.evolution.guards.shadow_providers import (
            RecentTaskHistoryProvider,
        )

        async def _empty_sampler(
            agent_id: str,
            sample_size: int,
        ) -> tuple[Task, ...]:
            return ()

        config = ShadowEvaluationConfig(
            task_provider="recent_history",
            sample_size=5,
        )
        runner = _ScriptedRunner(
            lambda adapted, proposal, task: ShadowTaskOutcome(
                success=True, quality_score=0.9
            )
        )
        guard = ShadowEvaluationGuard(
            config=config,
            task_provider=RecentTaskHistoryProvider(sampler=_empty_sampler),
            runner=runner,
            identity_store=_FakeIdentityStore(identity=_make_identity()),
        )
        decision = await guard.evaluate(_make_proposal())
        assert decision.approved is False
        assert "probe task suite is empty" in decision.reason

    async def test_sampler_result_is_deep_copied(self) -> None:
        """Mutations on sampled tasks must not leak to the sampler's store."""
        from synthorg.engine.evolution.guards.shadow_providers import (
            RecentTaskHistoryProvider,
        )

        original = _make_task("probe-immutable")

        async def _sampler(
            agent_id: str,
            sample_size: int,
        ) -> tuple[Task, ...]:
            return (original,)

        provider = RecentTaskHistoryProvider(sampler=_sampler)
        sampled = await provider.sample(agent_id="agent-1", sample_size=1)
        assert sampled[0] is not original

    async def test_sample_size_zero_returns_empty(self) -> None:
        """``sample_size=0`` short-circuits before calling the sampler."""
        from synthorg.engine.evolution.guards.shadow_providers import (
            ConfiguredShadowTaskProvider as _Configured,
        )
        from synthorg.engine.evolution.guards.shadow_providers import (
            RecentTaskHistoryProvider,
        )

        sampler_called = False

        async def _sampler(
            agent_id: str,
            sample_size: int,
        ) -> tuple[Task, ...]:
            nonlocal sampler_called
            sampler_called = True
            return ()

        recent = RecentTaskHistoryProvider(sampler=_sampler)
        assert await recent.sample(agent_id="a", sample_size=0) == ()
        assert sampler_called is False

        config = ShadowEvaluationConfig(
            probe_tasks=(_make_task("probe-1"),),
            sample_size=5,
        )
        configured = _Configured(config=config)
        assert await configured.sample(agent_id="a", sample_size=0) == ()


@pytest.mark.unit
class TestShadowAggregateInvariants:
    """``_ShadowAggregate.__post_init__`` rejects inconsistent stats."""

    def test_negative_totals_rejected(self) -> None:
        from synthorg.engine.evolution.guards.shadow_evaluation import (
            _ShadowAggregate,
        )

        with pytest.raises(ValueError, match="non-negative"):
            _ShadowAggregate(
                total=-1,
                success_count=0,
                pass_rate=0.0,
                score_mean=None,
                errors=(),
            )

    def test_success_count_exceeds_total_rejected(self) -> None:
        from synthorg.engine.evolution.guards.shadow_evaluation import (
            _ShadowAggregate,
        )

        with pytest.raises(ValueError, match="exceed total"):
            _ShadowAggregate(
                total=3,
                success_count=5,
                pass_rate=5 / 3,
                score_mean=None,
                errors=(),
            )

    def test_pass_rate_inconsistent_rejected(self) -> None:
        from synthorg.engine.evolution.guards.shadow_evaluation import (
            _ShadowAggregate,
        )

        with pytest.raises(ValueError, match="inconsistent"):
            _ShadowAggregate(
                total=4,
                success_count=2,
                pass_rate=0.9,
                score_mean=None,
                errors=(),
            )
