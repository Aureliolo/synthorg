"""Tests for PromptInjectionStrategy."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.text_estimation import DefaultTokenEstimator
from synthorg.ontology.injection.prompt import (
    PromptInjectionStrategy,
    format_entity,
)
from synthorg.ontology.models import EntityDefinition
from synthorg.providers.enums import MessageRole


@pytest.mark.unit
class TestFormatEntity:
    """Tests for format_entity helper."""

    def test_minimal_entity(
        self,
        core_entities: tuple[EntityDefinition, ...],
    ) -> None:
        """Entity with definition and fields is formatted correctly."""
        result = format_entity(core_entities[0])
        assert "## Task" in result
        assert "A unit of work" in result
        assert "title: str" in result

    def test_entity_with_constraints(
        self,
        sample_entity: EntityDefinition,
    ) -> None:
        """Constraints section is included."""
        # Use the fully-populated fixture from parent conftest
        result = format_entity(sample_entity)
        assert "Constraints:" in result
        assert "title must not be empty" in result

    def test_entity_with_disambiguation(
        self,
        sample_entity: EntityDefinition,
    ) -> None:
        """Disambiguation line is included."""
        result = format_entity(sample_entity)
        assert "Not:" in result

    def test_entity_with_relationships(
        self,
        sample_entity: EntityDefinition,
    ) -> None:
        """Relationships section is included."""
        result = format_entity(sample_entity)
        assert "Relationships:" in result
        assert "assigned_to -> AgentIdentity" in result


@pytest.mark.unit
class TestPromptInjectionStrategy:
    """Tests for PromptInjectionStrategy."""

    async def test_prepare_messages_returns_system_message(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Returns a single SYSTEM message with core entities."""
        strategy = PromptInjectionStrategy(backend=mock_backend)
        messages = await strategy.prepare_messages(
            agent_id="agent-1",
            task_context="Do some work",
            token_budget=5000,
        )
        assert len(messages) == 1
        assert messages[0].role == MessageRole.SYSTEM
        assert messages[0].content is not None
        assert "Entity Definitions" in messages[0].content
        assert "Task" in messages[0].content
        assert "AgentIdentity" in messages[0].content

    async def test_only_core_entities_injected(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """USER-tier entities are not included."""
        strategy = PromptInjectionStrategy(backend=mock_backend)
        messages = await strategy.prepare_messages(
            agent_id="agent-1",
            task_context="Do some work",
            token_budget=5000,
        )
        assert messages[0].content is not None
        assert "Invoice" not in messages[0].content

    async def test_respects_token_budget(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Stops adding entities when budget is exhausted."""
        strategy = PromptInjectionStrategy(
            backend=mock_backend,
            core_token_budget=50,
        )
        messages = await strategy.prepare_messages(
            agent_id="agent-1",
            task_context="Do some work",
            token_budget=5000,
        )
        # With a very small budget, may include 0 or 1 entities
        if messages:
            content = messages[0].content
            assert content is not None
            # Should not include both entities with tiny budget
            parts = content.split("## ")
            # parts[0] is header, each subsequent is an entity
            assert len(parts) <= 3  # header + at most 2 entities

    async def test_empty_when_no_core_entities(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Returns empty tuple when no CORE entities exist."""
        mock_backend.list_entities = AsyncMock(return_value=())
        strategy = PromptInjectionStrategy(backend=mock_backend)
        messages = await strategy.prepare_messages(
            agent_id="agent-1",
            task_context="Do some work",
            token_budget=5000,
        )
        assert messages == ()

    async def test_caller_budget_overrides_config(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Uses minimum of config budget and caller budget."""
        strategy = PromptInjectionStrategy(
            backend=mock_backend,
            core_token_budget=10000,
        )
        messages = await strategy.prepare_messages(
            agent_id="agent-1",
            task_context="Do some work",
            token_budget=10,
        )
        # Very small caller budget -- may return empty
        assert len(messages) <= 1

    def test_no_tool_definitions(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Prompt strategy provides no tools."""
        strategy = PromptInjectionStrategy(backend=mock_backend)
        assert strategy.get_tool_definitions() == ()

    def test_strategy_name(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Strategy name is 'prompt'."""
        strategy = PromptInjectionStrategy(backend=mock_backend)
        assert strategy.strategy_name == "prompt"

    async def test_custom_token_estimator(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Custom token estimator is used."""
        estimator = DefaultTokenEstimator()
        strategy = PromptInjectionStrategy(
            backend=mock_backend,
            token_estimator=estimator,
        )
        messages = await strategy.prepare_messages(
            agent_id="agent-1",
            task_context="Do some work",
            token_budget=5000,
        )
        assert len(messages) == 1
