"""A2A feature state slice.

Holds the agent-to-agent gateway collaborators: the Agent Card builder,
the peer registry, and the outbound A2A client. All ``None`` until the
A2A gateway is wired at boot (gated on the gateway being enabled); the
``.well-known`` handlers and capabilities controller surface 503 when unset.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.a2a.agent_card import AgentCardBuilder
from synthorg.a2a.client import A2AClient
from synthorg.a2a.peer_registry import PeerRegistry


class A2aStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the A2A feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    card_builder: AgentCardBuilder | None = None
    client: A2AClient | None = None
    peer_registry: PeerRegistry | None = None
