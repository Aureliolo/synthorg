"""Provider mutations trigger an immediate health probe.

Health is derived from recorded call outcomes, so a provider with none reports
UNKNOWN -- which the dashboard renders identically to a provider that is
genuinely unreachable. Probing on write is what stops a freshly configured
provider sitting in that state until the next periodic sweep, up to a full
probe interval later.
"""

import asyncio
from typing import Any

import pytest

from synthorg.api.dto_providers import UpdateProviderRequest
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.probe_protocol import ProviderProbeRequester
from synthorg.settings.service import SettingsService
from tests._shared import mock_of

from .conftest import make_create_request

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]

#: Outer bound for the hanging-probe test; the setting under test is far lower.
_HANG_GUARD_SECONDS = 5.0


def _requester() -> _Configured:
    """Build a probe requester double whose probe is a no-op.

    ``probe_provider`` is left to the autospec so its child mock stays bound
    to the protocol signature; overriding it with a separately-spec'd
    ``AsyncMock`` would re-introduce the unbound ``self`` parameter and make
    every call assertion fail to match.

    Returns:
        An autospec'd :class:`ProviderProbeRequester` substitute.
    """
    return mock_of[ProviderProbeRequester]()


@pytest.mark.unit
class TestProbeOnMutation:
    async def test_create_probes_the_new_provider(
        self,
        service: ProviderManagementService,
    ) -> None:
        requester = _requester()
        service.set_probe_requester(requester)

        request = make_create_request()
        await service.create_provider(request)

        requester.probe_provider.assert_awaited_once_with(request.name)

    async def test_update_probes_the_changed_provider(
        self,
        service: ProviderManagementService,
    ) -> None:
        request = make_create_request()
        await service.create_provider(request)
        # Wired only now, so the create above cannot satisfy the assertion:
        # a re-pointed endpoint must be re-probed on its own.
        requester = _requester()
        service.set_probe_requester(requester)

        await service.update_provider(
            request.name,
            UpdateProviderRequest(base_url="http://localhost:9999"),
        )

        requester.probe_provider.assert_awaited_once_with(request.name)

    async def test_probe_failure_does_not_fail_the_mutation(
        self,
        service: ProviderManagementService,
    ) -> None:
        """The provider is already persisted, so a probe error must not raise."""
        requester = _requester()
        requester.probe_provider.side_effect = RuntimeError("probe exploded")
        service.set_probe_requester(requester)

        result = await service.create_provider(make_create_request())

        assert result.driver == "litellm"

    async def test_create_succeeds_without_a_wired_requester(
        self,
        service: ProviderManagementService,
    ) -> None:
        """The prober is wired on startup, after this service is constructed."""
        result = await service.create_provider(make_create_request())

        assert result.driver == "litellm"

    async def test_a_hanging_probe_does_not_hold_the_mutation_open(
        self,
        service: ProviderManagementService,
        settings_service: SettingsService,
    ) -> None:
        """A mistyped host must not stall the save for the probe's own timeout.

        The probe is awaited on the request, so its budget is what bounds the
        POST; exceeding it costs only the immediate health reading.
        """
        # The setting's own floor, so this also pins that an operator cannot
        # configure the budget away entirely.
        await settings_service.set("api", "post_mutation_probe_timeout_seconds", "0.1")
        requester = _requester()

        async def _never_returns(name: str) -> None:
            del name
            await asyncio.Event().wait()

        requester.probe_provider.side_effect = _never_returns
        service.set_probe_requester(requester)

        # Comfortably above the 0.1s budget, far below any hang: this fails
        # loudly if the timeout is ever removed rather than waiting it out.
        result = await asyncio.wait_for(
            service.create_provider(make_create_request()),
            timeout=_HANG_GUARD_SECONDS,
        )

        assert result.driver == "litellm"

    async def test_cancellation_is_not_swallowed_by_the_best_effort_handler(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Shutdown must propagate, unlike an ordinary probe failure.

        The handler that keeps a failed probe from failing the mutation
        catches broadly, so without an explicit re-raise it would absorb the
        cancellation that stops the service.
        """
        requester = _requester()
        requester.probe_provider.side_effect = asyncio.CancelledError()
        service.set_probe_requester(requester)

        with pytest.raises(asyncio.CancelledError):
            await service.create_provider(make_create_request())
