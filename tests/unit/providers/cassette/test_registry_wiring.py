"""Unit tests for the cassette decoration chokepoint + boot wiring.

Pins: the single ``ProviderRegistry.from_config`` decoration point
wraps every driver exactly once when active; ``off`` is a structural
no-op (so the rest of the suite never runs through the cassette);
replay constructs no real driver (no factory call); and the boot-time
Cat-2 resolver honours env > default.
"""

from pathlib import Path

import pytest

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.cassette.mode import CassetteConfig, CassetteMode
from synthorg.providers.cassette.provider import CassetteCompletionProvider
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.registry import ProviderRegistry

pytestmark = pytest.mark.unit


def _config() -> ProviderConfig:
    return ProviderConfig(
        driver="scripted",
        api_key=None,
        models=(
            ProviderModelConfig(
                id="test-model",
                alias="test",
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            ),
        ),
    )


def _providers() -> dict[str, ProviderConfig]:
    return {"alpha": _config(), "beta": _config()}


class TestDecorationChokepoint:
    """``from_config`` is the single place drivers get wrapped."""

    def test_off_is_structural_no_op(self) -> None:
        reg = ProviderRegistry.from_config(_providers())
        assert isinstance(reg.get("alpha"), ScriptedDriver)
        assert reg.cassette_session is None

    def test_none_cassette_is_no_op(self) -> None:
        reg = ProviderRegistry.from_config(
            _providers(),
            cassette=CassetteConfig(mode=CassetteMode.OFF),
        )
        assert isinstance(reg.get("beta"), ScriptedDriver)
        assert reg.cassette_session is None

    def test_record_wraps_every_driver_once(self, tmp_path: Path) -> None:
        reg = ProviderRegistry.from_config(
            _providers(),
            cassette=CassetteConfig(
                mode=CassetteMode.RECORD,
                path=tmp_path / "c.json",
            ),
        )
        alpha = reg.get("alpha")
        beta = reg.get("beta")
        assert isinstance(alpha, CassetteCompletionProvider)
        assert isinstance(beta, CassetteCompletionProvider)
        assert alpha.provider_name == "alpha"
        assert beta.provider_name == "beta"
        assert reg.cassette_session is not None
        # One shared session across all wrapped drivers.
        assert alpha.session is beta.session


class TestReplayBuildsNoRealDriver:
    """Replay mode must not call any driver factory."""

    def test_replay_skips_factory(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        # A valid empty cassette so the replay session loads.
        rec = ProviderRegistry.from_config(
            _providers(),
            cassette=CassetteConfig(mode=CassetteMode.RECORD, path=path),
        )
        assert rec.cassette_session is not None
        rec.cassette_session.flush()

        calls: list[str] = []

        def _spy_factory(
            name: str,
            config: ProviderConfig,
        ) -> BaseCompletionProvider:
            del config
            calls.append(name)
            return ScriptedDriver(name)

        reg = ProviderRegistry.from_config(
            _providers(),
            factory_overrides={"scripted": _spy_factory},
            cassette=CassetteConfig(mode=CassetteMode.REPLAY, path=path),
        )
        assert calls == []  # zero real drivers constructed
        assert isinstance(reg.get("alpha"), CassetteCompletionProvider)

    def test_record_does_call_factory(self, tmp_path: Path) -> None:
        calls: list[str] = []

        def _spy_factory(
            name: str,
            config: ProviderConfig,
        ) -> BaseCompletionProvider:
            del config
            calls.append(name)
            return ScriptedDriver(name)

        ProviderRegistry.from_config(
            _providers(),
            factory_overrides={"scripted": _spy_factory},
            cassette=CassetteConfig(
                mode=CassetteMode.RECORD,
                path=tmp_path / "c.json",
            ),
        )
        assert sorted(calls) == ["alpha", "beta"]


class TestBootResolver:
    """The Cat-2 bootstrap resolver honours env > default."""

    def test_default_is_inert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from synthorg.api.auto_wire import _resolve_cassette_config

        monkeypatch.delenv("SYNTHORG_PROVIDERS_CASSETTE_MODE", raising=False)
        assert _resolve_cassette_config() is None

    def test_env_override_activates_replay(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from synthorg.api.auto_wire import _resolve_cassette_config

        target = tmp_path / "boot.json"
        monkeypatch.setenv("SYNTHORG_PROVIDERS_CASSETTE_MODE", "replay")
        monkeypatch.setenv("SYNTHORG_PROVIDERS_CASSETTE_PATH", str(target))
        cfg = _resolve_cassette_config()
        assert cfg is not None
        assert cfg.mode is CassetteMode.REPLAY
        assert cfg.path == target

    def test_active_mode_without_path_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from synthorg.api.auto_wire import _resolve_cassette_config

        monkeypatch.setenv("SYNTHORG_PROVIDERS_CASSETTE_MODE", "record")
        monkeypatch.delenv("SYNTHORG_PROVIDERS_CASSETTE_PATH", raising=False)
        with pytest.raises(ValueError, match="path is required"):
            _resolve_cassette_config()
