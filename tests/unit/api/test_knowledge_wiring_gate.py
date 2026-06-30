"""Unit tests for the knowledge-substrate wiring + synthesis ghost-wire."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

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


async def test_boot_config_disabled_does_not_gate_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boot config knowledge.enabled=False does not skip substrate wiring.

    The substrate is ghost-wired regardless of the boot Pydantic config; the
    settings-DB ``knowledge.enabled`` is the authority, enforced live at the
    handlers.
    """
    build = AsyncMock()
    monkeypatch.setattr(
        "synthorg.api.lifecycle_helpers.knowledge_wiring._build_and_wire_knowledge",
        build,
    )
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

    build.assert_awaited_once()


async def test_synthesizer_none_without_provider_registry() -> None:
    """No provider registry degrades the substrate to retrieval-only."""
    app_state = make_app_state(
        slices={SettingsStateSlice: {"settings_service": _settings({})}},
    )

    result = await _maybe_build_knowledge_synthesizer(app_state, provider_registry=None)

    assert result is None


async def test_synthesis_enabled_not_consulted_in_build() -> None:
    """Ghost-wire: the synthesiser build no longer reads synthesis_enabled.

    The flag is enforced live at the ``/ask`` gate, so the build must not gate
    on it (it depends only on the model + a usable provider).
    """
    requested: list[str] = []

    async def _get(namespace: str, key: str) -> SimpleNamespace:
        del namespace
        requested.append(key)
        return SimpleNamespace(value="")

    app_state = make_app_state(
        slices={
            SettingsStateSlice: {"settings_service": mock_of[SettingsService](get=_get)}
        },
    )

    result = await _maybe_build_knowledge_synthesizer(
        app_state, provider_registry=mock_of[ProviderRegistry]()
    )

    assert result is None
    assert "synthesis_enabled" not in requested


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
