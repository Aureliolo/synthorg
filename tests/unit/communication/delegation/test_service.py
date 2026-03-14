"""Tests for DelegationService."""

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
    role: str = "developer",
    *,
    can_delegate_to: tuple[str, ...] = (),
) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role=role,
        department="Engineering",
        level=SeniorityLevel.MID,
        model=_model_config(),
        hiring_date=date(2026, 1, 1),
        authority=Authority(can_delegate_to=can_delegate_to),
    )


def _make_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": "task-1",
        "title": "Build feature X",
        "description": "Implement the feature",
        "type": TaskType.DEVELOPMENT,
        "project": "proj-1",
        "created_by": "ceo",
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


def _build_service(
    *,
    enforce_chain: bool = True,
    allow_skip: bool = False,
    max_depth: int = 5,
) -> tuple[
    DelegationService,
    HierarchyResolver,
]:
    """Build a DelegationService with a 3-level hierarchy."""
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
        enforce_chain_of_command=enforce_chain,
        allow_skip_level=allow_skip,
    )
    authority_validator = AuthorityValidator(hierarchy, hierarchy_config)
    guard = DelegationGuard(
        LoopPreventionConfig(
            max_delegation_depth=max_depth,
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
    return service, hierarchy


@pytest.mark.unit
class TestDelegationServiceSuccess:
    def test_successful_delegation(self) -> None:
        service, _ = _build_service()
        task = _make_task()
        delegator = _make_agent("ceo", "ceo")
        delegatee = _make_agent("cto", "cto")
        request = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        result = service.delegate(request, delegator, delegatee)
        assert result.success is True
        assert result.delegated_task is not None
        assert result.delegated_task.parent_task_id == "task-1"
        assert result.delegated_task.delegation_chain == ("ceo",)
        assert result.delegated_task.status is TaskStatus.CREATED

    def test_sub_task_inherits_properties(self) -> None:
        service, _ = _build_service()
        task = _make_task(budget_limit=50.0, deadline="2026-12-31")
        delegator = _make_agent("ceo", "ceo")
        delegatee = _make_agent("cto", "cto")
        request = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        result = service.delegate(request, delegator, delegatee)
        sub = result.delegated_task
        assert sub is not None
        assert sub.budget_limit == 50.0
        assert sub.deadline == "2026-12-31"
        assert sub.type is TaskType.DEVELOPMENT

    def test_refinement_appended_to_description(self) -> None:
        service, _ = _build_service()
        task = _make_task()
        delegator = _make_agent("ceo", "ceo")
        delegatee = _make_agent("cto", "cto")
        request = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
            refinement="Focus on API layer",
        )
        result = service.delegate(request, delegator, delegatee)
        sub = result.delegated_task
        assert sub is not None
        assert "Focus on API layer" in sub.description

    def test_audit_trail_recorded(self) -> None:
        service, _ = _build_service()
        task = _make_task()
        delegator = _make_agent("ceo", "ceo")
        delegatee = _make_agent("cto", "cto")
        request = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        service.delegate(request, delegator, delegatee)
        trail = service.get_audit_trail()
        assert len(trail) == 1
        assert trail[0].delegator_id == "ceo"
        assert trail[0].delegatee_id == "cto"
        assert trail[0].original_task_id == "task-1"


@pytest.mark.unit
class TestDelegationServiceAuthority:
    def test_authority_denied(self) -> None:
        service, _ = _build_service()
        task = _make_task()
        # dev trying to delegate to ceo (not a report)
        delegator = _make_agent("dev")
        delegatee = _make_agent("ceo", "ceo")
        request = DelegationRequest(
            delegator_id="dev",
            delegatee_id="ceo",
            task=task,
        )
        result = service.delegate(request, delegator, delegatee)
        assert result.success is False
        assert result.blocked_by == "authority"

    def test_role_permission_denied(self) -> None:
        service, _ = _build_service()
        task = _make_task()
        delegator = _make_agent("ceo", "ceo", can_delegate_to=("qa",))
        delegatee = _make_agent("cto", "cto")
        request = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        result = service.delegate(request, delegator, delegatee)
        assert result.success is False
        assert result.blocked_by == "authority"


@pytest.mark.unit
class TestDelegationServiceLoopPrevention:
    def test_ancestry_blocked(self) -> None:
        service, _ = _build_service(enforce_chain=False)
        # First delegation: ceo -> cto
        task1 = _make_task()
        ceo = _make_agent("ceo", "ceo")
        cto = _make_agent("cto", "cto")
        req1 = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task1,
        )
        r1 = service.delegate(req1, ceo, cto)
        assert r1.success is True

        # Second: cto -> dev using sub-task
        sub = r1.delegated_task
        assert sub is not None
        dev = _make_agent("dev")
        req2 = DelegationRequest(
            delegator_id="cto",
            delegatee_id="dev",
            task=sub,
        )
        r2 = service.delegate(req2, cto, dev)
        assert r2.success is True

        # Third: dev tries to delegate back to ceo → blocked by ancestry
        sub2 = r2.delegated_task
        assert sub2 is not None
        req3 = DelegationRequest(
            delegator_id="dev",
            delegatee_id="ceo",
            task=sub2,
        )
        r3 = service.delegate(req3, dev, ceo)
        assert r3.success is False
        assert r3.blocked_by == "ancestry"

    def test_depth_exceeded(self) -> None:
        service, _ = _build_service(max_depth=1)
        task = _make_task(delegation_chain=("root",))
        delegator = _make_agent("ceo", "ceo")
        delegatee = _make_agent("cto", "cto")
        request = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        result = service.delegate(request, delegator, delegatee)
        assert result.success is False
        assert result.blocked_by == "max_depth"

    def test_dedup_blocked(self) -> None:
        service, _ = _build_service()
        task = _make_task()
        delegator = _make_agent("ceo", "ceo")
        delegatee = _make_agent("cto", "cto")
        request = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        # First succeeds
        r1 = service.delegate(request, delegator, delegatee)
        assert r1.success is True
        # Second is dedup blocked (same delegator, delegatee, task ID)
        r2 = service.delegate(request, delegator, delegatee)
        assert r2.success is False
        assert r2.blocked_by == "dedup"


