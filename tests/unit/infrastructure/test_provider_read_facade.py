"""Direct tests for ``ProviderReadService``'s registry and health reads.

This facade answers the ``synthorg_providers_get`` and
``synthorg_providers_get_health`` MCP tools, and both asked their collaborator
for a method it has never had, so every call returned a capability gap and the
tools reported nothing at all. Nothing failed, because nothing exercised them:
the MCP handler tests stub the facade rather than the registry and tracker
behind it. These call the real ones, which is the only way this class of
defect surfaces.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.infrastructure.services._read_facades import ProviderReadService
from synthorg.providers.health import ProviderHealthRecord
from synthorg.providers.health_tracker import ProviderHealthTracker
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.registry import ProviderRegistry
from tests._shared import mock_of

pytestmark = pytest.mark.unit

#: Inside the tracker's 24h window relative to a real ``now``. The facade
#: takes no reference time, so the tracker reads the wall clock and a record
#: stamped in the future is filtered straight back out.
_RECORDED_AT = datetime.now(UTC) - timedelta(minutes=1)


def _service(
    *,
    registry: ProviderRegistry | None = None,
    tracker: ProviderHealthTracker | None = None,
) -> ProviderReadService:
    """Build the facade over doubles for the collaborators it reads.

    Returns:
        The facade, with a real tracker so the health answers are real.
    """
    return ProviderReadService(
        registry=registry if registry is not None else mock_of[ProviderRegistry](),
        health=tracker if tracker is not None else ProviderHealthTracker(),
        management=mock_of[ProviderManagementService](),
    )


async def _tracker_with(provider_name: str) -> ProviderHealthTracker:
    """A tracker holding one successful outcome for *provider_name*.

    Returns:
        The tracker.
    """
    tracker = ProviderHealthTracker()
    await tracker.record(
        ProviderHealthRecord(
            provider_name=provider_name,
            timestamp=_RECORDED_AT,
            success=True,
            response_time_ms=100.0,
        )
    )
    return tracker


def _registry_with(*names: str) -> ProviderRegistry:
    """A registry double exposing the real surface the facade reads.

    Returns:
        The double, answering ``list_providers`` and ``get`` for *names*.
    """
    drivers = {name: object() for name in names}
    registry: ProviderRegistry = mock_of[ProviderRegistry](
        list_providers=lambda: tuple(sorted(drivers)),
        get=drivers.__getitem__,
    )
    return registry


class TestGetProvider:
    async def test_a_registered_provider_is_returned(self) -> None:
        service = _service(registry=_registry_with("test-provider"))

        assert await service.get_provider(NotBlankStr("test-provider")) is not None

    async def test_an_unregistered_provider_is_none(self) -> None:
        # ``ProviderRegistry.get`` raises for an unknown name, so membership is
        # checked first; letting the raise through would make an absent
        # provider indistinguishable from a broken registry at the MCP edge.
        service = _service(registry=_registry_with("test-provider"))

        assert await service.get_provider(NotBlankStr("nope")) is None


class TestGetHealth:
    async def test_a_registered_provider_reports_its_summary(self) -> None:
        tracker = await _tracker_with("test-provider")

        result = await _service(
            registry=_registry_with("test-provider"), tracker=tracker
        ).get_health(NotBlankStr("test-provider"))

        assert set(result) == {"test-provider"}

    async def test_an_unregistered_provider_is_not_found(self) -> None:
        # The tracker answers for any name at all, reporting UNKNOWN for one it
        # holds no records under, so without the registry check a typo and a
        # configured provider nothing has called yet read identically.
        service = _service(registry=_registry_with("test-provider"))

        with pytest.raises(NotFoundError):
            _ = await service.get_health(NotBlankStr("nope"))

    async def test_no_provider_id_reports_every_tracked_provider(self) -> None:
        tracker = await _tracker_with("test-provider")

        result = await _service(tracker=tracker).get_health()

        assert set(result) == {"test-provider"}

    async def test_a_tracker_without_the_capability_says_so(self) -> None:
        # The capability probe is what turns a missing method into a typed gap
        # rather than an AttributeError escaping to the MCP boundary.
        service = ProviderReadService(
            registry=mock_of[ProviderRegistry](),
            health=object(),  # type: ignore[arg-type]
            management=mock_of[ProviderManagementService](),
        )

        with pytest.raises(CapabilityNotSupportedError):
            _ = await service.get_health()
