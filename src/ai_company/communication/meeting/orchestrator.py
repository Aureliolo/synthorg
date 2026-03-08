"""Meeting orchestrator — lifecycle manager (DESIGN_SPEC Section 5.7).

Manages the full meeting lifecycle: validates inputs, selects the
configured protocol, executes the meeting, optionally creates tasks
from action items, and records audit trail entries.
"""

from collections import Counter
from collections.abc import Mapping  # noqa: TC003
from types import MappingProxyType
from uuid import uuid4

from ai_company.communication.meeting.config import MeetingProtocolConfig  # noqa: TC001
from ai_company.communication.meeting.enums import (
    MeetingProtocolType,
    MeetingStatus,
)
from ai_company.communication.meeting.errors import (
    MeetingBudgetExhaustedError,
    MeetingParticipantError,
    MeetingProtocolNotFoundError,
)
from ai_company.communication.meeting.models import (
    MeetingAgenda,
    MeetingMinutes,
    MeetingRecord,
)
from ai_company.communication.meeting.protocol import (  # noqa: TC001
    AgentCaller,
    MeetingProtocol,
    TaskCreator,
)
from ai_company.observability import get_logger
from ai_company.observability.events.meeting import (
    MEETING_ACTION_ITEM_EXTRACTED,
    MEETING_BUDGET_EXHAUSTED,
    MEETING_COMPLETED,
    MEETING_FAILED,
    MEETING_PROTOCOL_NOT_FOUND,
    MEETING_STARTED,
    MEETING_TASK_CREATED,
    MEETING_TASK_CREATION_FAILED,
    MEETING_VALIDATION_FAILED,
)

logger = get_logger(__name__)


def _format_exception(exc: BaseException) -> str:
    """Format an exception for error messages.

    Flattens ``ExceptionGroup`` (produced by ``asyncio.TaskGroup``
    when multiple concurrent tasks fail) into a single human-readable
    string.  Handles nested groups recursively.  Non-group exceptions
    are returned via ``str()``.
    """
    if isinstance(exc, ExceptionGroup):
        parts: list[str] = []
        for sub in exc.exceptions:
            if isinstance(sub, ExceptionGroup):
                parts.append(_format_exception(sub))
            else:
                parts.append(f"{type(sub).__name__}: {sub}")
        return f"Multiple errors: {'; '.join(parts)}"
    return str(exc)


