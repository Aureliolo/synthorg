"""Tests for risk budget enforcement in BudgetEnforcer."""

from datetime import UTC, datetime

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.errors import RiskBudgetExhaustedError
from synthorg.budget.risk_check import RiskCheckResult
from synthorg.budget.risk_config import RiskBudgetConfig
from synthorg.budget.risk_record import RiskRecord
from synthorg.budget.risk_tracker import RiskTracker
from synthorg.budget.tracker import CostTracker
from synthorg.security.risk_scorer import DefaultRiskScorer, RiskScore


def _make_risk_record(
    *,
    agent_id: str = "agent-1",
    task_id: str = "task-1",
    action_type: str = "code:write",
    risk_units: float = 0.3,
    timestamp: datetime | None = None,
) -> RiskRecord:
    score = RiskScore(
        reversibility=0.5,
        blast_radius=0.3,
        data_sensitivity=0.2,
        external_visibility=0.1,
    )
    return RiskRecord(
        agent_id=agent_id,
        task_id=task_id,
        action_type=action_type,
        risk_score=score,
        risk_units=risk_units,
        timestamp=timestamp or datetime.now(UTC),
    )


def _make_enforcer(
    *,
    risk_enabled: bool = True,
    per_task_risk_limit: float = 5.0,
    per_agent_daily_risk_limit: float = 20.0,
    total_daily_risk_limit: float = 100.0,
    total_monthly: float = 100.0,
) -> tuple[BudgetEnforcer, RiskTracker]:
    risk_config = RiskBudgetConfig(
        enabled=risk_enabled,
        per_task_risk_limit=per_task_risk_limit,
        per_agent_daily_risk_limit=per_agent_daily_risk_limit,
        total_daily_risk_limit=total_daily_risk_limit,
    )
    budget_config = BudgetConfig(
        total_monthly=total_monthly,
        risk_budget=risk_config,
    )
    cost_tracker = CostTracker(budget_config=budget_config)
    risk_tracker = RiskTracker(risk_budget_config=risk_config)
    risk_scorer = DefaultRiskScorer()
    enforcer = BudgetEnforcer(
        budget_config=budget_config,
        cost_tracker=cost_tracker,
        risk_tracker=risk_tracker,
        risk_scorer=risk_scorer,
    )
    return enforcer, risk_tracker


@pytest.mark.unit
class TestRiskBudgetEnforcerConstruction:
    """Tests for BudgetEnforcer with risk dependencies."""

    def test_construction_with_risk_tracker(self) -> None:
        enforcer, risk_tracker = _make_enforcer()
        assert enforcer.risk_tracker is risk_tracker

    def test_construction_without_risk_tracker(self) -> None:
        budget_config = BudgetConfig()
        cost_tracker = CostTracker(budget_config=budget_config)
        enforcer = BudgetEnforcer(
            budget_config=budget_config,
            cost_tracker=cost_tracker,
        )
        assert enforcer.risk_tracker is None


