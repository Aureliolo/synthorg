# module-kind: orchestrator
"""Meeting orchestrator -- lifecycle manager (see Communication design page).

Manages the full meeting lifecycle: validates inputs, selects the
configured protocol, executes the meeting, optionally creates tasks
from action items, and records audit trail entries.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol
from uuid import uuid4

from synthorg.communication.meeting._meeting_utils import (
    format_exception,
    validate_meeting_inputs,
)
from synthorg.communication.meeting.config import MeetingProtocolConfig
from synthorg.communication.meeting.enums import (
    MeetingProtocolType,
    MeetingStatus,
)
from synthorg.communication.meeting.errors import (
    MeetingBudgetExhaustedError,
    MeetingProtocolNotFoundError,
)
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingMinutes,
    MeetingRecord,
)
from synthorg.communication.meeting.protocol import (
    AgentCaller,
    MeetingProtocol,
    TaskCreator,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
)
from synthorg.observability.events.meeting import (
    MEETING_ACTION_ITEM_EXTRACTED,
    MEETING_BUDGET_EXHAUSTED,
    MEETING_CANCELLED,
    MEETING_COMPLETED,
    MEETING_FAILED,
    MEETING_LENS_ASSIGNMENT_FAILED,
    MEETING_PROTOCOL_NOT_FOUND,
    MEETING_RECORD_MIRROR_DRIFT,
    MEETING_STARTED,
    MEETING_TASK_CREATED,
    MEETING_TASK_CREATION_FAILED,
    MEETING_TASKS_CAPPED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)


class _LensStrategyConfig(Protocol):
    """Minimal view of the lens-strategy config.

    Typed structurally here (rather than importing the concrete config)
    to avoid an import cycle between the meeting orchestrator and the
    lens-strategy package.
    """

    @property
    def default_lenses(self) -> tuple[str, ...]:
        """The configured default lens collection."""
        ...


class _LensAssigner(Protocol):
    """Minimal view of the lens assigner.

    Typed structurally here to avoid the same import cycle as
    :class:`_LensStrategyConfig`.
    """

    def assign(
        self,
        participant_ids: tuple[str, ...],
        available_lenses: tuple[str, ...],
    ) -> dict[str, str]:
        """Assign a lens to each participant."""
        ...


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
        "_config_resolver",
        "_lens_assigner",
        "_protocol_registry",
        "_records",
        "_records_by_id",
        "_strategy_config",
        "_task_creator",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        protocol_registry: Mapping[MeetingProtocolType, MeetingProtocol],
        agent_caller: AgentCaller,
        task_creator: TaskCreator | None = None,
        strategy_config: _LensStrategyConfig | None = None,
        lens_assigner: _LensAssigner | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        self._protocol_registry: MappingProxyType[
            MeetingProtocolType, MeetingProtocol
        ] = MappingProxyType(dict(protocol_registry))
        self._agent_caller = agent_caller
        self._task_creator = task_creator
        self._strategy_config = strategy_config
        self._lens_assigner = lens_assigner
        self._config_resolver = config_resolver
        self._records: list[MeetingRecord] = []
        # Mirror records by id for O(1) lookup via ``get_record``.
        # The list keeps chronological order (used by ``get_records``);
        # the dict serves point lookups so controller endpoints don't
        # need to scan every record on every fetch.
        self._records_by_id: dict[str, MeetingRecord] = {}

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

        # Validate inputs before the kill-switch check so an invalid
        # request (e.g. token_budget=0, empty participants) always
        # raises the same ``ValueError`` /
        # ``MeetingParticipantError`` regardless of whether the
        # operator has paused meetings -- callers should not see
        # divergent error semantics across kill-switch state, and
        # constructing the cancellation ``MeetingRecord`` below would
        # otherwise hit pydantic validation on ``token_budget`` (gt=0).
        validate_meeting_inputs(
            meeting_id,
            leader_id,
            participant_ids,
            token_budget,
        )

        # ``communication.meetings_enabled`` kill switch: when False
        # the orchestrator records a CANCELLED meeting (no protocol
        # invocation, no agent calls) so an operator can suspend the
        # meetings subsystem without tearing down the scheduler.
        meetings_enabled = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="communication",
            key="meetings_enabled",
            fallback=True,
        )
        if not meetings_enabled:
            return self._record_cancellation(
                meeting_id=meeting_id,
                meeting_type_name=meeting_type_name,
                protocol_type=protocol_type,
                token_budget=token_budget,
            )

        return await self._run_and_record(
            meeting_id=meeting_id,
            meeting_type_name=meeting_type_name,
            protocol_config=protocol_config,
            agenda=agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            token_budget=token_budget,
        )

    def _record_cancellation(
        self,
        *,
        meeting_id: str,
        meeting_type_name: str,
        protocol_type: MeetingProtocolType,
        token_budget: int,
    ) -> MeetingRecord:
        """Record a kill-switch CANCELLED meeting (no protocol run).

        Returns:
            The stored CANCELLED ``MeetingRecord``.
        """
        cancelled_record = MeetingRecord(
            meeting_id=meeting_id,
            meeting_type_name=meeting_type_name,
            protocol_type=protocol_type,
            status=MeetingStatus.CANCELLED,
            token_budget=token_budget,
        )
        # Persist the cancellation in ``self._records`` so ``get_records()``
        # reports it alongside successful and protocol-failed runs; an audit
        # trail that silently drops kill-switch cancellations would mislead
        # operators reconstructing what happened during a paused window.
        self._append_record(cancelled_record)
        # ``MEETING_CANCELLED`` (not ``MEETING_FAILED``): operator cancellations
        # should not skew failure metrics or trip alerts wired to
        # ``meeting.lifecycle.failed``. Logged AFTER the record append per the
        # post-persist contract (CLAUDE.md state-transition rule).
        logger.info(
            MEETING_CANCELLED,
            meeting_id=meeting_id,
            meeting_type=meeting_type_name,
            reason="meetings_disabled_by_setting",
        )
        return cancelled_record

    async def _run_and_record(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        meeting_type_name: str,
        protocol_config: MeetingProtocolConfig,
        agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        token_budget: int,
    ) -> MeetingRecord:
        """Resolve the protocol, execute it, and record the outcome.

        Returns:
            The terminal ``MeetingRecord`` (protocol result, a recorded
            success, or FAILED / BUDGET_EXHAUSTED on a caught error).
        """
        protocol_type = protocol_config.protocol
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

        # Lens assignment (optional, when strategy config is present)
        lens_assignments = self._compute_lens_assignments(participant_ids)

        result = await self._execute_protocol(
            protocol,
            meeting_id,
            meeting_type_name,
            agenda,
            leader_id,
            participant_ids,
            token_budget,
            lens_assignments=lens_assignments,
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

    def get_record(self, meeting_id: str) -> MeetingRecord | None:
        """Return the meeting record matching ``meeting_id`` or ``None``.

        O(1) lookup via the by-id mirror; controller endpoints fetch a
        single meeting without scanning the full record list.
        """
        return self._records_by_id.get(meeting_id)

    def _append_record(self, record: MeetingRecord) -> None:
        """Append a record to the chronological list and the by-id mirror.

        Internal: every site that grows ``self._records`` MUST go through
        this helper so the mirror stays in lock-step with the list.

        ``_check_invariant`` runs under ``__debug__`` so production
        builds skip the O(1) length comparison; under ``-O`` the
        assertion is elided. In test / dev mode it catches future
        mutation sites that bypass the helper and drift the dict.
        """
        self._records.append(record)
        self._records_by_id[record.meeting_id] = record
        self._check_invariant()

    def _check_invariant(self) -> None:
        """Verify the list and dict mirror agree on size.

        Cheap O(1) sanity check: any drift between ``_records`` and
        ``_records_by_id`` is a bug in record-mutation discipline. The
        assertion is elided under ``python -O``.
        """
        assert len(self._records) == len(self._records_by_id), (  # noqa: S101
            f"MeetingOrchestrator record drift: list={len(self._records)} "
            f"dict={len(self._records_by_id)}"
        )

    def delete_record(self, meeting_id: str) -> bool:
        """Remove the meeting record matching ``meeting_id``.

        Synchronous because the in-memory store has no I/O; the
        surrounding service-layer wrapper is async to match the rest of
        the persistence contract.

        Returns:
            ``True`` when a record was removed, ``False`` when no record
            had the supplied id.
        """
        record = self._records_by_id.pop(meeting_id, None)
        if record is None:
            return False
        # Locate the same record in the chronological list and remove it.
        # ``list.remove`` is O(n) but acceptable here because the list
        # mirrors the dict; both are bounded by the in-memory record set.
        try:
            self._records.remove(record)
        except ValueError:
            # The list and dict drifted -- restore the dict entry so the
            # caller's "found" answer doesn't regress to a silent loss
            # AND emit a structured ERROR log so operators see the
            # invariant violation instead of debugging a phantom
            # "delete returned False" later.
            self._records_by_id[meeting_id] = record
            # TRY400: this is an invariant-violation log, not a stack
            # trace use case; the relevant context is the structured
            # fields below, not the ValueError trace.
            logger.error(
                MEETING_RECORD_MIRROR_DRIFT,
                reason="record_mirror_drift",
                meeting_id=meeting_id,
                list_len=len(self._records),
                dict_len=len(self._records_by_id),
            )
            return False
        return True

    async def _execute_protocol(  # noqa: PLR0913
        self,
        protocol: MeetingProtocol,
        meeting_id: str,
        meeting_type_name: str,
        agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        token_budget: int,
        *,
        lens_assignments: Mapping[str, str] | None = None,
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
                lens_assignments=lens_assignments,
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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
        """Build, store, and log a failure record.

        The terminal-state log (``MEETING_BUDGET_EXHAUSTED`` /
        ``MEETING_FAILED``) fires AFTER the record is appended so
        the audit trail only records transitions that actually
        landed. ``MeetingRecord`` construction or ``self._records``
        append could in principle raise (model_validator, memory
        pressure); if they do, the log is skipped. This handler does
        not emit a separate ``*_STATUS_TRANSITIONED`` event today --
        the terminal events above are the canonical hop log for the
        meeting subsystem.

        Returns:
            The stored failure ``MeetingRecord``.
        """
        error_msg = format_exception(exc)
        record = MeetingRecord(
            meeting_id=meeting_id,
            meeting_type_name=meeting_type_name,
            protocol_type=protocol.get_protocol_type(),
            status=status,
            error_message=error_msg,
            token_budget=token_budget,
        )
        self._append_record(record)
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
        """Build, store, and log a success record.

        The terminal-state log (``MEETING_COMPLETED``) fires AFTER
        the record is appended so the audit trail only records
        transitions that actually landed. This handler does not
        emit a separate ``*_STATUS_TRANSITIONED`` event today --
        ``MEETING_COMPLETED`` is the canonical hop log for the
        meeting subsystem.

        Returns:
            The stored success ``MeetingRecord``.
        """
        record = MeetingRecord(
            meeting_id=meeting_id,
            meeting_type_name=meeting_type_name,
            protocol_type=protocol_type,
            status=MeetingStatus.COMPLETED,
            minutes=minutes,
            token_budget=token_budget,
        )
        self._append_record(record)
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

        items = minutes.action_items
        cap = protocol_config.max_tasks_per_meeting
        if cap is not None and len(items) > cap:
            logger.info(
                MEETING_TASKS_CAPPED,
                meeting_id=meeting_id,
                total_action_items=len(items),
                max_tasks_per_meeting=cap,
            )
            items = items[:cap]

        total = len(items)
        logger.info(
            MEETING_ACTION_ITEM_EXTRACTED,
            meeting_id=meeting_id,
            action_item_count=total,
        )
        failures = 0
        for action_item in items:
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
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                failures += 1
                log_exception_redacted(
                    logger,
                    MEETING_TASK_CREATION_FAILED,
                    exc,
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

    def _compute_lens_assignments(
        self,
        participant_ids: tuple[str, ...],
    ) -> dict[str, str] | None:
        """Compute lens assignments for participants.

        Returns:
            A mapping of participant id to lens, or ``None`` when no lens
            assigner is configured.
        """
        if self._lens_assigner is None or self._strategy_config is None:
            return None
        try:
            lenses = self._strategy_config.default_lenses
            result: dict[str, str] = self._lens_assigner.assign(
                participant_ids,
                lenses,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                MEETING_LENS_ASSIGNMENT_FAILED,
                error="Lens assignment failed, proceeding without lenses",
            )
            return None

        # Validate the returned mapping: keys must match participant_ids,
        # values must be non-empty strings.
        expected_ids = set(participant_ids)
        if not isinstance(result, dict) or set(result.keys()) != expected_ids:
            logger.warning(
                MEETING_LENS_ASSIGNMENT_FAILED,
                error="Lens assigner returned mapping with mismatched keys",
                expected_count=len(expected_ids),
                actual_count=len(result) if isinstance(result, dict) else -1,
            )
            return None
        if not all(isinstance(v, str) and v for v in result.values()):
            logger.warning(
                MEETING_LENS_ASSIGNMENT_FAILED,
                error="Lens assigner returned non-string or empty lens value",
            )
            return None

        return dict(result)

    def _resolve_protocol(
        self,
        meeting_id: str,
        protocol_type: MeetingProtocolType,
    ) -> MeetingProtocol:
        """Look up the protocol implementation.

        Returns:
            The registered ``MeetingProtocol`` for ``protocol_type``.

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
