"""Tests for channel configuration."""

import pytest

from ai_company.api.channels import (
    ALL_CHANNELS,
    CHANNEL_AGENTS,
    CHANNEL_BUDGET,
    CHANNEL_MESSAGES,
    CHANNEL_SYSTEM,
    CHANNEL_TASKS,
    create_channels_plugin,
)


@pytest.mark.unit
class TestChannels:
    def test_all_channels_contains_expected(self) -> None:
        assert CHANNEL_TASKS in ALL_CHANNELS
        assert CHANNEL_AGENTS in ALL_CHANNELS
        assert CHANNEL_BUDGET in ALL_CHANNELS
        assert CHANNEL_MESSAGES in ALL_CHANNELS
        assert CHANNEL_SYSTEM in ALL_CHANNELS

    def test_all_channels_has_five_entries(self) -> None:
        assert len(ALL_CHANNELS) == 5

    def test_create_channels_plugin(self) -> None:
        plugin = create_channels_plugin()
        assert plugin is not None
