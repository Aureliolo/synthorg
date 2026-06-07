"""Shared fixtures for trajectory scoring tests."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    SkillSet,
)
from synthorg.core.role import Authority
from synthorg.engine.context import AgentContext
from synthorg.hr.seniority import SeniorityLevel


@pytest.fixture
def minimal_context() -> AgentContext:
    """Minimal AgentContext for trajectory tests."""
    identity = AgentIdentity(
        id=uuid4(),
        name="test-agent",
        role="Developer",
        department="Engineering",
        level=SeniorityLevel.JUNIOR,
        personality=PersonalityConfig(),
        skills=SkillSet(),
        authority=Authority(),
        model=ModelConfig(
            provider="test-provider",
            model_id="test-model",
        ),
        hiring_date=date(2026, 1, 1),
    )
    return AgentContext.from_identity(
        identity=identity,
        max_turns=10,
    )