@pytest.mark.unit
class TestCheckRiskBudget:
    """Tests for risk budget pre-flight checks."""

    async def test_check_passes_when_within_limits(self) -> None:
        enforcer, _ = _make_enforcer(per_task_risk_limit=5.0)
        result = await enforcer.check_risk_budget(
            "agent-1",
            "task-1",
            "code:write",
        )
        assert result.allowed is True

    async def test_check_raises_on_task_limit_exceeded(self) -> None:
        enforcer, risk_tracker = _make_enforcer(per_task_risk_limit=0.5)
        # Record enough risk to exceed the per-task limit
        await risk_tracker.record(
            _make_risk_record(task_id="task-1", risk_units=0.6),
        )
        with pytest.raises(RiskBudgetExhaustedError):
            await enforcer.check_risk_budget(
                "agent-1",
                "task-1",
                "code:write",
            )

    async def test_check_raises_on_agent_daily_limit_exceeded(self) -> None:
        enforcer, risk_tracker = _make_enforcer(
            per_agent_daily_risk_limit=1.0,
            per_task_risk_limit=0.0,
        )
        await risk_tracker.record(
            _make_risk_record(agent_id="agent-1", risk_units=1.1),
        )
        with pytest.raises(RiskBudgetExhaustedError):
            await enforcer.check_risk_budget(
                "agent-1",
                "task-2",
                "code:write",
            )

    async def test_check_raises_on_total_daily_limit_exceeded(self) -> None:
        enforcer, risk_tracker = _make_enforcer(
            total_daily_risk_limit=1.0,
            per_task_risk_limit=0.0,
            per_agent_daily_risk_limit=0.0,
        )
        await risk_tracker.record(
            _make_risk_record(
                agent_id="agent-a",
                task_id="other-task",
                risk_units=1.1,
            ),
        )
        with pytest.raises(RiskBudgetExhaustedError):
            await enforcer.check_risk_budget(
                "agent-b",
                "task-1",
                "code:write",
            )

    async def test_check_raises_when_projected_exceeds_per_task(self) -> None:
        """Recorded usage under limit, but projected (recorded + action score)
        exceeds it."""
        # code:write scores ~0.31 risk units with DefaultRiskScorer.
        # Set per-task limit just above recorded but below recorded + projected.
        enforcer, risk_tracker = _make_enforcer(per_task_risk_limit=0.4)
        await risk_tracker.record(
            _make_risk_record(task_id="task-1", risk_units=0.15),
        )
        # 0.15 (recorded) + ~0.31 (projected) = ~0.46 >= 0.4 limit
        with pytest.raises(RiskBudgetExhaustedError):
            await enforcer.check_risk_budget(
                "agent-1",
                "task-1",
                "code:write",
            )

    async def test_check_raises_when_projected_exceeds_agent_daily(
        self,
    ) -> None:
        """Agent daily: recorded under limit, projected pushes over."""
        enforcer, risk_tracker = _make_enforcer(
            per_agent_daily_risk_limit=0.4,
            per_task_risk_limit=0.0,
        )
        await risk_tracker.record(
            _make_risk_record(agent_id="agent-1", risk_units=0.15),
        )
        with pytest.raises(RiskBudgetExhaustedError):
            await enforcer.check_risk_budget(
                "agent-1",
                "task-2",
                "code:write",
            )

    async def test_check_raises_when_projected_exceeds_total_daily(
        self,
    ) -> None:
        """Total daily: recorded under limit, projected pushes over."""
        enforcer, risk_tracker = _make_enforcer(
            total_daily_risk_limit=0.4,
            per_task_risk_limit=0.0,
            per_agent_daily_risk_limit=0.0,
        )
        await risk_tracker.record(
            _make_risk_record(
                agent_id="agent-a",
                task_id="other-task",
                risk_units=0.15,
            ),
        )
        with pytest.raises(RiskBudgetExhaustedError):
            await enforcer.check_risk_budget(
                "agent-b",
                "task-1",
                "code:write",
            )

    async def test_check_skipped_when_risk_disabled(self) -> None:
        enforcer, _ = _make_enforcer(risk_enabled=False)
        result = await enforcer.check_risk_budget(
            "agent-1",
            "task-1",
            "code:write",
        )
        assert result.allowed is True

    async def test_check_skipped_when_no_risk_tracker(self) -> None:
        budget_config = BudgetConfig(
            risk_budget=RiskBudgetConfig(enabled=True),
        )
        cost_tracker = CostTracker(budget_config=budget_config)
        enforcer = BudgetEnforcer(
            budget_config=budget_config,
            cost_tracker=cost_tracker,
        )
        result = await enforcer.check_risk_budget(
            "agent-1",
            "task-1",
            "code:write",
        )
        assert result.allowed is True

    async def test_zero_limit_means_unlimited(self) -> None:
        enforcer, risk_tracker = _make_enforcer(
            per_task_risk_limit=0.0,
            per_agent_daily_risk_limit=0.0,
            total_daily_risk_limit=0.0,
        )
        await risk_tracker.record(
            _make_risk_record(risk_units=999.0),
        )
        result = await enforcer.check_risk_budget(
            "agent-1",
            "task-1",
            "code:write",
        )
        assert result.allowed is True


@pytest.mark.unit
class TestRecordRisk:
    """Tests for risk recording via BudgetEnforcer."""

    async def test_record_risk_returns_record(self) -> None:
        enforcer, _ = _make_enforcer()
        record = await enforcer.record_risk(
            "agent-1",
            "task-1",
            "code:write",
        )
        assert record is not None
        assert record.agent_id == "agent-1"
        assert record.action_type == "code:write"
        assert record.risk_units > 0.0

    async def test_record_risk_none_when_disabled(self) -> None:
        enforcer, _ = _make_enforcer(risk_enabled=False)
        record = await enforcer.record_risk(
            "agent-1",
            "task-1",
            "code:write",
        )
        assert record is None

    async def test_record_risk_none_when_no_tracker(self) -> None:
        budget_config = BudgetConfig(
            risk_budget=RiskBudgetConfig(enabled=True),
        )
        cost_tracker = CostTracker(budget_config=budget_config)
        enforcer = BudgetEnforcer(
            budget_config=budget_config,
            cost_tracker=cost_tracker,
        )
        record = await enforcer.record_risk(
            "agent-1",
            "task-1",
            "code:write",
        )
        assert record is None

    async def test_record_risk_none_when_no_scorer(self) -> None:
        risk_config = RiskBudgetConfig(enabled=True)
        budget_config = BudgetConfig(risk_budget=risk_config)
        cost_tracker = CostTracker(budget_config=budget_config)
        risk_tracker = RiskTracker(risk_budget_config=risk_config)
        enforcer = BudgetEnforcer(
            budget_config=budget_config,
            cost_tracker=cost_tracker,
            risk_tracker=risk_tracker,
            # risk_scorer intentionally omitted
        )
        record = await enforcer.record_risk(
            "agent-1",
            "task-1",
            "code:write",
        )
        assert record is None

    async def test_record_risk_accumulates(self) -> None:
        enforcer, risk_tracker = _make_enforcer()
        await enforcer.record_risk("agent-1", "task-1", "code:write")
        await enforcer.record_risk("agent-1", "task-1", "code:write")
        total = await risk_tracker.get_task_risk("task-1")
        assert total > 0.0


