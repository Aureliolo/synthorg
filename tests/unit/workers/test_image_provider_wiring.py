"""Tests for the boot image-provider wiring from the ``design`` settings."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.design.provider_image_provider import ProviderImageProvider
from synthorg.workers._image_provider_wiring import build_image_provider_or_none
from tests._shared import mock_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

pytestmark = pytest.mark.unit

_MODEL_ID = "example-image-001"
# design.image_model is an explicit (provider, model) MODEL_REF.
_MODEL = serialize_model_ref(ModelRef(provider="example-provider", model_id=_MODEL_ID))


def _caps(*, image: bool) -> ModelCapabilities:
    return ModelCapabilities(
        model_id=_MODEL_ID,
        provider="example-provider",
        max_context_tokens=1,
        max_output_tokens=1,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        supports_image_generation=image,
    )


def _app_state(registry: ProviderRegistry | None) -> AppState:
    slice_obj = SimpleNamespace(registry=registry)
    return cast("AppState", SimpleNamespace(slice=lambda _cls: slice_obj))


def _resolver(*, enabled: bool, model: str) -> ConfigResolver:
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = enabled
    resolver.get_str.return_value = model
    return cast("ConfigResolver", resolver)


def _registry(*, image: bool) -> ProviderRegistry:
    provider = ScriptedDriver(capabilities=_caps(image=image))
    registry = mock_of[ProviderRegistry]()
    registry.get.return_value = provider
    return cast("ProviderRegistry", registry)


def _patch_resolver(monkeypatch: pytest.MonkeyPatch, resolver: ConfigResolver) -> None:
    monkeypatch.setattr(
        "synthorg.workers._image_provider_wiring.config_resolver_of",
        lambda _app_state: resolver,
    )


async def test_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolver(monkeypatch, _resolver(enabled=False, model=_MODEL))
    result = await build_image_provider_or_none(_app_state(_registry(image=True)))
    assert result is None


async def test_returns_none_when_model_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolver(monkeypatch, _resolver(enabled=True, model="   "))
    result = await build_image_provider_or_none(_app_state(_registry(image=True)))
    assert result is None


async def test_returns_none_when_no_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, _resolver(enabled=True, model=_MODEL))
    result = await build_image_provider_or_none(_app_state(None))
    assert result is None


async def test_returns_none_when_model_not_image_capable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, _resolver(enabled=True, model=_MODEL))
    result = await build_image_provider_or_none(_app_state(_registry(image=False)))
    assert result is None


async def test_builds_provider_when_enabled_and_image_capable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, _resolver(enabled=True, model=_MODEL))
    result = await build_image_provider_or_none(_app_state(_registry(image=True)))
    assert isinstance(result, ProviderImageProvider)


async def test_returns_none_when_enabled_flag_resolve_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.side_effect = RuntimeError("settings backend down")
    _patch_resolver(monkeypatch, cast("ConfigResolver", resolver))
    result = await build_image_provider_or_none(_app_state(_registry(image=True)))
    assert result is None


async def test_returns_none_when_model_resolve_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, _resolver(enabled=True, model=_MODEL))
    registry = mock_of[ProviderRegistry]()
    registry.get.side_effect = DriverNotRegisteredError(
        "not registered", context={"provider": "example-provider"}
    )
    result = await build_image_provider_or_none(
        _app_state(cast("ProviderRegistry", registry))
    )
    assert result is None


async def test_returns_none_when_provider_not_image_generation_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Capability-flagged image-capable, but the serving object structurally
    # lacks ``generate_image`` -- the isinstance narrowing must fail open.
    _patch_resolver(monkeypatch, _resolver(enabled=True, model=_MODEL))

    async def _caps_call(*_args: object, **_kwargs: object) -> ModelCapabilities:
        return _caps(image=True)

    non_image_provider = SimpleNamespace(get_model_capabilities=_caps_call)
    registry = mock_of[ProviderRegistry]()
    registry.get.return_value = non_image_provider
    result = await build_image_provider_or_none(
        _app_state(cast("ProviderRegistry", registry))
    )
    assert result is None
