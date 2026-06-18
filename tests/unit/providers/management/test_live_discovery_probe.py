"""Tests for the live-discovery model-presence probe."""

from unittest.mock import AsyncMock

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.management.live_discovery_probe import (
    LiveDiscoveryProbe,
    ReadonlyModelDiscovery,
)
from synthorg.providers.management.model_presence_probe import ModelPresenceProbe
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _model(model_id: str, *, source: str = "litellm") -> ProviderModelConfig:
    return ProviderModelConfig(
        id=model_id,
        metadata=ModelMetadata(metadata_source=source),  # type: ignore[arg-type]
    )


def _local_provider(*model_ids: str) -> ProviderConfig:
    return ProviderConfig(
        base_url="http://localhost:11434",
        models=tuple(_model(m) for m in model_ids),
    )


def _cloud_provider(*model_ids: str) -> ProviderConfig:
    return ProviderConfig(
        litellm_provider="example-provider",
        models=tuple(_model(m) for m in model_ids),
    )


class TestLiveDiscoveryProbe:
    def test_satisfies_presence_probe_protocol(self) -> None:
        probe = LiveDiscoveryProbe(
            discovery=mock_of[ReadonlyModelDiscovery](),
            catalog=lambda _p: (),
        )
        assert isinstance(probe, ModelPresenceProbe)

    async def test_live_path_stamps_probe_source(self) -> None:
        discovery = mock_of[ReadonlyModelDiscovery](
            discover_models_readonly=AsyncMock(
                return_value=(_model("m1"), _model("m2"))
            ),
        )
        probe = LiveDiscoveryProbe(discovery=discovery, catalog=lambda _p: ())
        report = await probe.discover_report("local", _local_provider("m1"))
        assert {m.id for m in report.discovered} == {"m1", "m2"}
        assert all(m.metadata.metadata_source == "probe" for m in report.discovered)
        assert report.added_ids == ("m2",)
        assert report.missing_ids == ()

    async def test_live_path_flags_missing_configured_model(self) -> None:
        discovery = mock_of[ReadonlyModelDiscovery](
            discover_models_readonly=AsyncMock(return_value=(_model("m1"),)),
        )
        probe = LiveDiscoveryProbe(discovery=discovery, catalog=lambda _p: ())
        report = await probe.discover_report("local", _local_provider("m1", "gone"))
        assert report.missing_ids == ("gone",)
        assert report.checked_ids == ("m1", "gone")

    async def test_cloud_fallback_keeps_litellm_source(self) -> None:
        discovery = mock_of[ReadonlyModelDiscovery](
            discover_models_readonly=AsyncMock(return_value=()),
        )
        probe = LiveDiscoveryProbe(
            discovery=discovery,
            catalog=lambda _p: (_model("m1", source="litellm"),),
        )
        report = await probe.discover_report("cloud", _cloud_provider("m1"))
        assert {m.id for m in report.discovered} == {"m1"}
        assert report.discovered[0].metadata.metadata_source == "litellm"
        discovery.discover_models_readonly.assert_not_called()

    async def test_empty_discovery_is_documented_noop(self) -> None:
        discovery = mock_of[ReadonlyModelDiscovery](
            discover_models_readonly=AsyncMock(return_value=()),
        )
        probe = LiveDiscoveryProbe(discovery=discovery, catalog=lambda _p: ())
        report = await probe.discover_report("local", _local_provider("m1"))
        assert report.discovered == ()
        assert report.missing_ids == ()
        assert report.checked_ids == ()

    async def test_probe_projects_to_presence_report(self) -> None:
        discovery = mock_of[ReadonlyModelDiscovery](
            discover_models_readonly=AsyncMock(return_value=(_model("m1"),)),
        )
        probe = LiveDiscoveryProbe(discovery=discovery, catalog=lambda _p: ())
        presence = await probe.probe("local", _local_provider("m1", "gone"))
        assert presence.provider_name == "local"
        assert presence.missing_ids == ("gone",)
        assert presence.checked_ids == ("m1", "gone")
