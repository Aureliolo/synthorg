"""Registry coverage for ``coordination.decomposition_model``.

The coordinator's LLM decomposition strategy resolves its model id
from this Cat-1 setting at boot (DB > env > code default). It is a
plain mutable string entry whose code default is blank: a
provider-present boot validates the resolved value and raises a
startup error when it is empty. A runtime change applies on the next
coordinator rebuild (provider re-init).
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import mock_of

pytestmark = pytest.mark.unit


@pytest.fixture
def service() -> SettingsService:
    repo = mock_of[SettingsRepository]()
    repo.get.return_value = None
    repo.get_namespace.return_value = ()
    repo.list_items.return_value = ()
    repo.save.return_value = True
    return SettingsService(
        repository=repo,
        registry=get_registry(),
    )


def test_decomposition_model_registered_mutable() -> None:
    defn = get_registry().get("coordination", "decomposition_model")
    assert defn is not None
    assert defn.default == ""
    assert defn.compose_set is False


def test_routing_policy_defaults_to_llm_judged() -> None:
    # The intelligent policy is the shipped default: it asks the
    # decomposition model whether a brief needs a team (so a whole project
    # brief decomposes while a one-line fix stays solo), falling back to
    # the leaf-threshold heuristic on model error.
    defn = get_registry().get("coordination", "routing_policy")
    assert defn is not None
    assert defn.default == "llm-judged"
    assert "llm-judged" in defn.enum_values


async def test_decomposition_model_falls_back_to_default(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "SYNTHORG_COORDINATION_DECOMPOSITION_MODEL",
        raising=False,
    )
    value = await service.get("coordination", "decomposition_model")
    assert value.value == ""


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
    # A model assignment must bind both provider and model; a bare id is
    # rejected at write-time.
    ref = serialize_model_ref(
        ModelRef(provider="example-provider", model_id="example-expert-001")
    )
    await service.set("coordination", "decomposition_model", ref)
    repo.save.assert_awaited_once()


async def test_decomposition_model_set_rejects_bare_id(
    service: SettingsService,
) -> None:
    from synthorg.settings.errors import SettingValidationError

    with pytest.raises(SettingValidationError, match="provider is required"):
        await service.set("coordination", "decomposition_model", "example-expert-001")
