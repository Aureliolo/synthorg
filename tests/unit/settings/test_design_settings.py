"""Tests for the ``design`` settings namespace definitions."""

import pytest

import synthorg.settings.definitions  # noqa: F401 -- registers all definitions
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

pytestmark = pytest.mark.unit


def _get(key: str) -> SettingDefinition | None:
    return get_registry().get(SettingNamespace.DESIGN, key)


def test_image_generation_enabled_defaults_off() -> None:
    definition = _get("image_generation_enabled")
    assert definition is not None
    assert definition.type is SettingType.BOOLEAN
    assert definition.default == "false"
    assert definition.compose_set is False


def test_image_model_is_model_ref_unset_by_default() -> None:
    definition = _get("image_model")
    assert definition is not None
    assert definition.type is SettingType.MODEL_REF
    assert definition.default == ""
    assert definition.compose_set is False
