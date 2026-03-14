"""Tests for AuthorityValidator."""

from datetime import date

import pytest

from synthorg.communication.config import HierarchyConfig
from synthorg.communication.delegation.authority import (
    AuthorityValidator,
)
from synthorg.communication.delegation.hierarchy import (
    HierarchyResolver,
)
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.company import (
    Company,
    CompanyConfig,
    Department,
    Team,
)
from synthorg.core.enums import SeniorityLevel
from synthorg.core.role import Authority

pytestmark = pytest.mark.timeout(30)


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
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=date(2026, 1, 1),
        authority=Authority(can_delegate_to=can_delegate_to),
    )


def _build_resolver() -> HierarchyResolver:
    """Build hierarchy: cto -> backend_lead -> dev."""
    company = Company(
        name="Test Corp",
        departments=(
            Department(
                name="Engineering",
                head="cto",
                budget_percent=60.0,
                teams=(
                    Team(
                        name="backend",
                        lead="backend_lead",
                        members=("dev", "jr_dev"),
                    ),
                ),
            ),
        ),
        config=CompanyConfig(budget_monthly=100.0),
    )
    return HierarchyResolver(company)


@pytest.mark.unit
class TestAuthorityValidatorHierarchy:
    def test_direct_report_allowed(self) -> None:
        resolver = _build_resolver()
        config = HierarchyConfig(
            enforce_chain_of_command=True,
            allow_skip_level=False,
        )
        validator = AuthorityValidator(resolver, config)
        delegator = _make_agent("backend_lead", "lead")
        delegatee = _make_agent("dev")
        result = validator.validate(delegator, delegatee)
        assert result.allowed is True

    def test_non_report_denied(self) -> None:
        resolver = _build_resolver()
        config = HierarchyConfig(
            enforce_chain_of_command=True,
            allow_skip_level=False,
        )
        validator = AuthorityValidator(resolver, config)
        delegator = _make_agent("dev")
        delegatee = _make_agent("cto")
        result = validator.validate(delegator, delegatee)
        assert result.allowed is False
        assert "direct report" in result.reason

    def test_skip_level_denied_without_config(self) -> None:
        resolver = _build_resolver()
        config = HierarchyConfig(
            enforce_chain_of_command=True,
            allow_skip_level=False,
        )
        validator = AuthorityValidator(resolver, config)
        delegator = _make_agent("cto")
        delegatee = _make_agent("dev")
        result = validator.validate(delegator, delegatee)
        assert result.allowed is False

    def test_skip_level_allowed_with_config(self) -> None:
        resolver = _build_resolver()
        config = HierarchyConfig(
            enforce_chain_of_command=True,
            allow_skip_level=True,
        )
        validator = AuthorityValidator(resolver, config)
        delegator = _make_agent("cto")
        delegatee = _make_agent("dev")
        result = validator.validate(delegator, delegatee)
        assert result.allowed is True

    def test_chain_of_command_disabled(self) -> None:
        """When chain of command is not enforced, any pair is allowed."""
        resolver = _build_resolver()
        config = HierarchyConfig(
            enforce_chain_of_command=False,
            allow_skip_level=False,
        )
        validator = AuthorityValidator(resolver, config)
        delegator = _make_agent("dev")
        delegatee = _make_agent("cto")
        result = validator.validate(delegator, delegatee)
        assert result.allowed is True


@pytest.mark.unit
class TestAuthorityValidatorRoles:
    def test_role_in_can_delegate_to_allowed(self) -> None:
        resolver = _build_resolver()
        config = HierarchyConfig(
            enforce_chain_of_command=True,
            allow_skip_level=False,
        )
        validator = AuthorityValidator(resolver, config)
        delegator = _make_agent(
            "backend_lead",
            "lead",
            can_delegate_to=("developer",),
        )
        delegatee = _make_agent("dev", "developer")
        result = validator.validate(delegator, delegatee)
        assert result.allowed is True

    def test_role_not_in_can_delegate_to_denied(self) -> None:
        resolver = _build_resolver()
        config = HierarchyConfig(
            enforce_chain_of_command=True,
            allow_skip_level=False,
        )
        validator = AuthorityValidator(resolver, config)
        delegator = _make_agent(
            "backend_lead",
            "lead",
            can_delegate_to=("qa",),
        )
        delegatee = _make_agent("dev", "developer")
        result = validator.validate(delegator, delegatee)
        assert result.allowed is False
        assert "can_delegate_to" in result.reason

    def test_empty_can_delegate_to_allows_all_roles(self) -> None:
        resolver = _build_resolver()
        config = HierarchyConfig(
            enforce_chain_of_command=True,
            allow_skip_level=False,
        )
        validator = AuthorityValidator(resolver, config)
        delegator = _make_agent("backend_lead", "lead")
        delegatee = _make_agent("dev", "any_role")
        result = validator.validate(delegator, delegatee)
        assert result.allowed is True
