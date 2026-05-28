"""A2A peer connection type registration.

Provides the ``A2A_PEER`` connection type's webhook signature
verifier registration for the unified webhook receiver.
"""

from typing import Final

from synthorg.a2a.push_verifier import A2APushVerifier
from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.webhooks.verifiers.protocol import (
    SignatureVerifier,
)

_DEFAULT_CLOCK_SKEW_SECONDS: Final[int] = 300


def get_a2a_push_verifier(
    clock_skew_seconds: int = _DEFAULT_CLOCK_SKEW_SECONDS,
) -> SignatureVerifier:
    """Create an A2A push notification verifier.

    Args:
        clock_skew_seconds: Clock skew tolerance (must be >= 0).

    Returns:
        A ``SignatureVerifier`` instance for A2A push events.

    Raises:
        ValueError: If clock_skew_seconds is negative.
    """
    if clock_skew_seconds < 0:
        msg = "clock_skew_seconds must be >= 0"
        raise ValueError(msg)
    return A2APushVerifier(clock_skew_seconds=clock_skew_seconds)


# Connection type constant for external reference
A2A_PEER_TYPE = ConnectionType.A2A_PEER
