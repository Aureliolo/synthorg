"""Interrupt models and in-memory interrupt store.

Defines the ``Interrupt`` and ``InterruptResolution`` models for the
HITL interrupt/resume protocol, plus the ``InterruptStore`` that holds
pending interrupts with async resolution signaling.
"""

import asyncio
import copy
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.event_stream import (
    EVENT_STREAM_INTERRUPT_CREATED,
    EVENT_STREAM_INTERRUPT_DUPLICATE,
    EVENT_STREAM_INTERRUPT_EXPIRED,
    EVENT_STREAM_INTERRUPT_NOT_FOUND,
    EVENT_STREAM_INTERRUPT_RESUMED,
    EVENT_STREAM_INVALID_RESUME_PAYLOAD,
)

logger = get_logger(__name__)


class InterruptType(StrEnum):
    """Type of blocking interrupt.

    Members:
        TOOL_APPROVAL: Approval gate parked execution for HITL review.
        INFO_REQUEST: Agent needs clarification mid-task.
    """

    TOOL_APPROVAL = "tool_approval"
    INFO_REQUEST = "info_request"


@dataclass(frozen=True, slots=True)
class _InterruptFieldRule:
    """Per-``InterruptType`` required-field rule.

    Declares, for one interrupt type, the field that must be present on
    the :class:`Interrupt` model and the field that must be present on
    the resume payload. Both the model validator and the API resume
    guard consult the single :data:`INTERRUPT_FIELD_RULES` table so the
    per-type knowledge lives in exactly one place (ADR-0002: this site
    uses a frozen data table, not ``StrategyRegistry``, because the
    per-type knowledge is data, not behaviour dispatch).
    """

    interrupt_field: str
    resume_field: str


INTERRUPT_FIELD_RULES: Final[Mapping[InterruptType, _InterruptFieldRule]] = (
    MappingProxyType(
        {
            InterruptType.TOOL_APPROVAL: _InterruptFieldRule(
                interrupt_field="tool_name",
                resume_field="decision",
            ),
            InterruptType.INFO_REQUEST: _InterruptFieldRule(
                interrupt_field="question",
                resume_field="response",
            ),
        },
    )
)


class ResumeDecision(StrEnum):
    """Human decision for a tool approval interrupt.

    Members:
        APPROVE: Allow the tool execution to proceed.
        REJECT: Deny the tool execution.
        REVISE: Request changes before re-attempting.
    """

    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"


class Interrupt(BaseModel):
    """A blocking interrupt awaiting human resolution.

    Attributes:
        id: Unique interrupt identifier.
        type: Interrupt classification.
        session_id: Session this interrupt belongs to.
        agent_id: Agent that triggered the interrupt.
        created_at: When the interrupt was created.
        timeout_seconds: Seconds before the interrupt auto-expires.
        tool_name: Tool that triggered the interrupt (TOOL_APPROVAL).
        tool_args: Arguments to the tool (TOOL_APPROVAL).
        evidence_package_id: Associated evidence package (TOOL_APPROVAL).
        question: Clarification question (INFO_REQUEST).
        context_snippet: Context for the question (INFO_REQUEST).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique interrupt identifier")
    type: InterruptType = Field(description="Interrupt classification")
    session_id: NotBlankStr = Field(description="Owning session")
    agent_id: NotBlankStr = Field(description="Triggering agent")
    created_at: AwareDatetime = Field(description="Creation timestamp")
    timeout_seconds: float = Field(
        gt=0,
        description=(
            "Suggested expiry timeout in seconds.  Advisory: the caller"
            " of InterruptStore.wait_for_resolution() supplies the actual"
            " timeout; this field is informational for UI display."
        ),
    )
    tool_name: NotBlankStr | None = Field(
        default=None,
        description="Tool name (TOOL_APPROVAL only)",
    )
    tool_args: dict[str, object] | None = Field(
        default=None,
        description="Tool arguments (TOOL_APPROVAL only)",
    )
    evidence_package_id: NotBlankStr | None = Field(
        default=None,
        description="Evidence package ID (TOOL_APPROVAL only)",
    )
    question: NotBlankStr | None = Field(
        default=None,
        description="Clarification question (INFO_REQUEST only)",
    )
    context_snippet: NotBlankStr | None = Field(
        default=None,
        description="Context for the question (INFO_REQUEST only)",
    )

    @model_validator(mode="after")
    def _validate_type_fields(self) -> Self:
        """Enforce required fields per interrupt type via the rule table."""
        rule = INTERRUPT_FIELD_RULES.get(self.type)
        if rule is not None and getattr(self, rule.interrupt_field) is None:
            msg = f"{rule.interrupt_field} is required for {self.type.name} interrupts"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _deep_copy_tool_args(self) -> Self:
        """Deep-copy tool_args to prevent external mutation."""
        if self.tool_args is not None:
            object.__setattr__(
                self,
                "tool_args",
                copy.deepcopy(self.tool_args),
            )
        return self


class InterruptResolution(BaseModel):
    """Human response to an interrupt.

    Attributes:
        interrupt_id: The interrupt being resolved.
        decision: Approval decision (TOOL_APPROVAL interrupts).
        feedback: Optional feedback text (TOOL_APPROVAL interrupts).
        response: Clarification response (INFO_REQUEST interrupts).
        resolved_at: When the resolution was provided.
        resolved_by: Who provided the resolution.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    interrupt_id: NotBlankStr = Field(
        description="Interrupt being resolved",
    )
    decision: ResumeDecision | None = Field(
        default=None,
        description="Approval decision (TOOL_APPROVAL only)",
    )
    feedback: NotBlankStr | None = Field(
        default=None,
        description="Feedback text (TOOL_APPROVAL only)",
    )
    response: NotBlankStr | None = Field(
        default=None,
        description="Clarification response (INFO_REQUEST only)",
    )
    resolved_at: AwareDatetime = Field(description="Resolution timestamp")
    resolved_by: NotBlankStr = Field(description="Resolver identity")

    @model_validator(mode="after")
    def _validate_payload(self) -> Self:
        """Ensure at least one semantic field is provided."""
        if self.decision is None and self.response is None:
            msg = "decision or response is required"
            raise ValueError(msg)
        return self


