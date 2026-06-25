"""Unit tests for the knowledge-substrate boot gate + synthesis degradation."""

from types import SimpleNamespace

import pytest

from synthorg.api.lifecycle_helpers.knowledge_wiring import (
    _maybe_build_knowledge_synthesizer,
    wire_knowledge_engine,
)
from synthorg.config.schema import RootConfig
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _settings(values: dict[str, str]) -> SettingsService:
    async def _get(namespace: str, key: str) -> SimpleNamespace:
        assert namespace == "knowledge"
        return SimpleNamespace(value=values.get(key, ""))

    service: SettingsService = mock_of[SettingsService](get=_get)
    return service


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

    await wire_knowledge_engine(app_state, provider_registry=None)

    assert app_state.slice(KnowledgeStateSlice).service is None


async def test_synthesizer_none_without_provider_registry() -> None:
    """No provider registry degrades the substrate to retrieval-only."""
    app_state = make_app_state(
        slices={SettingsStateSlice: {"settings_service": _settings({})}},
    )

    result = await _maybe_build_knowledge_synthesizer(app_state, provider_registry=None)

    assert result is None


async def test_synthesizer_none_when_disabled() -> None:
    """synthesis_enabled=false degrades to retrieval-only."""
    app_state = make_app_state(
        slices={
            SettingsStateSlice: {
                "settings_service": _settings(
                    {"synthesis_enabled": "false", "synthesis_model": "m"}
                )
            }
        },
    )

    result = await _maybe_build_knowledge_synthesizer(
        app_state, provider_registry=mock_of[ProviderRegistry]()
    )

    assert result is None


async def test_synthesizer_none_when_model_unset() -> None:
    """synthesis enabled but no model degrades to retrieval-only."""
    app_state = make_app_state(
        slices={
            SettingsStateSlice: {
                "settings_service": _settings(
                    {"synthesis_enabled": "true", "synthesis_model": ""}
                )
            }
        },
    )

    result = await _maybe_build_knowledge_synthesizer(
        app_state, provider_registry=mock_of[ProviderRegistry]()
    )

    assert result is None
