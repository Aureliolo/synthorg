"""Registry coverage for ``coordination.decomposition_model``.

The coordinator's LLM decomposition strategy resolves its model id
from this Cat-1 setting at boot (DB > env > code default). It is a
plain mutable string entry: a runtime change applies on the next
coordinator rebuild (provider re-init).
"""

from unittest.mock import AsyncMock

import pytest

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
    return SettingsService(
        repository=repo,
        registry=get_registry(),
    )


def test_decomposition_model_registered_mutable() -> None:
    defn = get_registry().get("coordination", "decomposition_model")
    assert defn is not None
    assert defn.default == "example-medium-001"
    assert defn.read_only_post_init is False
    assert defn.restart_required is False


async def test_decomposition_model_falls_back_to_default(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "SYNTHORG_COORDINATION_DECOMPOSITION_MODEL",
        raising=False,
    )
    value = await service.get("coordination", "decomposition_model")
    assert value.value == "example-medium-001"


async def test_decomposition_model_resolves_through_env(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SYNTHORG_COORDINATION_DECOMPOSITION_MODEL",
        "env-model-001",
    )
    value = await service.get("coordination", "decomposition_model")
    assert value.value == "env-model-001"


async def test_decomposition_model_set_succeeds(
    service: SettingsService,
) -> None:
    repo: AsyncMock = service._repository  # type: ignore[assignment]
    await service.set("coordination", "decomposition_model", "example-large-001")
    repo.save.assert_awaited_once()
