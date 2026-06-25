"""Tests for ``load_self_improvement_config`` and the feature overlay.

The Chief-of-Staff + self-improvement flags and per-feature models are
individual runtime settings (the single source of truth); the loader
overlays them onto the structural ``meta.self_improvement`` blob. An
empty blob with a real settings service therefore yields the
on-by-default posture, not pure code defaults, and a setting always wins
over a legacy flag value still carried in the blob.
"""

from collections.abc import AsyncIterator

import pytest

from synthorg.meta._config_overlay import overlay_feature_settings
from synthorg.meta.config import SelfImprovementConfig, load_self_improvement_config
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit


@pytest.fixture
async def settings_service() -> AsyncIterator[SettingsService]:
    """A settings service backed by a connected in-memory backend."""
    backend = FakePersistenceBackend()
    await backend.connect()
    yield SettingsService(repository=backend.settings, registry=get_registry())
    await backend.disconnect()


async def test_missing_service_returns_default() -> None:
    """``None`` service falls back to ``SelfImprovementConfig()`` defaults."""
    config = await load_self_improvement_config(None)
    assert config == SelfImprovementConfig()


async def test_empty_db_yields_on_by_default_posture(
    settings_service: SettingsService,
) -> None:
    """An empty blob + real settings yields the on-by-default posture."""
    config = await load_self_improvement_config(settings_service)
    cos = config.chief_of_staff
    # Conversational capabilities default on.
    assert cos.chat_enabled is True
    assert cos.propose_enabled is True
    assert cos.routing_enabled is True
    assert cos.group_chat_enabled is True
    # Background-spend + acts-on-your-behalf capabilities default off.
    assert cos.learning_enabled is False
    assert cos.alerts_enabled is False
    assert cos.narrative_enabled is False
    assert cos.invite_enabled is False
    assert cos.direct_mcp_enabled is False
    # The self-modification master stays off.
    assert config.enabled is False
    assert config.code_modification_enabled is False


async def test_setting_overrides_flag(settings_service: SettingsService) -> None:
    """A DB setting flips the corresponding config flag."""
    await settings_service.set("chief_of_staff", "group_chat_enabled", "false")
    config = await load_self_improvement_config(settings_service)
    assert config.chief_of_staff.group_chat_enabled is False


async def test_settings_win_over_legacy_blob_flag(
    settings_service: SettingsService,
) -> None:
    """A legacy blob flag never overrides the on-by-default setting."""
    await settings_service.set(
        "meta",
        "self_improvement",
        '{"chief_of_staff": {"chat_enabled": false}}',
    )
    config = await load_self_improvement_config(settings_service)
    assert config.chief_of_staff.chat_enabled is True


async def test_blank_model_keeps_builtin_default(
    settings_service: SettingsService,
) -> None:
    """A blank model setting keeps the config's built-in non-blank default."""
    config = await load_self_improvement_config(settings_service)
    assert (
        config.chief_of_staff.chat_model
        == SelfImprovementConfig().chief_of_staff.chat_model
    )


async def test_model_setting_lands(settings_service: SettingsService) -> None:
    """A non-blank model setting overrides the built-in default."""
    await settings_service.set("chief_of_staff", "chat_model", "example-large-001")
    config = await load_self_improvement_config(settings_service)
    assert config.chief_of_staff.chat_model == "example-large-001"


async def test_structural_blob_field_survives_overlay(
    settings_service: SettingsService,
) -> None:
    """A structural sub-config in the blob lands; flags stay from settings."""
    await settings_service.set(
        "meta",
        "self_improvement",
        '{"rules": {"disabled_rules": ["x"]}}',
    )
    config = await load_self_improvement_config(settings_service)
    assert config.rules.disabled_rules == ("x",)
    assert config.chief_of_staff.chat_enabled is True


async def test_overlay_couples_toolsmith_to_tool_creation(
    settings_service: SettingsService,
) -> None:
    """Tool creation with an allowlist enables a coherent toolsmith config."""
    await settings_service.set("self_improvement", "tool_creation_enabled", "true")
    await settings_service.set(
        "self_improvement",
        "tool_creation_allowed_capabilities",
        '["docs:summarize"]',
    )
    overrides = await overlay_feature_settings(settings_service, {})
    assert overrides["tool_creation_enabled"] is True
    toolsmith = overrides["toolsmith"]
    assert isinstance(toolsmith, dict)
    assert toolsmith["enabled"] is True
    assert toolsmith["allowed_capabilities"] == ["docs:summarize"]


async def test_tool_creation_without_allowlist_is_held_off(
    settings_service: SettingsService,
) -> None:
    """Enabling tool creation without an allowlist holds it off, not crashes.

    An empty allowlist is deny-all (the toolsmith validator rejects it), so a
    bad toolsmith sub-config must not sink the whole self-improvement posture:
    tool creation is downgraded to off while the master enable survives.
    """
    await settings_service.set("self_improvement", "enabled", "true")
    await settings_service.set("self_improvement", "tool_creation_enabled", "true")
    overrides = await overlay_feature_settings(settings_service, {})
    assert overrides["enabled"] is True
    assert overrides["tool_creation_enabled"] is False
    toolsmith = overrides["toolsmith"]
    assert isinstance(toolsmith, dict)
    assert toolsmith["enabled"] is False
    # The full config must validate (no exception) with the held-off toolsmith.
    config = await load_self_improvement_config(settings_service)
    assert config.enabled is True
    assert config.tool_creation_enabled is False
