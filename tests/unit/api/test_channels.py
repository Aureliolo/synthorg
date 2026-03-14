"""Tests for channel configuration."""

import pytest

from synthorg.api.channels import (
    ALL_CHANNELS,
    CHANNEL_AGENTS,
    CHANNEL_APPROVALS,
    CHANNEL_BUDGET,
    CHANNEL_MEETINGS,
    CHANNEL_MESSAGES,
    CHANNEL_SYSTEM,
    CHANNEL_TASKS,
    create_channels_plugin,
)


@pytest.mark.unit
class TestChannels:
    @pytest.mark.parametrize(
        "channel",
        [
            CHANNEL_TASKS,
            CHANNEL_AGENTS,
            CHANNEL_BUDGET,
            CHANNEL_MESSAGES,
            CHANNEL_SYSTEM,
            CHANNEL_APPROVALS,
            CHANNEL_MEETINGS,
        ],
    )
    def test_all_channels_contains_expected(self, channel: str) -> None:
        assert channel in ALL_CHANNELS

    def test_all_channels_has_seven_entries(self) -> None:
        assert len(ALL_CHANNELS) == 7

    def test_create_channels_plugin(self) -> None:
        plugin = create_channels_plugin()
        assert plugin is not None
