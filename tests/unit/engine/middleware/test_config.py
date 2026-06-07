"""Tests for middleware configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.core.middleware_config import (
    DEFAULT_AGENT_CHAIN,
    DEFAULT_COORDINATION_CHAIN,
    AgentMiddlewareConfig,
    AuthorityDeferenceConfig,
    ClarificationGateConfig,
    CoordinationMiddlewareConfig,
    MiddlewareConfig,
)
from tests._shared import as_uuid


@pytest.mark.unit
class TestAuthorityDeferenceConfig:
    """AuthorityDeferenceConfig defaults and validation."""

    def test_defaults(self) -> None:
        cfg = AuthorityDeferenceConfig()
        assert cfg.enabled is True
        assert len(cfg.patterns) > 0
        assert len(cfg.justification_header) > 0

    def test_frozen(self) -> None:
        cfg = AuthorityDeferenceConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = False  # type: ignore[misc]


@pytest.mark.unit
class TestClarificationGateConfig:
    """ClarificationGateConfig defaults and validation."""

    def test_defaults(self) -> None:
        cfg = ClarificationGateConfig()
        assert cfg.enabled is True
        assert cfg.min_criterion_length == 10
        assert "done" in cfg.generic_patterns

    def test_rejects_zero_min_length(self) -> None:
        with pytest.raises(ValidationError):
            ClarificationGateConfig(min_criterion_length=0)

    def test_frozen(self) -> None:
        cfg = ClarificationGateConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = False  # type: ignore[misc]


@pytest.mark.unit
class TestAgentMiddlewareConfig:
    """AgentMiddlewareConfig defaults."""

    def test_default_chain(self) -> None:
        cfg = AgentMiddlewareConfig()
        assert cfg.chain == DEFAULT_AGENT_CHAIN
        assert "cost_recording" in cfg.chain
        assert "security_interceptor" in cfg.chain

    def test_custom_chain(self) -> None:
        cfg = AgentMiddlewareConfig(chain=("cost_recording",))
        assert cfg.chain == ("cost_recording",)

    def test_rejects_blank_chain_entry(self) -> None:
        with pytest.raises(ValidationError):
            AgentMiddlewareConfig(chain=("  ",))

    def test_sub_configs_default(self) -> None:
        cfg = AgentMiddlewareConfig()
        assert cfg.authority_deference.enabled is True


@pytest.mark.unit
class TestCoordinationMiddlewareConfig:
    """CoordinationMiddlewareConfig defaults."""

    def test_default_chain(self) -> None:
        cfg = CoordinationMiddlewareConfig()
        assert cfg.chain == DEFAULT_COORDINATION_CHAIN
        assert "clarification_gate" in cfg.chain

    def test_sub_configs_default(self) -> None:
        cfg = CoordinationMiddlewareConfig()
        assert cfg.clarification_gate.enabled is True

    def test_custom_chain(self) -> None:
        cfg = CoordinationMiddlewareConfig(
            chain=("task_ledger",),
        )
        assert cfg.chain == ("task_ledger",)


@pytest.mark.unit
class TestMiddlewareConfig:
    """Top-level MiddlewareConfig."""

    def test_defaults(self) -> None:
        cfg = MiddlewareConfig()
        assert isinstance(cfg.agent, AgentMiddlewareConfig)
        assert isinstance(cfg.coordination, CoordinationMiddlewareConfig)

    def test_frozen(self) -> None:
        cfg = MiddlewareConfig()
        with pytest.raises(ValidationError):
            cfg.agent = AgentMiddlewareConfig()  # type: ignore[misc]


@pytest.mark.unit
class TestCompanyConfigMiddleware:
    """CompanyConfig includes middleware field."""

    def test_company_config_has_middleware(self) -> None:
        from synthorg.core.company import CompanyConfig

        cfg = CompanyConfig()
        assert isinstance(cfg.middleware, MiddlewareConfig)

    def test_company_config_custom_middleware(self) -> None:
        from synthorg.core.company import CompanyConfig

        custom = MiddlewareConfig(
            agent=AgentMiddlewareConfig(chain=("cost_recording",)),
        )
        cfg = CompanyConfig(middleware=custom)
        assert cfg.middleware.agent.chain == ("cost_recording",)


@pytest.mark.unit
class TestTaskMiddlewareOverride:
    """Task includes middleware_override field."""

    def test_default_none(self) -> None:
        from synthorg.core.enums import Priority, TaskType
        from synthorg.core.task import Task

        task = Task(
            id=as_uuid("t-1"),
            title="Test",
            description="desc",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj",
            created_by="creator",
        )
        assert task.middleware_override is None

    def test_custom_override(self) -> None:
        from synthorg.core.enums import Priority, TaskType
        from synthorg.core.task import Task

        task = Task(
            id=as_uuid("t-1"),
            title="Test",
            description="desc",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj",
            created_by="creator",
            middleware_override=("cost_recording", "sanitize_message"),
        )
        assert task.middleware_override == (
            "cost_recording",
            "sanitize_message",
        )