class MeetingOrchestrator:
    """Lifecycle manager for meeting execution.

    Coordinates protocol selection, execution, task creation from
    action items, and audit trail recording.  Meeting records are
    stored in memory; see the persistence layer for durable storage
    when available.

    Args:
        protocol_registry: Mapping of protocol types to implementations.
        agent_caller: Callback to invoke agents during meetings.
        task_creator: Optional callback to create tasks from action items.
    """

    __slots__ = (
        "_agent_caller",
        "_protocol_registry",
        "_records",
        "_task_creator",
    )

    def __init__(
        self,
        *,
        protocol_registry: Mapping[MeetingProtocolType, MeetingProtocol],
        agent_caller: AgentCaller,
        task_creator: TaskCreator | None = None,
    ) -> None:
        self._protocol_registry: MappingProxyType[
            MeetingProtocolType, MeetingProtocol
        ] = MappingProxyType(dict(protocol_registry))
        self._agent_caller = agent_caller
        self._task_creator = task_creator
        self._records: list[MeetingRecord] = []

    async def run_meeting(  # noqa: PLR0913
        self,
        *,
        meeting_type_name: str,
        protocol_config: MeetingProtocolConfig,
        agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        token_budget: int,
    ) -> MeetingRecord:
        """Execute a meeting and return the audit record.

        Validation errors (``MeetingParticipantError``,
        ``MeetingProtocolNotFoundError``) are raised directly.
        Domain and runtime errors during protocol execution are caught
        and returned as a ``MeetingRecord`` with ``FAILED`` or
        ``BUDGET_EXHAUSTED`` status.  ``BaseException`` subclasses
        (e.g. ``KeyboardInterrupt``) are NOT caught.

        Args:
            meeting_type_name: Name of the meeting type from config.
            protocol_config: Protocol configuration to use.
            agenda: The meeting agenda.
            leader_id: ID of the agent leading the meeting.
            participant_ids: IDs of participating agents.
            token_budget: Maximum tokens for the meeting (must be > 0).

        Returns:
            Meeting record with status and optional minutes.

        Raises:
            MeetingProtocolNotFoundError: If the configured protocol
                is not in the registry.
            MeetingParticipantError: If participant list is empty,
                contains duplicates, or leader is in participants.
            ValueError: If token_budget is not positive.
        """
        meeting_id = f"mtg-{uuid4().hex[:12]}"
        protocol_type = protocol_config.protocol

        self._validate_inputs(
            meeting_id,
            leader_id,
            participant_ids,
            token_budget,
        )
        protocol = self._resolve_protocol(meeting_id, protocol_type)

        logger.info(
            MEETING_STARTED,
            meeting_id=meeting_id,
            meeting_type=meeting_type_name,
            protocol=protocol_type,
            leader_id=leader_id,
            participant_count=len(participant_ids),
            token_budget=token_budget,
        )

        result = await self._execute_protocol(
            protocol,
            meeting_id,
            meeting_type_name,
            agenda,
            leader_id,
            participant_ids,
            token_budget,
        )

        if isinstance(result, MeetingRecord):
            return result

        self._create_tasks(meeting_id, protocol_config, result)
        return self._record_success(
            meeting_id,
            meeting_type_name,
            protocol_type,
            result,
            token_budget,
        )

    def get_records(self) -> tuple[MeetingRecord, ...]:
        """Return all meeting audit records.

        Returns:
            Tuple of meeting records in chronological order.
        """
        return tuple(self._records)

    async def _execute_protocol(  # noqa: PLR0913
        self,
        protocol: MeetingProtocol,
        meeting_id: str,
        meeting_type_name: str,
        agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        token_budget: int,
    ) -> MeetingMinutes | MeetingRecord:
        """Run the protocol, catching errors as failure records.

        ``MeetingBudgetExhaustedError`` produces a
        ``BUDGET_EXHAUSTED`` record; all other ``Exception``
        subclasses (including ``ExceptionGroup`` from parallel
        ``TaskGroup`` execution) produce ``FAILED`` records.
        ``BaseException`` subclasses (e.g. ``KeyboardInterrupt``)
        propagate uncaught.

        Returns:
            Minutes on success, or a failure MeetingRecord on error.
        """
        try:
            return await protocol.run(
                meeting_id=meeting_id,
                agenda=agenda,
                leader_id=leader_id,
                participant_ids=participant_ids,
                agent_caller=self._agent_caller,
                token_budget=token_budget,
            )
        except MeetingBudgetExhaustedError as exc:
            return self._make_failure_record(
                meeting_id,
                meeting_type_name,
                protocol,
                token_budget,
                MeetingStatus.BUDGET_EXHAUSTED,
                exc,
            )
        except Exception as exc:
            status = MeetingStatus.FAILED
            if isinstance(exc, ExceptionGroup):
                budget_group = exc.subgroup(MeetingBudgetExhaustedError)
                if budget_group is not None and len(budget_group.exceptions) == len(
                    exc.exceptions
                ):
                    status = MeetingStatus.BUDGET_EXHAUSTED
            return self._make_failure_record(
                meeting_id,
                meeting_type_name,
                protocol,
                token_budget,
                status,
                exc,
            )

    def _make_failure_record(  # noqa: PLR0913
        self,
        meeting_id: str,
        meeting_type_name: str,
        protocol: MeetingProtocol,
        token_budget: int,
        status: MeetingStatus,
        exc: BaseException,
    ) -> MeetingRecord:
        """Build, store, and log a failure record."""
        error_msg = _format_exception(exc)
        record = MeetingRecord(
            meeting_id=meeting_id,
            meeting_type_name=meeting_type_name,
            protocol_type=protocol.get_protocol_type(),
            status=status,
            error_message=error_msg,
            token_budget=token_budget,
        )
        self._records.append(record)

        if status == MeetingStatus.BUDGET_EXHAUSTED:
            logger.warning(
                MEETING_BUDGET_EXHAUSTED,
                meeting_id=meeting_id,
                status=status,
                error=error_msg,
                error_type=type(exc).__name__,
            )
        else:
            logger.error(
                MEETING_FAILED,
                meeting_id=meeting_id,
                status=status,
                error=error_msg,
                error_type=type(exc).__name__,
            )
        return record

    def _record_success(
        self,
        meeting_id: str,
        meeting_type_name: str,
        protocol_type: MeetingProtocolType,
        minutes: MeetingMinutes,
        token_budget: int,
    ) -> MeetingRecord:
        """Build, store, and log a success record."""
        record = MeetingRecord(
            meeting_id=meeting_id,
            meeting_type_name=meeting_type_name,
            protocol_type=protocol_type,
            status=MeetingStatus.COMPLETED,
            minutes=minutes,
            token_budget=token_budget,
        )
        self._records.append(record)
        logger.info(
            MEETING_COMPLETED,
            meeting_id=meeting_id,
            total_tokens=minutes.total_tokens,
            contributions=len(minutes.contributions),
        )
        return record

    def _create_tasks(
        self,
        meeting_id: str,
        protocol_config: MeetingProtocolConfig,
        minutes: MeetingMinutes,
    ) -> None:
        """Create tasks from action items if configured."""
        if (
            self._task_creator is None
            or not protocol_config.auto_create_tasks
            or not minutes.action_items
        ):
            return

        total = len(minutes.action_items)
        logger.info(
            MEETING_ACTION_ITEM_EXTRACTED,
            meeting_id=meeting_id,
            action_item_count=total,
        )
        failures = 0
        for action_item in minutes.action_items:
            try:
                self._task_creator(
                    action_item.description,
                    action_item.assignee_id,
                    action_item.priority,
                )
                logger.debug(
                    MEETING_TASK_CREATED,
                    meeting_id=meeting_id,
                    description=action_item.description,
                    assignee=action_item.assignee_id,
                )
            except Exception:
                failures += 1
                logger.exception(
                    MEETING_TASK_CREATION_FAILED,
                    meeting_id=meeting_id,
                    description=action_item.description,
                    assignee=action_item.assignee_id,
                )
        if failures:
            logger.warning(
                MEETING_TASK_CREATION_FAILED,
                meeting_id=meeting_id,
                failed_count=failures,
                total_count=total,
            )

    def _validate_inputs(
        self,
        meeting_id: str,
        leader_id: str,
        participant_ids: tuple[str, ...],
        token_budget: int,
    ) -> None:
        """Validate meeting inputs.

        Raises:
            MeetingParticipantError: If participants are empty, contain
                duplicates, or leader is in participants.
            ValueError: If token_budget is not positive.
        """
        if token_budget <= 0:
            logger.warning(
                MEETING_VALIDATION_FAILED,
                meeting_id=meeting_id,
                error=f"token_budget must be positive, got {token_budget}",
            )
            msg = f"token_budget must be positive, got {token_budget}"
            raise ValueError(msg)

        if not participant_ids:
            logger.warning(
                MEETING_VALIDATION_FAILED,
                meeting_id=meeting_id,
                error="at least one participant is required",
            )
            msg = "At least one participant is required"
            raise MeetingParticipantError(
                msg,
                context={"meeting_id": meeting_id},
            )
        if len(participant_ids) != len(set(participant_ids)):
            dupes = sorted(v for v, c in Counter(participant_ids).items() if c > 1)
            logger.warning(
                MEETING_VALIDATION_FAILED,
                meeting_id=meeting_id,
                error="duplicate participant_ids",
                duplicates=dupes,
            )
            msg = f"Duplicate participant IDs: {dupes}"
            raise MeetingParticipantError(
                msg,
                context={
                    "meeting_id": meeting_id,
                    "duplicates": dupes,
                },
            )
        if leader_id in participant_ids:
            logger.warning(
                MEETING_VALIDATION_FAILED,
                meeting_id=meeting_id,
                error="leader in participant_ids",
                leader_id=leader_id,
            )
            msg = (
                f"Leader {leader_id!r} must not be in participant_ids "
                f"(leader participates implicitly)"
            )
            raise MeetingParticipantError(
                msg,
                context={
                    "meeting_id": meeting_id,
                    "leader_id": leader_id,
                },
            )

    def _resolve_protocol(
        self,
        meeting_id: str,
        protocol_type: MeetingProtocolType,
    ) -> MeetingProtocol:
        """Look up the protocol implementation.

        Raises:
            MeetingProtocolNotFoundError: If not registered.
        """
        protocol = self._protocol_registry.get(protocol_type)
        if protocol is None:
            logger.warning(
                MEETING_PROTOCOL_NOT_FOUND,
                meeting_id=meeting_id,
                protocol_type=protocol_type,
            )
            msg = f"Protocol {protocol_type!r} is not registered"
            raise MeetingProtocolNotFoundError(
                msg,
                context={
                    "meeting_id": meeting_id,
                    "protocol_type": protocol_type,
                },
            )
        return protocol
