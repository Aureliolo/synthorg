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
_DEFAULT_IDLE_TIMEOUT_SECONDS: Final[float] = 600.0


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
    idle_timeout_seconds: float = Field(
        default=_DEFAULT_IDLE_TIMEOUT_SECONDS,
        gt=0,
        description="Max seconds to wait for the next container event before "
        "treating the run as hung",
    )


@dataclass(frozen=True)
class OpenHandsLoopDeps:
    """Injected collaborators for the OpenHands loop.

    ``build_conversation`` is the conversation factory: in production the
    container runtime bound to the egress-pinned sandbox, in tests a scripted
    fake. ``signer`` must be the same instance the gateway verifies with, so a
    token minted here is accepted at the gateway. The URLs must be non-blank
    (validated here at construction); the wiring returns ``None`` deps rather
    than constructing with blank URLs, so an unwired boundary leaves the loop
    unavailable rather than failing at execute.
    """

    build_conversation: ConversationFactory
    signer: GatewaySigner
    gateway_base_url: str
    mcp_base_url: str
    clock: Clock

    def __post_init__(self) -> None:
        """Reject blank gateway / MCP URLs at construction.

        Raises:
            ValueError: When either boundary URL is blank; the fail point is
                construction, not a later ``execute`` call.
        """
        if not self.gateway_base_url or not self.gateway_base_url.strip():
            msg = "OpenHandsLoopDeps.gateway_base_url must be non-blank"
            raise ValueError(msg)
        if not self.mcp_base_url or not self.mcp_base_url.strip():
            msg = "OpenHandsLoopDeps.mcp_base_url must be non-blank"
            raise ValueError(msg)
