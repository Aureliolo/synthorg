"""Integration tests for hierarchical delegation with loop prevention."""

from datetime import date

import pytest

from synthorg.communication.config import (
    CircuitBreakerConfig,
    HierarchyConfig,
    LoopPreventionConfig,
    RateLimitConfig,
)
from synthorg.communication.delegation.authority import (
    AuthorityValidator,
)
from synthorg.communication.delegation.hierarchy import (
    HierarchyResolver,
)
from synthorg.communication.delegation.models import (
    DelegationRequest,
)
from synthorg.communication.delegation.service import (
    DelegationService,
)
from synthorg.communication.loop_prevention.guard import (
    DelegationGuard,
)
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.company import (
    Company,
    CompanyConfig,
    Department,
    Team,
)
from synthorg.core.enums import SeniorityLevel, TaskStatus, TaskType
from synthorg.core.role import Authority
from synthorg.core.task import Task

pytestmark = pytest.mark.timeout(30)


def _model_config() -> ModelConfig:
    return ModelConfig(
        provider="test-provider",
        model_id="test-small-001",
    )


def _make_agent(
    name: str,
    role: str,
    *,
    level: SeniorityLevel = SeniorityLevel.MID,
    can_delegate_to: tuple[str, ...] = (),
) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role=role,
        department="Engineering",
        level=level,
        model=_model_config(),
        hiring_date=date(2026, 1, 1),
        authority=Authority(can_delegate_to=can_delegate_to),
    )


def _make_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": "task-root",
        "title": "Build authentication system",
        "description": "Implement full auth flow",
        "type": TaskType.DEVELOPMENT,
        "project": "proj-auth",
        "created_by": "ceo",
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


def _build_three_level_service() -> tuple[
    DelegationService,
    HierarchyResolver,
    dict[str, AgentIdentity],
]:
    """Build a 3-level hierarchy: CEO → CTO → Dev.

    Returns:
        service, hierarchy, agents dict.
    """
    company = Company(
        name="Test Corp",
        departments=(
            Department(
                name="Engineering",
                head="ceo",
                budget_percent=100.0,
                teams=(
                    Team(
                        name="platform",
                        lead="cto",
                        members=("dev",),
                    ),
                ),
            ),
        ),
        config=CompanyConfig(budget_monthly=100.0),
    )
    hierarchy = HierarchyResolver(company)
    hierarchy_config = HierarchyConfig(
        enforce_chain_of_command=True,
        allow_skip_level=True,
    )
    authority_validator = AuthorityValidator(hierarchy, hierarchy_config)
    guard = DelegationGuard(
        LoopPreventionConfig(
            max_delegation_depth=5,
            rate_limit=RateLimitConfig(max_per_pair_per_minute=10, burst_allowance=3),
            circuit_breaker=CircuitBreakerConfig(
                bounce_threshold=3, cooldown_seconds=300
            ),
        ),
    )
    service = DelegationService(
        hierarchy=hierarchy,
        authority_validator=authority_validator,
        guard=guard,
    )
    agents = {
        "ceo": _make_agent("ceo", "CEO", level=SeniorityLevel.VP),
        "cto": _make_agent("cto", "CTO", level=SeniorityLevel.DIRECTOR),
        "dev": _make_agent("dev", "Developer", level=SeniorityLevel.MID),
    }
    return service, hierarchy, agents


@pytest.mark.integration
class TestFullDelegationFlow:
    """End-to-end delegation through a 3-level hierarchy."""

    def test_ceo_to_cto_to_dev(self) -> None:
        """CEO delegates to CTO, CTO delegates to Dev."""
        service, _, agents = _build_three_level_service()
        task = _make_task()

        # CEO → CTO
        req1 = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
            refinement="Focus on backend API",
        )
        r1 = service.delegate(req1, agents["ceo"], agents["cto"])
        assert r1.success is True
        sub1 = r1.delegated_task
        assert sub1 is not None
        assert sub1.parent_task_id == "task-root"
        assert sub1.delegation_chain == ("ceo",)
        assert sub1.status is TaskStatus.CREATED
        assert "Focus on backend API" in sub1.description

        # CTO → Dev (different task ID avoids dedup naturally)
        sub1_retitled = Task(
            id=sub1.id,
            title="Build auth API endpoints",
            description=sub1.description,
            type=sub1.type,
            project=sub1.project,
            created_by=sub1.created_by,
            parent_task_id=sub1.parent_task_id,
            delegation_chain=sub1.delegation_chain,
        )
        req2 = DelegationRequest(
            delegator_id="cto",
            delegatee_id="dev",
            task=sub1_retitled,
        )
        r2 = service.delegate(req2, agents["cto"], agents["dev"])
        assert r2.success is True
        sub2 = r2.delegated_task
        assert sub2 is not None
        assert sub2.delegation_chain == ("ceo", "cto")
        assert sub2.parent_task_id == sub1.id

        # Verify audit trail
        trail = service.get_audit_trail()
        assert len(trail) == 2

    def test_ancestry_prevents_back_delegation(self) -> None:
        """Dev cannot delegate back to CEO (ancestry block)."""
        service, _, agents = _build_three_level_service()
        task = _make_task()

        # CEO → CTO
        req1 = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        r1 = service.delegate(req1, agents["ceo"], agents["cto"])
        sub1 = r1.delegated_task
        assert sub1 is not None

        # CTO → Dev
        sub1_new = Task(
            id=sub1.id,
            title="Auth: implement login",
            description=sub1.description,
            type=sub1.type,
            project=sub1.project,
            created_by=sub1.created_by,
            parent_task_id=sub1.parent_task_id,
            delegation_chain=sub1.delegation_chain,
        )
        req2 = DelegationRequest(
            delegator_id="cto",
            delegatee_id="dev",
            task=sub1_new,
        )
        r2 = service.delegate(req2, agents["cto"], agents["dev"])
        sub2 = r2.delegated_task
        assert sub2 is not None
        # chain is now ("ceo", "cto")

        # Dev → CEO: should be blocked by ancestry
        # (ceo is in delegation_chain)
        req3 = DelegationRequest(
            delegator_id="dev",
            delegatee_id="ceo",
            task=sub2,
        )
        r3 = service.delegate(req3, agents["dev"], agents["ceo"])
        assert r3.success is False
        # Blocked by either ancestry or authority
        assert r3.blocked_by is not None

    def test_dedup_prevents_repeated_delegation(self) -> None:
        """Same delegation request is rejected on second attempt."""
        service, _, agents = _build_three_level_service()
        task = _make_task()
        req = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        r1 = service.delegate(req, agents["ceo"], agents["cto"])
        assert r1.success is True

        r2 = service.delegate(req, agents["ceo"], agents["cto"])
        assert r2.success is False
        assert r2.blocked_by == "dedup"


