# module-kind: code
"""Config and injected dependencies for the OpenHands execution loop.

``OpenHandsLoopConfig`` is the frozen, settings-driven behaviour (token
lifetime, turn ceiling). ``OpenHandsLoopDeps`` carries the collaborators
that cannot live in frozen config: the conversation factory (the real SDK
runtime, or a fake), the shared gateway signer used to mint per-run
bearers, the gateway / MCP endpoint URLs, and the clock.
"""

from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.clock import Clock
from synthorg.engine.openhands.conversation import ConversationFactory
from synthorg.llm.gateway_token import GatewaySigner

_DEFAULT_MAX_TURNS: Final[int] = 50
_DEFAULT_TOKEN_TTL_SECONDS: Final[int] = 3600


class OpenHandsLoopConfig(BaseModel):
    """Frozen, settings-driven behaviour for the OpenHands loop."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_turns: int = Field(
        default=_DEFAULT_MAX_TURNS, gt=0, description="Turn ceiling for a run"
    )
    token_ttl_seconds: int = Field(
        default=_DEFAULT_TOKEN_TTL_SECONDS,
        gt=0,
        description="Lifetime of a minted per-run gateway bearer",
    )


@dataclass(frozen=True)
class OpenHandsLoopDeps:
    """Injected collaborators for the OpenHands loop.

    ``signer`` must be the same instance (or a same-secret peer) the gateway
    verifies with, so a token minted here is accepted at the gateway. ``None``
    dependencies leave the loop unavailable (it fails loud on execute).
    """

    build_conversation: ConversationFactory
    signer: GatewaySigner
    gateway_base_url: str
    mcp_base_url: str
    clock: Clock
