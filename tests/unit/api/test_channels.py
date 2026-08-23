"""Tests for channel configuration."""

import json
from datetime import UTC, datetime

import pytest
from litestar.channels import ChannelsPlugin

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
    CHANNEL_EVENTS,
    CHANNEL_INTERRUPTS,
    CHANNEL_MESSAGES,
    CHANNEL_PLANS,
    CHANNEL_PROJECTS,
    CHANNEL_RATELIMIT,
    CHANNEL_REQUESTS,
    CHANNEL_REVIEWS,
    CHANNEL_SIMULATIONS,
    CHANNEL_SYSTEM,
    CHANNEL_TASKS,
    CHANNEL_WEBHOOKS,
    CHANNEL_WORKFLOWS,
    create_channels_plugin,
    extract_user_id,
    is_user_channel,
    make_plan_notifier,
    plan_updated_payload,
    user_channel,
)
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from tests._shared import FakeClock, mock_of, sid


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
            CHANNEL_ARTIFACTS,
            CHANNEL_PROJECTS,
            CHANNEL_PLANS,
            CHANNEL_COMPANY,
            CHANNEL_DEPARTMENTS,
            CHANNEL_CLIENTS,
            CHANNEL_REQUESTS,
            CHANNEL_SIMULATIONS,
            CHANNEL_REVIEWS,
            CHANNEL_EVENTS,
            CHANNEL_INTERRUPTS,
            CHANNEL_COCKPIT,
            CHANNEL_WORKFLOWS,
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


_PLAN_TIME = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


@pytest.mark.unit
class TestPlanNotifier:
    """The background plan publisher speaks the controllers' payload."""

    @staticmethod
    def _plan() -> Plan:
        return Plan(
            project=NotBlankStr(sid("proj-1")),
            project_name=NotBlankStr("Games"),
            objective_id=NotBlankStr("obj-1"),
            objective_title=NotBlankStr("Ship the board"),
            parent_task_id=NotBlankStr(sid("task-1")),
            items=(
                PlanItem(
                    id=NotBlankStr(sid("item-1")),
                    title=NotBlankStr("Build it"),
                    description=NotBlankStr("Implement the board."),
                    acceptance_criteria=(NotBlankStr("it renders"),),
                    expected_artifacts=(NotBlankStr("web/src/board.tsx"),),
                ),
            ),
            status=PlanStatus.PENDING_REVIEW,
            version=3,
            created_at=_PLAN_TIME,
            updated_at=_PLAN_TIME,
        )

    def test_it_publishes_the_shared_locator_on_the_plans_channel(self) -> None:
        # The gate publishes from a background spine, so nothing downstream
        # would notice a payload that drifted from what the controllers send
        # until a subscriber read a key that was not there.
        plan = self._plan()
        published: list[tuple[str, list[str]]] = []
        plugin = mock_of[ChannelsPlugin](
            publish=lambda data, channels: published.append((data, channels))
        )

        make_plan_notifier(plugin, clock=FakeClock())(plan)

        assert len(published) == 1
        data, channels = published[0]
        assert channels == [CHANNEL_PLANS]
        event = json.loads(data)
        assert event["event_type"] == WsEventType.PLAN_UPDATED.value
        assert event["payload"] == plan_updated_payload(plan)

    def test_the_payload_names_the_plan_its_version_and_its_status(self) -> None:
        plan = self._plan()

        assert plan_updated_payload(plan) == {
            "plan_id": str(plan.id),
            "version": 3,
            "status": PlanStatus.PENDING_REVIEW.value,
        }

    def test_a_supersedes_payload_survives_envelope_validation(self) -> None:
        """The declared payload and what a replan publishes have to agree.

        ``PAYLOAD_CONFIG`` forbids extras and ``WsEvent`` validates every
        payload against the union on construction, so a key the model does
        not declare is not an ignored field: it raises here and the
        subscriber gets no event at all. Asserting the dict alone would
        have passed while the replan event never reached the dashboard.
        """
        plan = self._plan()
        retired = self._plan()

        payload = plan_updated_payload(plan, supersedes=retired)

        assert payload["supersedes"] == str(retired.id)
        event = WsEvent(
            event_type=WsEventType.PLAN_UPDATED,
            channel=CHANNEL_PLANS,
            timestamp=FakeClock().now(),
            payload=payload,
        )
        assert event.payload["supersedes"] == str(retired.id)
