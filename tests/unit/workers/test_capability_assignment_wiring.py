"""Tests for the settings-backed tier-assignment wiring."""

from collections.abc import Callable
from typing import Any

import pytest

from synthorg.providers.capability_assignment.errors import (
    CapabilityClassifierDisabledError,
    CapabilityClassifierModelUnsetError,
    CapabilityOverrideStoreReadOnlyError,
)
from synthorg.providers.capability_assignment.models import (
    CAPABILITY_ASSIGNMENT_SCHEMA_VERSION,
    CapabilityOverrideMap,
)
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice
from synthorg.workers._capability_assignment_wiring import (
    SettingsCapabilityOverrideStore,
    build_capability_recommender,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _returns(value: object) -> Callable[..., Any]:  # type: ignore[explicit-any]
    """Return an async stub that yields *value*."""

    async def _fn(*_args: object, **_kwargs: object) -> object:
        return value

    return _fn


def _raises(exc: Exception) -> Callable[..., Any]:  # type: ignore[explicit-any]
    """Return an async stub that raises *exc*."""

    async def _fn(*_args: object, **_kwargs: object) -> object:
        raise exc

    return _fn


class _RecordingSet:
    """Async ``settings_service.set`` double counting invocations."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, *_args: object, **_kwargs: object) -> None:
        self.calls += 1


def _store(get_json: Callable[..., Any]) -> SettingsCapabilityOverrideStore:  # type: ignore[explicit-any]
    return SettingsCapabilityOverrideStore(
        resolver=mock_of[ConfigResolver](get_json=get_json),
        settings_service=None,
    )


class TestOverrideStoreLoad:
    async def test_no_resolver_returns_empty(self) -> None:
        store = SettingsCapabilityOverrideStore(resolver=None, settings_service=None)
        assert await store.load() == CapabilityOverrideMap()

    async def test_none_blob_returns_empty(self) -> None:
        store = _store(_returns(None))
        assert await store.load() == CapabilityOverrideMap()

    async def test_valid_blob_round_trips(self) -> None:
        store = _store(_returns(CapabilityOverrideMap().model_dump()))
        assert (
            await store.load()
        ).schema_version == CAPABILITY_ASSIGNMENT_SCHEMA_VERSION

    async def test_read_error_degrades_to_empty(self) -> None:
        store = _store(_raises(ValueError("corrupt")))
        assert await store.load() == CapabilityOverrideMap()

    async def test_unknown_version_degrades_to_empty(self) -> None:
        store = _store(_returns({"schema_version": 999, "overrides": []}))
        assert await store.load() == CapabilityOverrideMap()


class TestOverrideStoreSave:
    async def test_read_only_store_raises(self) -> None:
        store = SettingsCapabilityOverrideStore(
            resolver=mock_of[ConfigResolver](), settings_service=None
        )
        with pytest.raises(CapabilityOverrideStoreReadOnlyError):
            await store.save(CapabilityOverrideMap())

    async def test_save_persists_through_settings_service(self) -> None:
        recorder = _RecordingSet()
        store = SettingsCapabilityOverrideStore(
            resolver=mock_of[ConfigResolver](),
            settings_service=mock_of[SettingsService](set=recorder),
        )
        await store.save(CapabilityOverrideMap())
        assert recorder.calls == 1


class TestBuildRecommender:
    async def test_disabled_raises(self) -> None:
        app_state = make_app_state(
            config_resolver=mock_of[ConfigResolver](get_bool=_returns(False)),
        )
        with pytest.raises(CapabilityClassifierDisabledError):
            await build_capability_recommender(app_state)

    async def test_enabled_but_unset_model_raises(self) -> None:
        app_state = make_app_state(
            config_resolver=mock_of[ConfigResolver](
                get_bool=_returns(True),
                get_str=_returns(""),
            ),
        )
        with pytest.raises(CapabilityClassifierModelUnsetError):
            await build_capability_recommender(app_state)

    async def test_no_resolver_raises_unset(self) -> None:
        app_state = make_app_state(config_resolver=None)
        assert app_state.slice(SettingsStateSlice).config_resolver is None
        with pytest.raises(CapabilityClassifierModelUnsetError):
            await build_capability_recommender(app_state)
