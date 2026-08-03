# module-kind: code
"""Resolving the interface the recording host listens on.

The sandbox dials the recorder back through the sidecar's
``host.docker.internal:host-gateway`` alias, so the listener has to sit on an
address that alias resolves to. Binding every interface always satisfies that,
which is why it is tempting and why it is refused here: the recorder serves the
*whole* application, and ``/auth/setup`` is force-excluded from authentication
(:mod:`synthorg.api.middleware_factory` keeps it reachable so an operator can
never lock themselves out) and hands a CEO session to the first caller while no
CEO exists. The recorder seeds one before it serves, so that door is shut either
way, but a run still has no reason to offer its login, health and docs surfaces
to the network.

Which address is narrow enough depends on how the daemon bridges to the host:

- Docker Desktop runs the daemon in a VM and forwards ``host.docker.internal``
  to the host's loopback, so loopback is both sufficient and reachable.
- Docker Engine on Linux resolves ``host-gateway`` to the bridge network's
  gateway address, which is a real address *on* the host, so that one address is
  enough and every other interface is surplus.

Anything else fails loud and asks for ``--bind-host`` rather than guessing wide.
"""

import ipaddress
import sys
from typing import Final

import aiodocker

from evals.errors import LoopAbBindHostUnresolvedError
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_LOOP_AB_BIND_HOST_RESOLVED

logger = get_logger(__name__)

#: Docker Desktop forwards the container-facing alias here.
LOOPBACK_BIND_HOST: Final[str] = "127.0.0.1"

#: The daemon's default bridge network, whose gateway is the address a Linux
#: ``host-gateway`` alias resolves to.
_BRIDGE_NETWORK: Final[str] = "bridge"

#: Platforms where the daemon runs in a VM that forwards to host loopback.
_DESKTOP_PLATFORMS: Final[frozenset[str]] = frozenset({"darwin", "win32"})

#: The address family the resolved gateway must belong to.
_IPV4: Final[int] = 4


async def resolve_bind_host(configured: str | None) -> str:
    """Decide which interface the recording host should listen on.

    Args:
        configured: An explicit operator choice, or ``None`` to resolve one.

    Returns:
        The address to bind.

    Raises:
        LoopAbBindHostUnresolvedError: No narrow address could be resolved.
            Widening to every interface is the operator's call, not this
            function's, so it is surfaced rather than assumed.
    """
    if configured is not None:
        return configured
    if sys.platform in _DESKTOP_PLATFORMS:
        logger.info(
            EVALS_LOOP_AB_BIND_HOST_RESOLVED,
            bind_host=LOOPBACK_BIND_HOST,
            source="desktop-loopback",
        )
        return LOOPBACK_BIND_HOST
    gateway = await _bridge_gateway()
    logger.info(
        EVALS_LOOP_AB_BIND_HOST_RESOLVED, bind_host=gateway, source="bridge-gateway"
    )
    return gateway


async def _bridge_gateway() -> str:
    """Read the default bridge network's gateway address from the daemon.

    Returns:
        The gateway address.

    Raises:
        LoopAbBindHostUnresolvedError: The daemon is unreachable, or its bridge
            network declares no IPv4 gateway.
    """
    try:
        async with aiodocker.Docker() as client:
            network = await client.networks.get(_BRIDGE_NETWORK)
            detail = await network.show()
    except Exception as exc:
        msg = (
            "could not read the Docker bridge gateway to bind to; "
            "pass --bind-host explicitly"
        )
        raise LoopAbBindHostUnresolvedError(msg) from exc
    for entry in _ipam_config(detail):
        gateway = entry.get("Gateway")
        if isinstance(gateway, str) and _is_ipv4(gateway):
            return gateway
    msg = (
        "the Docker bridge network declares no IPv4 gateway address; "
        "pass --bind-host explicitly"
    )
    raise LoopAbBindHostUnresolvedError(msg)


def _is_ipv4(address: str) -> bool:
    """Decide whether *address* is one this host can bind and dial unbracketed.

    A dual-stack bridge declares an IPv6 config entry alongside the IPv4 one,
    in either order, and every URL built from the result interpolates the
    address without brackets, so an IPv6 gateway would produce an unparseable
    authority rather than a listener the sandbox can reach.

    Args:
        address: The candidate gateway address.

    Returns:
        Whether it parses as IPv4.
    """
    try:
        return ipaddress.ip_address(address).version == _IPV4
    except ValueError:
        return False


def _ipam_config(detail: object) -> tuple[dict[str, object], ...]:
    """Pull the IPAM config entries out of a network inspection.

    Args:
        detail: The daemon's network detail payload.

    Returns:
        The IPAM config entries, empty when the payload is not the expected
        shape (a daemon that answered in a shape we do not know is the same
        situation as one that answered with no gateway).
    """
    if not isinstance(detail, dict):
        return ()
    ipam = detail.get("IPAM")
    if not isinstance(ipam, dict):
        return ()
    config = ipam.get("Config")
    if not isinstance(config, list):
        return ()
    return tuple(entry for entry in config if isinstance(entry, dict))


__all__ = ["LOOPBACK_BIND_HOST", "resolve_bind_host"]