@pytest.mark.unit
class TestDelegationServiceMultiHop:
    def test_multi_level_delegation_chain(self) -> None:
        """CEO → CTO → Dev: delegation chain grows correctly."""
        service, _ = _build_service(allow_skip=True)
        task = _make_task()
        ceo = _make_agent("ceo", "ceo")
        cto = _make_agent("cto", "cto")
        dev = _make_agent("dev")

        # CEO → CTO
        req1 = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        r1 = service.delegate(req1, ceo, cto)
        assert r1.success is True
        sub1 = r1.delegated_task
        assert sub1 is not None
        assert sub1.delegation_chain == ("ceo",)

        # CTO → Dev (using sub-task, different task ID avoids dedup)
        sub1_new_title = Task(
            id=sub1.id,
            title="Build feature X - backend",
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
            task=sub1_new_title,
        )
        r2 = service.delegate(req2, cto, dev)
        assert r2.success is True
        sub2 = r2.delegated_task
        assert sub2 is not None
        assert sub2.delegation_chain == ("ceo", "cto")
        assert sub2.parent_task_id == sub1.id

    def test_audit_trail_multi_hop(self) -> None:
        service, _ = _build_service(allow_skip=True)
        task = _make_task()
        ceo = _make_agent("ceo", "ceo")
        cto = _make_agent("cto", "cto")
        dev = _make_agent("dev")

        req1 = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        r1 = service.delegate(req1, ceo, cto)
        sub1 = r1.delegated_task
        assert sub1 is not None

        sub1_retitled = Task(
            id=sub1.id,
            title="Feature X - backend piece",
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
        service.delegate(req2, cto, dev)

        trail = service.get_audit_trail()
        assert len(trail) == 2
        assert trail[0].delegator_id == "ceo"
        assert trail[1].delegator_id == "cto"


@pytest.mark.unit
class TestDelegationServiceIdentityValidation:
    def test_delegator_id_mismatch_raises(self) -> None:
        service, _ = _build_service()
        task = _make_task()
        delegatee = _make_agent("cto", "cto")
        request = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        wrong_delegator = _make_agent("imposter", "imposter")
        with pytest.raises(ValueError, match="delegator_id"):
            service.delegate(request, wrong_delegator, delegatee)

    def test_delegatee_id_mismatch_raises(self) -> None:
        service, _ = _build_service()
        task = _make_task()
        delegator = _make_agent("ceo", "ceo")
        request = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
        )
        wrong_delegatee = _make_agent("imposter", "imposter")
        with pytest.raises(ValueError, match="delegatee_id"):
            service.delegate(request, delegator, wrong_delegatee)


@pytest.mark.unit
class TestDelegationServiceConstraints:
    def test_constraints_appended_to_description(self) -> None:
        service, _ = _build_service()
        task = _make_task()
        delegator = _make_agent("ceo", "ceo")
        delegatee = _make_agent("cto", "cto")
        request = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="cto",
            task=task,
            constraints=("no-external-deps", "max-2-files"),
        )
        result = service.delegate(request, delegator, delegatee)
        sub = result.delegated_task
        assert sub is not None
        assert "- no-external-deps" in sub.description
        assert "- max-2-files" in sub.description


@pytest.mark.unit
class TestDelegationServiceHelpers:
    def test_get_supervisor_of(self) -> None:
        service, _ = _build_service()
        assert service.get_supervisor_of("cto") == "ceo"
        assert service.get_supervisor_of("dev") == "cto"
        assert service.get_supervisor_of("ceo") is None
