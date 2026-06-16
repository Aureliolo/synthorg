"""Tests for the static model-presence probe."""

from unittest.mock import patch

import pytest

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.management.model_presence_probe import (
    ModelPresenceProbe,
    ModelPresenceReport,
    StaticPresenceProbe,
)


def _provider(*ids: str) -> ProviderConfig:
    return ProviderConfig(
        litellm_provider="test-provider",
        models=tuple(ProviderModelConfig(id=i) for i in ids),
    )


@pytest.mark.unit
class TestStaticPresenceProbe:
    async def test_flags_absent_model(self) -> None:
        probe = StaticPresenceProbe()
        available = (ProviderModelConfig(id="present-1"),)
        with patch(
            "synthorg.providers.management.model_presence_probe.models_from_litellm",
            return_value=available,
        ):
            report = await probe.probe("test", _provider("present-1", "gone-2"))
        assert report.missing_ids == ("gone-2",)
        assert set(report.checked_ids) == {"present-1", "gone-2"}

    async def test_all_present_is_clean(self) -> None:
        probe = StaticPresenceProbe()
        available = (
            ProviderModelConfig(id="m-1"),
            ProviderModelConfig(id="m-2"),
        )
        with patch(
            "synthorg.providers.management.model_presence_probe.models_from_litellm",
            return_value=available,
        ):
            report = await probe.probe("test", _provider("m-1", "m-2"))
        assert report.missing_ids == ()

    async def test_empty_catalogue_is_noop(self) -> None:
        probe = StaticPresenceProbe()
        with patch(
            "synthorg.providers.management.model_presence_probe.models_from_litellm",
            return_value=(),
        ):
            report = await probe.probe("local", _provider("local-model"))
        # No catalogue to check against: no false "absent".
        assert report.missing_ids == ()
        assert report.checked_ids == ()

    def test_satisfies_protocol(self) -> None:
        assert isinstance(StaticPresenceProbe(), ModelPresenceProbe)

    def test_report_is_frozen(self) -> None:
        report = ModelPresenceReport(provider_name="p")
        with pytest.raises(Exception):  # noqa: B017, PT011 -- frozen pydantic
            report.provider_name = "q"  # type: ignore[misc]
