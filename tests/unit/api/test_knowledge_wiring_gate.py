"""Unit test for the knowledge-substrate enable gate at boot.

The knowledge substrate is on by default and gated on the
``knowledge.enabled`` setting (read live via the config resolver), so the
gate is driven by the setting rather than a ``RootConfig`` field.
"""

import pytest

from synthorg.api.lifecycle_helpers.feature_wiring import _wire_knowledge_engine
from synthorg.config.schema import RootConfig
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared import make_app_state
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit


async def test_disabled_knowledge_skips_wiring() -> None:
    """With knowledge.enabled=false the substrate is never wired."""
    backend = FakePersistenceBackend()
    await backend.connect()
    try:
        config = RootConfig(company_name="test")
        settings = SettingsService(repository=backend.settings, registry=get_registry())
        await settings.set("knowledge", "enabled", "false")
        resolver = ConfigResolver(settings_service=settings, config=config)
        app_state = make_app_state(
            config=config,
            settings_service=settings,
            config_resolver=resolver,
            slices={
                PersistenceStateSlice: {"backend": object()},
                KnowledgeStateSlice: {"service": None, "tool_factory": None},
            },
        )

        await _wire_knowledge_engine(app_state)

        assert app_state.slice(KnowledgeStateSlice).service is None
    finally:
        await backend.disconnect()
