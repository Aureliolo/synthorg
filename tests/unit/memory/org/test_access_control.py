"""Tests for org memory access control."""

import pytest

from synthorg.core.enums import OrgFactCategory, SeniorityLevel
from synthorg.memory.org.access_control import (
    CategoryWriteRule,
    WriteAccessConfig,
    check_write_access,
    require_write_access,
)
from synthorg.memory.org.errors import OrgMemoryAccessDeniedError
from synthorg.memory.org.models import OrgFactAuthor

pytestmark = pytest.mark.timeout(30)

_HUMAN = OrgFactAuthor(is_human=True)
_SENIOR_AGENT = OrgFactAuthor(
    agent_id="agent-senior",
    seniority=SeniorityLevel.SENIOR,
    is_human=False,
)
_JUNIOR_AGENT = OrgFactAuthor(
    agent_id="agent-junior",
    seniority=SeniorityLevel.JUNIOR,
    is_human=False,
)
_LEAD_AGENT = OrgFactAuthor(
    agent_id="agent-lead",
    seniority=SeniorityLevel.LEAD,
    is_human=False,
)


@pytest.mark.unit
class TestCheckWriteAccess:
    """check_write_access for all category/author combinations."""

    def test_human_can_write_core_policy(self) -> None:
        config = WriteAccessConfig()
        assert check_write_access(config, OrgFactCategory.CORE_POLICY, _HUMAN) is True

    def test_agent_cannot_write_core_policy(self) -> None:
        config = WriteAccessConfig()
        assert (
            check_write_access(config, OrgFactCategory.CORE_POLICY, _SENIOR_AGENT)
            is False
        )

    def test_senior_can_write_adr(self) -> None:
        config = WriteAccessConfig()
        assert check_write_access(config, OrgFactCategory.ADR, _SENIOR_AGENT) is True

    def test_junior_cannot_write_adr(self) -> None:
        config = WriteAccessConfig()
        assert check_write_access(config, OrgFactCategory.ADR, _JUNIOR_AGENT) is False

    def test_lead_can_write_procedure(self) -> None:
        config = WriteAccessConfig()
        assert (
            check_write_access(config, OrgFactCategory.PROCEDURE, _LEAD_AGENT) is True
        )

    def test_human_can_write_convention(self) -> None:
        config = WriteAccessConfig()
        assert check_write_access(config, OrgFactCategory.CONVENTION, _HUMAN) is True

    def test_custom_rule_human_denied(self) -> None:
        config = WriteAccessConfig(
            rules={
                OrgFactCategory.CONVENTION: CategoryWriteRule(
                    allowed_seniority=SeniorityLevel.SENIOR,
                    human_allowed=False,
                ),
            },
        )
        assert check_write_access(config, OrgFactCategory.CONVENTION, _HUMAN) is False

    def test_unknown_category_defaults_to_deny_all(self) -> None:
        """Fail-closed: missing category rule denies everyone."""
        config = WriteAccessConfig(rules={})
        assert check_write_access(config, OrgFactCategory.CORE_POLICY, _HUMAN) is False
        assert (
            check_write_access(config, OrgFactCategory.CORE_POLICY, _SENIOR_AGENT)
            is False
        )


@pytest.mark.unit
class TestRequireWriteAccess:
    """require_write_access raises on denial."""

    def test_allowed_does_not_raise(self) -> None:
        config = WriteAccessConfig()
        require_write_access(config, OrgFactCategory.ADR, _SENIOR_AGENT)

    def test_denied_raises(self) -> None:
        config = WriteAccessConfig()
        with pytest.raises(OrgMemoryAccessDeniedError, match="Write access denied"):
            require_write_access(
                config,
                OrgFactCategory.CORE_POLICY,
                _JUNIOR_AGENT,
            )


@pytest.mark.unit
class TestWriteAccessConfigImmutability:
    """WriteAccessConfig.rules is a MappingProxyType."""

    def test_rules_immutable(self) -> None:
        config = WriteAccessConfig()
        with pytest.raises(TypeError):
            config.rules[OrgFactCategory.CORE_POLICY] = CategoryWriteRule()
