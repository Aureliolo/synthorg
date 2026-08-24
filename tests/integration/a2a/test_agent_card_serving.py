"""Integration test: Agent Card served at well-known URI.

Verifies the safe-subset projection -- the card MUST NOT contain
budget, authority, model config, or other sensitive fields from
AgentIdentity.
"""

from datetime import date

import pytest

from synthorg.a2a.agent_card import AgentCardBuilder
from synthorg.a2a.models import A2AAuthSchemeInfo
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    SkillSet,
)
from synthorg.core.role import Skill


@pytest.mark.integration
def test_agent_card_safe_subset() -> None:
    """Agent Card contains only safe-subset fields."""
    identity = AgentIdentity(
        name="senior-dev",
        role="senior developer",
        department="engineering",
        model=ModelConfig(
            provider="test-provider",
            model_id="test-expert-001",
            temperature=0.9,
            max_tokens=8192,
        ),
        skills=SkillSet(
            primary=(
                Skill(id="architecture", name="architecture"),
                Skill(id="code-review", name="code-review"),
            ),
            secondary=(Skill(id="mentoring", name="mentoring"),),
        ),
        hiring_date=date(2025, 6, 1),
    )

    builder = AgentCardBuilder(
        default_auth_schemes=(A2AAuthSchemeInfo(scheme="api_key"),),
    )
    card = builder.build(identity, "https://example.com/a2a")
    card_json = card.model_dump_json()

    # Safe fields present
    assert "senior-dev" in card_json
    assert "senior developer" in card_json
    assert "engineering" in card_json
    assert "architecture" in card_json
    assert "code-review" in card_json

    # Sensitive fields ABSENT
    assert "perfectionist" not in card_json
    assert "analytical" not in card_json
    assert "terse" not in card_json
    assert "test-provider" not in card_json
    assert "test-expert-001" not in card_json
    assert "0.9" not in card_json
    assert "8192" not in card_json
    assert "2025-06-01" not in card_json


@pytest.mark.integration
def test_peer_not_on_allowlist_rejected() -> None:
    """Peer not on the allowlist is rejected."""
    from synthorg.a2a.security import validate_peer

    assert validate_peer("unknown-peer", ("allowed-peer",)) is False
    assert validate_peer("allowed-peer", ("allowed-peer",)) is True
