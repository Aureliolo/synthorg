"""Coverage for the ``simulations`` intake settings.

``simulations.intake_strategy`` / ``simulations.intake_model`` are
read at app construction (boot) via the bootstrap resolver, so they
are ``read_only_post_init`` (mutation through ``SettingsService.set``
is rejected) yet still resolve env > default through the standard
chain.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace, SettingSource, SettingType
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


@pytest.fixture
def service() -> SettingsService:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get.return_value = None
    repo.get_namespace.return_value = ()
    repo.list_items.return_value = ()
    return SettingsService(repository=repo, registry=get_registry())


def test_intake_strategy_registered() -> None:
    defn = get_registry().get("simulations", "intake_strategy")
    assert defn is not None
    assert defn.type is SettingType.ENUM
    assert defn.default == "direct"
    assert defn.enum_values == ("direct", "agent")
    assert defn.read_only_post_init is True
    assert defn.restart_required is True


def test_intake_model_registered() -> None:
    defn = get_registry().get("simulations", "intake_model")
    assert defn is not None
    assert defn.type is SettingType.STRING
    assert defn.default is None
    assert defn.read_only_post_init is True
    assert defn.restart_required is True


async def test_intake_strategy_default_resolves(service: SettingsService) -> None:
    result = await service.get("simulations", "intake_strategy")
    assert result.value == "direct"
    assert result.source is SettingSource.DEFAULT


def test_intake_strategy_bootstrap_default() -> None:
    resolved = resolve_init_value(
        SettingNamespace.SIMULATIONS,
        "intake_strategy",
        env={},
    )
    assert resolved.value == "direct"
    assert resolved.source is SettingSource.DEFAULT


def test_intake_strategy_bootstrap_env_override() -> None:
    resolved = resolve_init_value(
        SettingNamespace.SIMULATIONS,
        "intake_strategy",
        env={"SYNTHORG_SIMULATIONS_INTAKE_STRATEGY": "agent"},
    )
    assert resolved.value == "agent"
    assert resolved.source is SettingSource.ENVIRONMENT


def test_intake_model_bootstrap_default_is_empty() -> None:
    resolved = resolve_init_value(
        SettingNamespace.SIMULATIONS,
        "intake_model",
        env={},
    )
    assert resolved.value == ""
    assert resolved.source is SettingSource.DEFAULT


def test_intake_model_bootstrap_env_override() -> None:
    resolved = resolve_init_value(
        SettingNamespace.SIMULATIONS,
        "intake_model",
        env={"SYNTHORG_SIMULATIONS_INTAKE_MODEL": "test-model-001"},
    )
    assert resolved.value == "test-model-001"
    assert resolved.source is SettingSource.ENVIRONMENT
