"""Tests for channel configuration."""

import pytest

from synthorg.api.channels import (
    ALL_CHANNELS,
    BUDGET_CHANNELS,
    CHANNEL_AGENTS,
    CHANNEL_APPROVALS,
    CHANNEL_ARTIFACTS,
    CHANNEL_BUDGET,
    CHANNEL_CLIENTS,
    CHANNEL_COCKPIT,
    CHANNEL_COMPANY,
    CHANNEL_DEPARTMENTS,
    CHANNEL_DISSENT,
    CHANNEL_EVENTS,
    CHANNEL_INTERRUPTS,
    CHANNEL_MEETINGS,
    CHANNEL_MESSAGES,
    CHANNEL_PROJECTS,
    CHANNEL_RATELIMIT,
    CHANNEL_REQUESTS,
    CHANNEL_REVIEWS,
    CHANNEL_SIMULATIONS,
    CHANNEL_SYSTEM,
    CHANNEL_TASKS,
    CHANNEL_WEBHOOKS,
    create_channels_plugin,
    extract_user_id,
    is_user_channel,
    user_channel,
)


@pytest.mark.unit
class TestChannels:
    @pytest.mark.parametrize(
        "channel",
        [
            CHANNEL_TASKS,
            CHANNEL_AGENTS,
            CHANNEL_BUDGET,
            CHANNEL_CLIENTS,
            CHANNEL_MESSAGES,
            CHANNEL_SYSTEM,
            CHANNEL_APPROVALS,
            CHANNEL_MEETINGS,
            CHANNEL_ARTIFACTS,
            CHANNEL_PROJECTS,
            CHANNEL_COMPANY,
            CHANNEL_DEPARTMENTS,
            CHANNEL_REQUESTS,
            CHANNEL_REVIEWS,
            CHANNEL_SIMULATIONS,
        ],
    )
    def test_all_channels_contains_expected(self, channel: str) -> None:
        assert channel in ALL_CHANNELS

    def test_all_channels_has_expected_entries(self) -> None:
        expected = {
            CHANNEL_TASKS,
            CHANNEL_AGENTS,
            CHANNEL_BUDGET,
            CHANNEL_MESSAGES,
            CHANNEL_SYSTEM,
            CHANNEL_APPROVALS,
            CHANNEL_MEETINGS,
            CHANNEL_ARTIFACTS,
            CHANNEL_PROJECTS,
            CHANNEL_COMPANY,
            CHANNEL_DEPARTMENTS,
            CHANNEL_CLIENTS,
            CHANNEL_REQUESTS,
            CHANNEL_SIMULATIONS,
            CHANNEL_REVIEWS,
            CHANNEL_EVENTS,
            CHANNEL_INTERRUPTS,
            CHANNEL_COCKPIT,
            CHANNEL_DISSENT,
            CHANNEL_WEBHOOKS,
            CHANNEL_RATELIMIT,
        }
        assert set(ALL_CHANNELS) == expected

    def test_budget_channels_include_sensitive_integration_channels(self) -> None:
        """``#webhooks`` and ``#ratelimit`` must be restricted to system roles."""
        assert CHANNEL_BUDGET in BUDGET_CHANNELS
        assert CHANNEL_WEBHOOKS in BUDGET_CHANNELS
        assert CHANNEL_RATELIMIT in BUDGET_CHANNELS

    def test_create_channels_plugin(self) -> None:
        plugin = create_channels_plugin()
        assert plugin is not None
        # ChannelsPlugin exposes no public accessor for configuration;
        # private attrs are used intentionally for security verification.
        # Arbitrary channels are enabled for dynamic user:{id} channels.
        assert plugin._arbitrary_channels_allowed is True
        assert set(plugin._channels) == set(ALL_CHANNELS)


@pytest.mark.unit
class TestUserChannelHelpers:
    def test_user_channel_returns_prefixed(self) -> None:
        assert user_channel("abc") == "user:abc"

    def test_is_user_channel_true(self) -> None:
        assert is_user_channel("user:abc") is True

    def test_is_user_channel_false(self) -> None:
        assert is_user_channel("tasks") is False

    def test_extract_user_id_valid(self) -> None:
        assert extract_user_id("user:abc") == "abc"

    def test_extract_user_id_non_user_channel(self) -> None:
        assert extract_user_id("tasks") is None

    def test_extract_user_id_empty_suffix(self) -> None:
        assert extract_user_id("user:") == ""
