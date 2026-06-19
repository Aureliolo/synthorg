"""Tests for the startup-failure reverse cleanup helper.

``_cleanup_on_failure`` tears down already-started runtime background
services when ``_safe_startup`` raises. The runtime services (event
stream hub, escalation subscriber / sweeper, the health probers) start
AFTER the core services, so a crash after they start must stop them or
they leak background tasks. These tests pin that each service is stopped
only when its ``started_*`` flag is set, so a not-yet-started service is
never stopped on a ``None`` handle.
"""

import pytest

from synthorg.api.lifecycle_shared import _AsyncStartStop, _cleanup_on_failure
from tests._shared import mock_of

pytestmark = pytest.mark.unit


async def test_started_runtime_services_are_stopped() -> None:
    event_stream_hub = mock_of[_AsyncStartStop]()
    escalation_notify_subscriber = mock_of[_AsyncStartStop]()
    escalation_sweeper = mock_of[_AsyncStartStop]()
    provider_health_prober = mock_of[_AsyncStartStop]()

    await _cleanup_on_failure(
        persistence=None,
        started_persistence=False,
        message_bus=None,
        started_bus=False,
        event_stream_hub=event_stream_hub,
        started_event_stream_hub=True,
        escalation_notify_subscriber=escalation_notify_subscriber,
        started_escalation_notify_subscriber=True,
        escalation_sweeper=escalation_sweeper,
        started_escalation_sweeper=True,
        provider_health_prober=provider_health_prober,
        started_provider_health_prober=True,
    )

    event_stream_hub.stop.assert_awaited_once()
    escalation_notify_subscriber.stop.assert_awaited_once()
    escalation_sweeper.stop.assert_awaited_once()
    provider_health_prober.stop.assert_awaited_once()


async def test_not_started_services_are_skipped() -> None:
    # A service whose ``started_*`` flag is False was never started, so
    # cleanup must NOT call its stop() (the handle may be present but
    # unconnected).
    event_stream_hub = mock_of[_AsyncStartStop]()
    provider_health_prober = mock_of[_AsyncStartStop]()

    await _cleanup_on_failure(
        persistence=None,
        started_persistence=False,
        message_bus=None,
        started_bus=False,
        event_stream_hub=event_stream_hub,
        started_event_stream_hub=False,
        provider_health_prober=provider_health_prober,
        started_provider_health_prober=False,
    )

    event_stream_hub.stop.assert_not_awaited()
    provider_health_prober.stop.assert_not_awaited()


async def test_started_flag_true_but_handle_none_is_safe() -> None:
    # The both-or-neither contract: a True flag with a None handle must
    # not raise (the guard checks both), so a partially-threaded failure
    # path stays crash-free.
    await _cleanup_on_failure(
        persistence=None,
        started_persistence=False,
        message_bus=None,
        started_bus=False,
        event_stream_hub=None,
        started_event_stream_hub=True,
        provider_health_prober=None,
        started_provider_health_prober=True,
    )