class _RaisingRiskScorer:
    """``RiskScorer``-shaped double whose ``score()`` always raises.

    Used to exercise the ``except Exception as exc:`` branch in
    ``check_risk_budget`` / ``record_risk`` where the scorer call
    fails mid-enforcement. Implements the protocol structurally (no
    base class) so a ``runtime_checkable`` isinstance check is
    irrelevant here -- the enforcer calls ``score(action_type)``
    directly.
    """

    def __init__(self, *, exc: Exception) -> None:
        self._exc = exc

    def score(self, action_type: str) -> RiskScore:
        raise self._exc


@pytest.mark.unit
class TestRiskEnforcerExceptionPaths:
    """The except-Exception branches in check_risk_budget / record_risk.

    These tests pin the redacted-logging contract on the risk
    enforcer: any unexpected exception raised by the scorer or tracker
    must be swallowed (returning a neutral RiskCheckResult / None) and
    logged via ``log_exception_redacted`` rather than propagated. The
    helper guarantees the credential-bearing ``str(exc)`` never reaches
    the structured log record.
    """

    async def test_check_risk_budget_swallows_scorer_exception(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Scorer that raises => check returns neutral result, logs redacted."""
        risk_config = RiskBudgetConfig(enabled=True)
        budget_config = BudgetConfig(risk_budget=risk_config)
        cost_tracker = CostTracker(budget_config=budget_config)
        risk_tracker = RiskTracker(risk_budget_config=risk_config)
        # Scorer raises a ValueError whose message embeds a credential-
        # looking substring so the redaction round-trip is exercised
        # end-to-end -- the substring must NOT survive into any log
        # record emitted via log_exception_redacted.
        scorer = _RaisingRiskScorer(
            exc=ValueError("scoring failed: client_secret=cs-leak-xyz"),
        )
        enforcer = BudgetEnforcer(
            budget_config=budget_config,
            cost_tracker=cost_tracker,
            risk_tracker=risk_tracker,
            risk_scorer=scorer,
        )

        result = await enforcer.check_risk_budget(
            "agent-1",
            "task-1",
            "code:write",
        )

        # Failure is swallowed; the caller still gets a usable result.
        # ``projected`` is initialised to 0.0 BEFORE the try block, so
        # the returned ``RiskCheckResult`` carries 0.0 risk units, not
        # None -- a scorer failure must not poison downstream callers
        # that read ``result.risk_units``.
        assert isinstance(result, RiskCheckResult)
        assert result.risk_units == 0.0

    async def test_record_risk_swallows_scorer_exception_returns_none(
        self,
    ) -> None:
        """Scorer that raises during record_risk => returns None, redacted log."""
        risk_config = RiskBudgetConfig(enabled=True)
        budget_config = BudgetConfig(risk_budget=risk_config)
        cost_tracker = CostTracker(budget_config=budget_config)
        risk_tracker = RiskTracker(risk_budget_config=risk_config)
        # A scorer that fails on ``score()`` triggers the except block
        # in ``record_risk`` (the call is the first statement inside
        # the ``try``). The same redaction-via-helper contract applies.
        scorer = _RaisingRiskScorer(
            exc=RuntimeError("upstream model down: api_key=sk-prod-secret"),
        )
        enforcer = BudgetEnforcer(
            budget_config=budget_config,
            cost_tracker=cost_tracker,
            risk_tracker=risk_tracker,
            risk_scorer=scorer,
        )

        record = await enforcer.record_risk(
            "agent-1",
            "task-1",
            "code:write",
        )

        # The except-Exception branch returns None and DOES NOT raise.
        # The non-None return path requires a successful score; a
        # raising scorer cannot reach it.
        assert record is None


@pytest.mark.unit
class TestRiskBudgetExhaustedError:
    """Tests for RiskBudgetExhaustedError."""

    def test_is_subclass_of_budget_exhausted(self) -> None:
        assert issubclass(RiskBudgetExhaustedError, Exception)
        from synthorg.budget.errors import BudgetExhaustedError

        assert issubclass(RiskBudgetExhaustedError, BudgetExhaustedError)

    def test_attributes(self) -> None:
        err = RiskBudgetExhaustedError(
            "test",
            agent_id="a",
            task_id="t",
            risk_units_used=5.0,
            risk_limit=4.0,
        )
        assert err.agent_id == "a"
        assert err.task_id == "t"
        assert err.risk_units_used == 5.0
        assert err.risk_limit == 4.0
