"""A connection test moves the provider's health.

Health is derived from recorded call outcomes, and a connection test is a
real call to the provider. Leaving its verdict unrecorded is what left a
provider reading DOWN long after an operator had fixed it: the only control
that moved health was re-saving the provider, so testing it and refreshing
the page both reported the old aggregate back.

Every test here mocks the driver's completion call. The probe reaches
``LiteLLMDriver`` regardless of the configured driver name, so an unmocked
test would resolve a real host and spend its retry backoff on the wall clock.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.providers.errors import AuthenticationError

# Aliased because pytest collects any module-level ``Test*`` name as a test
# class, and the request model is not one.
from synthorg.providers.management.dtos import (
    TestConnectionRequest as ConnTestRequest,
)
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.probe_protocol import ProviderProbeRequester
from tests._shared import mock_of

from .conftest import make_create_request

_ACOMPLETION = "synthorg.providers.drivers.litellm_driver._litellm.acompletion"

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]


def _requester() -> _Configured:
    """An autospec'd probe requester double.

    Returns:
        A substitute bound to the :class:`ProviderProbeRequester` signature.
    """
    return mock_of[ProviderProbeRequester]()


@contextmanager
def _answers() -> Iterator[None]:
    """Stand in for a provider that completes the probe."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "pong"
    response.choices[0].finish_reason = "stop"
    response.usage = MagicMock()
    response.usage.prompt_tokens = 1
    response.usage.completion_tokens = 1
    response.id = "test-id"
    with patch(_ACOMPLETION, new_callable=AsyncMock, return_value=response):
        yield


@contextmanager
def _refuses() -> Iterator[None]:
    """Stand in for a provider that rejects the probe.

    An authentication failure rather than a transport error, because it is
    classified non-retryable: a retryable one would spend real backoff.
    """
    with patch(
        _ACOMPLETION,
        new_callable=AsyncMock,
        side_effect=AuthenticationError("Invalid key"),
    ):
        yield


@pytest.mark.unit
class TestConnectionTestRecordsHealth:
    async def test_the_outcome_is_recorded(
        self,
        service: ProviderManagementService,
    ) -> None:
        request = make_create_request()
        await service.create_provider(request)
        requester = _requester()
        service.set_probe_requester(requester)

        with _answers():
            response = await service.test_connection(request.name, ConnTestRequest())

        assert response.success is True
        requester.record_outcome.assert_awaited_once()
        call = requester.record_outcome.await_args
        assert call.args[0] == request.name
        outcome = call.args[1]
        assert outcome.success is True
        # The model the test actually called, so the verdict is about a pair
        # rather than about the whole connection.
        assert outcome.model == response.model_tested

    async def test_a_failing_test_is_recorded_as_a_failure(
        self,
        service: ProviderManagementService,
    ) -> None:
        # A provider that cannot answer must not read as healthy just because
        # something finally called it.
        request = make_create_request()
        await service.create_provider(request)
        requester = _requester()
        service.set_probe_requester(requester)

        with _refuses():
            response = await service.test_connection(request.name, ConnTestRequest())

        assert response.success is False
        requester.record_outcome.assert_awaited_once()
        assert requester.record_outcome.await_args.args[1].success is False

    async def test_a_failure_records_no_invented_latency(
        self,
        service: ProviderManagementService,
    ) -> None:
        # A call that never reached the wire has no round trip to report, and
        # inventing one would drag the average it is folded into.
        request = make_create_request()
        await service.create_provider(request)
        requester = _requester()
        service.set_probe_requester(requester)

        with _refuses():
            _ = await service.test_connection(request.name, ConnTestRequest())

        assert requester.record_outcome.await_args.args[1].response_time_ms == 0.0

    async def test_a_provider_with_no_models_records_nothing(
        self,
        service: ProviderManagementService,
    ) -> None:
        # No call was made, so there is no call outcome. Recording one would
        # push a configuration gap into the reachability aggregate, which is
        # meant to describe whether the provider answers.
        request = make_create_request(models=())
        await service.create_provider(request)
        requester = _requester()
        service.set_probe_requester(requester)

        response = await service.test_connection(request.name, ConnTestRequest())

        assert response.success is False
        requester.record_outcome.assert_not_awaited()

    async def test_a_recorder_failure_does_not_fail_the_test(
        self,
        service: ProviderManagementService,
    ) -> None:
        request = make_create_request()
        await service.create_provider(request)
        requester = _requester()
        requester.record_outcome.side_effect = RuntimeError("tracker exploded")
        service.set_probe_requester(requester)

        with _answers():
            response = await service.test_connection(request.name, ConnTestRequest())

        assert response.success is True
        assert response.model_tested is not None

    async def test_cancellation_is_not_swallowed(
        self,
        service: ProviderManagementService,
    ) -> None:
        request = make_create_request()
        await service.create_provider(request)
        requester = _requester()
        requester.record_outcome.side_effect = asyncio.CancelledError()
        service.set_probe_requester(requester)

        with _answers(), pytest.raises(asyncio.CancelledError):
            _ = await service.test_connection(request.name, ConnTestRequest())

    async def test_it_works_without_a_wired_requester(
        self,
        service: ProviderManagementService,
    ) -> None:
        """The prober is wired on startup, after this service is constructed."""
        request = make_create_request()
        await service.create_provider(request)

        with _answers():
            response = await service.test_connection(request.name, ConnTestRequest())

        assert response.success is True
        assert response.model_tested is not None
