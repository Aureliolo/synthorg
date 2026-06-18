"""Unit test for the knowledge-substrate enable gate at boot."""

import pytest

from synthorg.api.lifecycle_helpers.feature_wiring import _wire_knowledge_engine
from synthorg.config.schema import RootConfig
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


async def test_disabled_knowledge_skips_wiring() -> None:
    """With knowledge.enabled=False the substrate is never wired."""
    app_state = make_app_state(
        config=RootConfig(
            company_name="test",
            knowledge=KnowledgeConfig(enabled=False),
        ),
        slices={
            PersistenceStateSlice: {"backend": object()},
            KnowledgeStateSlice: {"service": None, "tool_factory": None},
        },
    )

    await _wire_knowledge_engine(app_state)

    assert app_state.slice(KnowledgeStateSlice).service is None
