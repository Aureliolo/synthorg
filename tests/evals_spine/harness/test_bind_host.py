# module-kind: tests
"""Resolving the interface the recording host listens on.

The daemon is never contacted: these drive the resolution against inspection
payloads the daemon can genuinely return, because what matters is which entry
of a dual-stack bridge is picked, not that aiodocker can reach a socket.
"""

import sys
from types import SimpleNamespace, TracebackType
from typing import Self

import pytest

from evals.errors import HarnessBindHostUnresolvedError
from evals.harness.bind_host import (
    LOOPBACK_BIND_HOST,
    resolve_bind_host,
)

pytestmark = pytest.mark.unit

_IPV4_GATEWAY = "172.17.0.1"
_IPV6_GATEWAY = "fe80::1"

#: What the injected unreachable-daemon failure carries.
_DAEMON_DOWN = "daemon socket is not there"


class _FakeNetwork:
    """A network whose inspection returns a preset payload."""

    def __init__(self, detail: object) -> None:
        self._detail = detail

    async def show(self) -> object:
        """Return the preset inspection payload.

        Returns:
            The payload the daemon would have answered with.
        """
        return self._detail


class _FakeNetworks:
    def __init__(self, detail: object) -> None:
        self._detail = detail

    async def get(self, name: str) -> _FakeNetwork:
        """Return the fake network regardless of name.

        Returns:
            The single fake network.
        """
        del name
        return _FakeNetwork(self._detail)


class _FakeDocker:
    """Stands in for ``aiodocker.Docker`` as an async context manager."""

    def __init__(self, detail: object) -> None:
        self.networks = _FakeNetworks(detail)

    async def __aenter__(self) -> Self:
        """Enter the client scope.

        Returns:
            This client.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Leave the client scope."""
        del exc_type, exc, traceback


def _bind_daemon(monkeypatch: pytest.MonkeyPatch, detail: object) -> None:
    """Make the bridge inspection answer with *detail*, and force the Linux path.

    Args:
        monkeypatch: The patching fixture.
        detail: The payload ``network.show()`` should return.
    """
    monkeypatch.setattr(
        "evals.harness.bind_host.aiodocker",
        SimpleNamespace(Docker=lambda: _FakeDocker(detail)),
    )
    # The desktop platforms short-circuit to loopback before the daemon is
    # consulted at all, so the bridge path is only reachable off them.
    monkeypatch.setattr(sys, "platform", "linux")


def _ipam(*gateways: str) -> dict[str, object]:
    """Build a network inspection declaring *gateways* in order.

    Returns:
        The inspection payload.
    """
    return {"IPAM": {"Config": [{"Gateway": gateway} for gateway in gateways]}}


async def test_an_explicit_choice_is_taken_verbatim() -> None:
    assert await resolve_bind_host("10.1.2.3") == "10.1.2.3"


async def test_an_ipv6_entry_ahead_of_the_ipv4_one_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A dual-stack bridge declares both, in no guaranteed order, and every URL
    # built from the result interpolates the address without brackets.
    _bind_daemon(monkeypatch, _ipam(_IPV6_GATEWAY, _IPV4_GATEWAY))

    assert await resolve_bind_host(None) == _IPV4_GATEWAY


async def test_an_ipv4_gateway_is_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_daemon(monkeypatch, _ipam(_IPV4_GATEWAY))

    assert await resolve_bind_host(None) == _IPV4_GATEWAY


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param(_ipam(_IPV6_GATEWAY), id="ipv6-only"),
        pytest.param(_ipam(""), id="blank"),
        pytest.param(_ipam("not-an-address"), id="unparseable"),
        pytest.param({"IPAM": {"Config": []}}, id="no-entries"),
        pytest.param({"IPAM": "unexpected"}, id="unknown-shape"),
    ],
)
async def test_no_usable_gateway_fails_loud(
    monkeypatch: pytest.MonkeyPatch, detail: object
) -> None:
    # Widening to every interface is the operator's call, so an unresolvable
    # bridge asks for --bind-host rather than binding 0.0.0.0 quietly.
    _bind_daemon(monkeypatch, detail)

    with pytest.raises(HarnessBindHostUnresolvedError):
        await resolve_bind_host(None)


async def test_an_unreachable_daemon_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _refuse() -> _FakeDocker:
        raise OSError(_DAEMON_DOWN)

    monkeypatch.setattr(
        "evals.harness.bind_host.aiodocker", SimpleNamespace(Docker=_refuse)
    )
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(HarnessBindHostUnresolvedError):
        await resolve_bind_host(None)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
async def test_desktop_platforms_resolve_to_loopback(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    # The daemon runs in a VM that forwards the container-facing alias to the
    # host's loopback, so loopback is both sufficient and the narrowest choice.
    monkeypatch.setattr(sys, "platform", platform)

    assert await resolve_bind_host(None) == LOOPBACK_BIND_HOST