@pytest.mark.integration
class TestCircuitBreakerIntegration:
    """Circuit breaker triggers after repeated bounces."""

    def test_circuit_opens_after_threshold(self) -> None:
        company = Company(
            name="Test Corp",
            departments=(
                Department(
                    name="Engineering",
                    head="ceo",
                    budget_percent=100.0,
                    teams=(
                        Team(
                            name="platform",
                            lead="cto",
                            members=("dev",),
                        ),
                    ),
                ),
            ),
            config=CompanyConfig(budget_monthly=100.0),
        )
        hierarchy = HierarchyResolver(company)
        hierarchy_config = HierarchyConfig(
            enforce_chain_of_command=True,
            allow_skip_level=False,
        )
        authority_validator = AuthorityValidator(hierarchy, hierarchy_config)
        guard = DelegationGuard(
            LoopPreventionConfig(
                max_delegation_depth=10,
                rate_limit=RateLimitConfig(
                    max_per_pair_per_minute=100,
                    burst_allowance=100,
                ),
                circuit_breaker=CircuitBreakerConfig(
                    bounce_threshold=3, cooldown_seconds=300
                ),
            ),
        )
        service = DelegationService(
            hierarchy=hierarchy,
            authority_validator=authority_validator,
            guard=guard,
        )
        ceo = _make_agent("ceo", "CEO", level=SeniorityLevel.VP)
        cto = _make_agent("cto", "CTO", level=SeniorityLevel.DIRECTOR)

        # Perform 3 delegations (= bounce_threshold)
        for i in range(3):
            task = _make_task(
                id=f"task-{i}",
                title=f"Task variant {i}",
            )
            req = DelegationRequest(
                delegator_id="ceo",
                delegatee_id="cto",
                task=task,
            )
            result = service.delegate(req, ceo, cto)
            assert result.success is True

        # 4th delegation: circuit breaker should block
        task4 = _make_task(id="task-4", title="Task after circuit")
        req4 = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task4,
        )
        r4 = service.delegate(req4, ceo, cto)
        assert r4.success is False
        assert r4.blocked_by == "circuit_breaker"


@pytest.mark.integration
class TestDelegationChainValidation:
    """Validate delegation chain grows correctly across hops."""

    def test_chain_carries_full_ancestry(self) -> None:
        service, _, agents = _build_three_level_service()

        # Start with root task
        root = _make_task()
        assert root.delegation_chain == ()

        # CEO → CTO
        req1 = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=root,
        )
        r1 = service.delegate(req1, agents["ceo"], agents["cto"])
        sub1 = r1.delegated_task
        assert sub1 is not None
        assert sub1.delegation_chain == ("ceo",)

        # CTO → Dev
        sub1_retitled = Task(
            id=sub1.id,
            title="Auth subtask for dev",
            description=sub1.description,
            type=sub1.type,
            project=sub1.project,
            created_by=sub1.created_by,
            parent_task_id=sub1.parent_task_id,
            delegation_chain=sub1.delegation_chain,
        )
        req2 = DelegationRequest(
            delegator_id="cto",
            delegatee_id="dev",
            task=sub1_retitled,
        )
        r2 = service.delegate(req2, agents["cto"], agents["dev"])
        sub2 = r2.delegated_task
        assert sub2 is not None
        assert sub2.delegation_chain == ("ceo", "cto")

        # Verify parent chain
        assert sub2.parent_task_id == sub1.id
        assert sub1.parent_task_id == "task-root"
