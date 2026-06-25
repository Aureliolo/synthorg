"""Registry coverage for the ``knowledge`` synthesis settings.

The knowledge ask surface (generative RAG) resolves its enable flag,
provider, model, strategy, and chunk budget from these settings at boot.
Synthesis is on by default (opt-out) but functionally gated on a configured
model: the ask surface 503s until ``knowledge.synthesis_model`` is set.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.knowledge.constants import KNOWLEDGE_SYNTHESIS_DEFAULT_MAX_CHUNKS
from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


@pytest.fixture
def service() -> SettingsService:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.get_namespace = AsyncMock(return_value=())
    repo.list_items = AsyncMock(return_value=())
    repo.save = AsyncMock(return_value=True)
    return SettingsService(repository=repo, registry=get_registry())


def test_synthesis_enabled_defaults_on() -> None:
    defn = get_registry().get("knowledge", "synthesis_enabled")
    assert defn is not None
    assert defn.default == "true"


def test_synthesis_model_defaults_blank_and_mutable() -> None:
    defn = get_registry().get("knowledge", "synthesis_model")
    assert defn is not None
    assert defn.default == ""
    assert defn.read_only_post_init is False


def test_synthesis_max_chunks_default_matches_constant() -> None:
    defn = get_registry().get("knowledge", "synthesis_max_chunks")
    assert defn is not None
    assert defn.default == str(KNOWLEDGE_SYNTHESIS_DEFAULT_MAX_CHUNKS)


async def test_synthesis_model_resolves_through_env(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNTHORG_KNOWLEDGE_SYNTHESIS_MODEL", "env-model-001")
    value = await service.get("knowledge", "synthesis_model")
    assert value.value == "env-model-001"


async def test_synthesis_model_set_succeeds(service: SettingsService) -> None:
    repo: AsyncMock = service._repository  # type: ignore[assignment]
    await service.set("knowledge", "synthesis_model", "example-large-001")
    repo.save.assert_awaited_once()
