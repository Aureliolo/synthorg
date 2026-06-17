"""Coverage for the ``providers.model_refresh_*`` settings.

These drive the periodic model-refresh subsystem. The ``model_refresh_mode``
enum values MUST stay in lock-step with
:class:`synthorg.providers.management.refresh_config.RefreshMode` (kept
literal in the definition to avoid a definitions -> providers import cycle),
and the interval floor MUST match the scheduler minimum.
"""

import pytest

from synthorg.providers.management.refresh_config import (
    REFRESH_MODE_VALUES,
    ModelRefreshConfig,
)
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.enums import SettingType
from synthorg.settings.registry import get_registry

pytestmark = pytest.mark.unit


def test_model_refresh_mode_registered() -> None:
    defn = get_registry().get("providers", "model_refresh_mode")
    assert defn is not None
    assert defn.type is SettingType.ENUM
    assert defn.default == "off"
    assert defn.enum_values == REFRESH_MODE_VALUES


def test_model_refresh_interval_registered() -> None:
    defn = get_registry().get("providers", "model_refresh_interval_seconds")
    assert defn is not None
    assert defn.type is SettingType.FLOAT
    assert defn.default == "86400.0"
    assert defn.min_value == 60.0
    assert defn.max_value == 604800.0


def test_model_refresh_auto_apply_registered() -> None:
    defn = get_registry().get("providers", "model_refresh_auto_apply_within_family")
    assert defn is not None
    assert defn.type is SettingType.BOOLEAN
    assert defn.default == "false"


def test_registered_defaults_match_config_defaults() -> None:
    cfg = ModelRefreshConfig()
    mode = get_registry().get("providers", "model_refresh_mode")
    interval = get_registry().get("providers", "model_refresh_interval_seconds")
    auto = get_registry().get("providers", "model_refresh_auto_apply_within_family")
    assert mode is not None
    assert interval is not None
    assert auto is not None
    assert interval.default is not None
    assert cfg.mode.value == mode.default
    assert cfg.interval_seconds == float(interval.default)
    assert cfg.auto_apply_within_family is (auto.default == "true")
