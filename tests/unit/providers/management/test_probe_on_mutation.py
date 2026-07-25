"""Provider mutations trigger an immediate health probe.

Health is derived from recorded call outcomes, so a provider with none reports
UNKNOWN -- which the dashboard renders identically to a provider that is
genuinely unreachable. Probing on write is what stops a freshly configured
provider sitting in that state until the next periodic sweep, up to a full
probe interval later.
"""

from typing import Any

import pytest

from synthorg.api.dto_providers import UpdateProviderRequest
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.probe_protocol import ProviderProbeRequester
from tests._shared import mock_of

from .conftest import make_create_request

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]


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