class InterruptStore:
    """In-memory store for pending interrupts with async resolution.

    Each interrupt gets an ``asyncio.Event`` that is set when the
    interrupt is resolved.  Callers can await resolution via
    :meth:`wait_for_resolution`.

    .. warning::

       This implementation is **not persistent**.  On server restart,
       all pending interrupts and their resolutions are lost.  For
       production deployments that require durability, implement a
       persistent backend (e.g. SQL-backed) behind the same interface.
    """

    __slots__ = ("_events", "_lock", "_pending", "_results")

    def __init__(self) -> None:
        self._pending: dict[str, Interrupt] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._results: dict[str, InterruptResolution] = {}
        self._lock = asyncio.Lock()

    async def create(self, interrupt: Interrupt) -> None:
        """Register a new pending interrupt.

        The existence check and the dict writes execute under
        ``self._lock`` so two concurrent ``create()`` calls with the
        same ``interrupt.id`` cannot both pass the existence guard.

        Args:
            interrupt: The interrupt to register.

        Raises:
            ValueError: If an interrupt with the same ID already exists.
        """
        async with self._lock:
            if (
                interrupt.id in self._pending
                or interrupt.id in self._events
                or interrupt.id in self._results
            ):
                msg = f"Interrupt {interrupt.id!r} already exists"
                # Log before raise per the repo's error-path observability
                # rule. Includes the interrupt id + which dict already had
                # it so concurrent-create races are diagnosable from the
                # event stream alone.
                logger.warning(
                    EVENT_STREAM_INTERRUPT_DUPLICATE,
                    interrupt_id=interrupt.id,
                    in_pending=interrupt.id in self._pending,
                    in_events=interrupt.id in self._events,
                    in_results=interrupt.id in self._results,
                )
                raise ValueError(msg)
            self._pending[interrupt.id] = copy.deepcopy(interrupt)
            self._events[interrupt.id] = asyncio.Event()
            logger.info(
                EVENT_STREAM_INTERRUPT_CREATED,
                interrupt_id=interrupt.id,
                interrupt_type=interrupt.type.value,
                session_id=interrupt.session_id,
            )

    async def get(self, interrupt_id: str) -> Interrupt | None:
        """Get a pending interrupt by ID.

        Returns a deep copy so callers cannot mutate in-store state.

        The deep copy runs **outside** the lock so an arbitrarily large
        ``Interrupt`` payload doesn't block concurrent ``create`` /
        ``resolve`` writers for the duration of the copy. The store is
        only held long enough to snapshot the in-store reference; the
        reference is immutable for our purposes (``Interrupt`` is a
        frozen Pydantic model and we only ever replace the dict slot,
        never mutate in place), so copying after the release is safe.

        Args:
            interrupt_id: The interrupt identifier.

        Returns:
            A copy of the interrupt, or ``None`` if not found.
        """
        async with self._lock:
            interrupt = self._pending.get(interrupt_id)
        return copy.deepcopy(interrupt) if interrupt is not None else None

    async def list_pending(
        self,
        session_id: str | None = None,
    ) -> tuple[Interrupt, ...]:
        """List pending interrupts, optionally filtered by session.

        Returns deep copies so callers cannot mutate in-store state.

        Snapshots the matching ``Interrupt`` references under the lock,
        then deep-copies them outside the lock. Same justification as
        ``get``: the in-store entries are immutable replace-only slots,
        so a snapshot taken under the lock is safe to copy after
        release. This keeps a large pending queue's copy cost off the
        critical path of concurrent ``create`` / ``resolve`` writers.

        Args:
            session_id: Filter by session, or ``None`` for all.

        Returns:
            Tuple of copied pending interrupts.
        """
        async with self._lock:
            if session_id is None:
                snapshot: tuple[Interrupt, ...] = tuple(self._pending.values())
            else:
                snapshot = tuple(
                    i for i in self._pending.values() if i.session_id == session_id
                )
        return tuple(copy.deepcopy(i) for i in snapshot)

    async def resolve(
        self,
        resolution: InterruptResolution,
    ) -> Interrupt | None:
        """Resolve a pending interrupt and signal waiters.

        The lookup, validation, removal, result write, and ``event.set()``
        run under ``self._lock`` so a concurrent ``wait_for_resolution()``
        cannot observe a half-applied state where ``_pending`` is cleared
        but the resolution has not landed in ``_results`` yet.

        Returns ``None`` in three cases (each emits a structured log so
        operators can distinguish them):

        * ``EVENT_STREAM_INTERRUPT_NOT_FOUND`` -- no matching pending
          interrupt for ``resolution.interrupt_id``.
        * ``EVENT_STREAM_INVALID_RESUME_PAYLOAD`` (TOOL_APPROVAL) --
          ``decision`` was missing.
        * ``EVENT_STREAM_INVALID_RESUME_PAYLOAD`` (INFO_REQUEST) --
          ``response`` was missing.

        On the latter two cases the interrupt stays in ``_pending`` so
        the caller can retry with a corrected resolution; the eventual
        ``wait_for_resolution()`` timeout cleans up if no retry arrives.

        Args:
            resolution: The resolution to apply.

        Returns:
            The resolved interrupt, or ``None`` per the three cases
            documented above.
        """
        from synthorg.communication.event_stream.interrupt_resolution_validators import (  # noqa: E501, PLC0415
            INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY,
        )
        from synthorg.core.registry import (  # noqa: PLC0415
            StrategyFactoryNotFoundError,
        )

        async with self._lock:
            interrupt = self._pending.get(resolution.interrupt_id)
            if interrupt is None:
                logger.warning(
                    EVENT_STREAM_INTERRUPT_NOT_FOUND,
                    interrupt_id=resolution.interrupt_id,
                )
                return None

            # Validate resolution payload matches interrupt type. An
            # interrupt type with no registered validator is a
            # programming gap; surface it as a rejection note rather
            # than unwinding the resolve flow through an exception.
            try:
                failure_note = INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY.build(
                    interrupt.type, resolution
                )
            except StrategyFactoryNotFoundError:
                failure_note = f"no validator for interrupt type {interrupt.type!r}"
            if failure_note is not None:
                logger.warning(
                    EVENT_STREAM_INVALID_RESUME_PAYLOAD,
                    interrupt_id=resolution.interrupt_id,
                    note=failure_note,
                )
                return None

            # Remove from pending only after validation succeeds.
            del self._pending[resolution.interrupt_id]
            self._results[resolution.interrupt_id] = resolution
            event = self._events.get(resolution.interrupt_id)
            if event is not None:
                event.set()

            logger.info(
                EVENT_STREAM_INTERRUPT_RESUMED,
                interrupt_id=resolution.interrupt_id,
                resolved_by=resolution.resolved_by,
            )
            return interrupt

    async def wait_for_resolution(
        self,
        interrupt_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> InterruptResolution | None:
        """Block until the interrupt is resolved or timeout expires.

        The waiter snapshots the ``asyncio.Event`` under the lock,
        releases the lock for the ``await event.wait()`` (so resolvers
        can take the lock to set the event), then re-acquires the lock
        for the timeout / result cleanup so the dict mutations stay
        consistent with concurrent ``resolve()`` calls.

        Args:
            interrupt_id: The interrupt to wait on.
            timeout: Seconds to wait, or ``None`` for indefinite.

        Returns:
            The resolution, or ``None`` on timeout or if the
            interrupt does not exist.
        """
        async with self._lock:
            event = self._events.get(interrupt_id)
        if event is None:
            return None

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            async with self._lock:
                # Clean up expired interrupt and any orphaned result.
                self._pending.pop(interrupt_id, None)
                self._events.pop(interrupt_id, None)
                self._results.pop(interrupt_id, None)
                logger.info(
                    EVENT_STREAM_INTERRUPT_EXPIRED,
                    interrupt_id=interrupt_id,
                )
            return None

        async with self._lock:
            result = self._results.pop(interrupt_id, None)
            self._events.pop(interrupt_id, None)
        return result
