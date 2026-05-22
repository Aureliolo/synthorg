"""Pluggable steering directive for cockpit hint/redirect interventions.

PAUSE and KILL reuse the task lifecycle seams at the controller; this
module covers HINT and REDIRECT. The safe default delivers them as an
``INFO_REQUEST`` interrupt the running agent consumes at its next safe
turn boundary, producing a visible queued artefact rather than a silent
no-op. Future strategies can swap in deeper in-loop propagation
behind this same protocol without controller or wiring churn.
"""

from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from synthorg.communication.event_stream.interrupt import (
    Interrupt,
    InterruptStore,
    InterruptType,
)
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import InterventionKind
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger(__name__)

#: Advisory expiry stamped on a steering interrupt; the engine supplies
#: the real wait timeout. A named constant keeps the magic-number gate
#: satisfied while documenting the default operator-hint lifetime.
DEFAULT_STEERING_TIMEOUT_SECONDS: Final[float] = 600.0

#: Intervention kinds the steering directive is responsible for; PAUSE
#: and KILL are routed to the task lifecycle seams at the controller.
_STEERABLE_KINDS: Final[frozenset[InterventionKind]] = frozenset(
    {InterventionKind.HINT, InterventionKind.REDIRECT},
)


class SteeringOutcome(BaseModel):
    """Result of applying a steering directive."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: InterventionKind = Field(description="Intervention kind applied")
    applied: bool = Field(description="Whether the directive was delivered")
    artifact_id: NotBlankStr | None = Field(
        default=None,
        description="Interrupt id the directive produced, when applied",
    )
    detail: str = Field(description="Human-readable outcome description")


@runtime_checkable
class SteeringDirective(Protocol):
    """Delivers a mid-flight hint/redirect to a running agent."""

    async def steer(
        self,
        *,
        kind: InterventionKind,
        execution_id: str,
        agent_id: str,
        details: Mapping[str, object],
    ) -> SteeringOutcome:
        """Apply a steering intervention; return its outcome."""
        ...


class SafeDefaultSteeringDirective:
    """Default directive: queue an ``INFO_REQUEST`` interrupt.

    Both HINT and REDIRECT post an interrupt carrying the operator's
    text, which the running agent consumes at its next safe turn
    boundary. This is best-effort: the agent adopts the directive when
    it next checks for interrupts, with no in-flight state mutation.
    """

    def __init__(
        self,
        interrupt_store: InterruptStore,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._interrupt_store = interrupt_store
        self._clock = clock or SystemClock()

    async def steer(
        self,
        *,
        kind: InterventionKind,
        execution_id: str,
        agent_id: str,
        details: Mapping[str, object],
    ) -> SteeringOutcome:
        """Queue a hint/redirect interrupt for the running agent."""
        if kind not in _STEERABLE_KINDS:
            return SteeringOutcome(
                kind=kind,
                applied=False,
                detail=f"{kind.value} is not handled by the steering directive",
            )
        text = str(details.get("text", "")).strip()
        if not text:
            return SteeringOutcome(
                kind=kind,
                applied=False,
                detail="no directive text supplied",
            )
        # SEC-1: the operator types this text into the mission-control
        # UI and it flows into the running agent's interrupt queue, so
        # it is untrusted content from the model's perspective and must
        # be wrapped before being persisted to the queued artefact.
        safe_text = wrap_untrusted(TAG_TASK_DATA, text)
        interrupt = Interrupt(
            id=NotBlankStr(str(uuid4())),
            type=InterruptType.INFO_REQUEST,
            session_id=NotBlankStr(execution_id),
            agent_id=NotBlankStr(agent_id),
            created_at=self._clock.now(),
            timeout_seconds=DEFAULT_STEERING_TIMEOUT_SECONDS,
            question=NotBlankStr(safe_text),
            context_snippet=NotBlankStr(f"Operator {kind.value} via mission control"),
        )
        await self._interrupt_store.create(interrupt)
        return SteeringOutcome(
            kind=kind,
            applied=True,
            artifact_id=interrupt.id,
            detail="queued, awaiting the agent's next safe turn boundary",
        )


def build_steering_directive(
    interrupt_store: InterruptStore,
    *,
    strategy: str = "safe_default",
    clock: Clock | None = None,
) -> SteeringDirective:
    """Select the configured steering directive implementation."""
    if strategy != "safe_default":
        msg = f"Unknown steering directive strategy: {strategy!r}"
        raise ValueError(msg)
    return SafeDefaultSteeringDirective(interrupt_store, clock=clock)
