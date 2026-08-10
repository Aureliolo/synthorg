# module-kind: orchestrator
"""Meeting orchestrator -- lifecycle manager (see Communication design page).

Manages the full meeting lifecycle: validates inputs, selects the
configured protocol, executes the meeting, optionally creates tasks
from action items, and records audit trail entries.
"""

from collections.abc import Mapping
from types import MappingProxyType
from uuid import uuid4

from synthorg.communication.meeting._lens_assignment import (
    LensAssigner,
    LensStrategyConfig,
    compute_lens_assignments,
)
from synthorg.communication.meeting._meeting_utils import (
    format_exception,
    run_conflict_escalation_hook,
    validate_meeting_inputs,
)
from synthorg.communication.meeting._task_creation import (
    create_tasks_from_action_items,
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
    ConflictEscalationHook,
    MeetingProtocol,
    MeetingProtocolFactory,
    TaskCreator,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import (
    MEETING_BUDGET_EXHAUSTED,
    MEETING_CANCELLED,
    MEETING_COMPLETED,
    MEETING_FAILED,
    MEETING_PROTOCOL_NOT_FOUND,
    MEETING_RECORD_MIRROR_DRIFT,
    MEETING_STARTED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)


def _freeze_registry(
    registry: Mapping[MeetingProtocolType, MeetingProtocolFactory] | None,
) -> MappingProxyType[MeetingProtocolType, MeetingProtocolFactory]:
    """Copy a protocol registry into an immutable mapping.

    Args:
        registry: The factories to install, or ``None`` for none yet.

    Returns:
        A read-only view over a private copy, so a caller mutating the
        mapping it passed cannot change which protocol a meeting runs.
    """
    factories: dict[MeetingProtocolType, MeetingProtocolFactory] = (
        {} if registry is None else dict(registry)
    )
    return MappingProxyType(factories)


class MeetingOrchestrator:
    """Lifecycle manager for meeting execution.

    Coordinates protocol selection, execution, task creation from
    action items, and audit trail recording.  Meeting records are
    stored in memory; see the persistence layer for durable storage
    when available.

    The protocol registry is installed rather than constructed: the
    factories bake in organisation-wide strategy policy read from
    settings, so the ``meeting_protocol_registry`` subsystem owns
    building them and reinstalls a replacement when that policy changes.
    Until it does, this orchestrator exists (the REST surface stays
    available) and refuses to run a meeting, naming the subsystem.

    Args:
        protocol_registry: Mapping of protocol types to factories, each
            building an instance from the meeting's own protocol config.
            Empty until the subsystem installs one.
        agent_caller: Callback to invoke agents during meetings.
        task_creator: Optional callback to create tasks from action items.
    """

    __slots__ = (
        "_agent_caller",
        "_config_resolver",
        "_conflict_escalation_hook",
        "_lens_assigner",
        "_protocol_registry",
        "_records",
        "_records_by_id",
        "_strategy_config",
        "_task_creator",
    )

    def __init__(
        self,
        *,
        protocol_registry: Mapping[MeetingProtocolType, MeetingProtocolFactory]
        | None = None,
        agent_caller: AgentCaller,
        task_creator: TaskCreator | None = None,
        strategy_config: LensStrategyConfig | None = None,
        lens_assigner: LensAssigner | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
        conflict_escalation_hook: ConflictEscalationHook | None = None,
    ) -> None:
        self._protocol_registry: MappingProxyType[
            MeetingProtocolType, MeetingProtocolFactory
        ] = _freeze_registry(protocol_registry)
        self._agent_caller = agent_caller
        self._task_creator = task_creator
        self._strategy_config = strategy_config
        self._lens_assigner = lens_assigner
        self._config_resolver = config_resolver
        self._conflict_escalation_hook = conflict_escalation_hook
        self._records: list[MeetingRecord] = []
        # Mirror records by id for O(1) lookup via ``get_record``.
        # The list keeps chronological order (used by ``get_records``);
        # the dict serves point lookups so controller endpoints don't
        # need to scan every record on every fetch.
        self._records_by_id: dict[str, MeetingRecord] = {}

    @property
    def has_protocol_registry(self) -> bool:
        """Whether a protocol registry is installed.

        Computed from the field :meth:`set_protocol_registry` writes, so
        the subsystem liveness probe reading this cannot claim an
        installation that did not happen.
        """
        return bool(self._protocol_registry)

    def set_protocol_registry(
        self,
        registry: Mapping[MeetingProtocolType, MeetingProtocolFactory],
    ) -> None:
        """Install the protocol factories meetings are built from.

        The factories close over organisation-wide strategy policy, so
        they are rebuilt and reinstalled when that policy changes rather
        than being fixed at construction.

        Args:
            registry: One factory per protocol type.
        """
        self._protocol_registry = _freeze_registry(registry)

    def clear_protocol_registry(self) -> None:
        """Uninstall the protocol factories.

        Leaves the orchestrator serving reads while refusing to run a
        meeting, which is the honest state between a teardown and the
        activation that replaces it.
        """
        self._protocol_registry = _freeze_registry(None)

    def set_conflict_escalation_hook(self, hook: ConflictEscalationHook) -> None:
        """Install the post-meeting conflict-escalation hook.

        Post-construction injection: the conflict-resolution service the
        hook drives is built later in the same wiring pass than the
        orchestrator, so the composition root sets the hook here rather
        than at construction.
        """
        self._conflict_escalation_hook = hook

    async def run_meeting(
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
            StrategyFactoryNotFoundError: If the protocol's own
                construction cannot resolve a strategy it needs, such as
                a conflict detector. The protocol is built per meeting,
                so this surfaces here rather than at wiring time.
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

    async def _run_and_record(
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
        protocol = self._resolve_protocol(meeting_id, protocol_config)

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
        lens_assignments = compute_lens_assignments(
            participant_ids,
            assigner=self._lens_assigner,
            strategy_config=self._strategy_config,
        )

        result = await self._execute_protocol(
            protocol,
            meeting_id,
            meeting_type_name=meeting_type_name,
            agenda=agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            token_budget=token_budget,
            lens_assignments=lens_assignments,
        )

        if isinstance(result, MeetingRecord):
            return result

        create_tasks_from_action_items(
            self._task_creator,
            meeting_id=meeting_id,
            protocol_config=protocol_config,
            minutes=result,
        )
        await run_conflict_escalation_hook(
            self._conflict_escalation_hook, result, meeting_id=meeting_id
        )
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

    async def _execute_protocol(
        self,
        protocol: MeetingProtocol,
        meeting_id: str,
        *,
        meeting_type_name: str,
        agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        token_budget: int,
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
                meeting_type_name=meeting_type_name,
                protocol=protocol,
                token_budget=token_budget,
                status=MeetingStatus.BUDGET_EXHAUSTED,
                exc=exc,
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
                meeting_type_name=meeting_type_name,
                protocol=protocol,
                token_budget=token_budget,
                status=status,
                exc=exc,
            )

    def _make_failure_record(
        self,
        meeting_id: str,
        *,
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

    def _resolve_protocol(
        self,
        meeting_id: str,
        protocol_config: MeetingProtocolConfig,
    ) -> MeetingProtocol:
        """Build this meeting's protocol from its own configuration.

        Returns:
            A ``MeetingProtocol`` carrying ``protocol_config``'s
            matching sub-config.

        Raises:
            MeetingProtocolNotFoundError: If not registered.
        """
        protocol_type = protocol_config.protocol
        registry_empty = not self._protocol_registry
        factory = self._protocol_registry.get(protocol_type)
        if factory is None:
            logger.warning(
                MEETING_PROTOCOL_NOT_FOUND,
                meeting_id=meeting_id,
                protocol_type=protocol_type,
                registry_empty=registry_empty,
            )
            # An empty registry is a different fault from an unregistered
            # protocol type, and sends the operator somewhere else: the
            # protocol registry is installed by a reconciled subsystem,
            # so an empty one means that subsystem is not up yet.
            msg = (
                "No meeting protocol registry is installed; the "
                "'meeting_protocol_registry' subsystem has not activated "
                "(see GET /subsystems)"
                if registry_empty
                else f"Protocol {protocol_type!r} is not registered"
            )
            raise MeetingProtocolNotFoundError(
                msg,
                context={
                    "meeting_id": meeting_id,
                    "protocol_type": protocol_type,
                    "registry_empty": registry_empty,
                },
            )
        return factory(protocol_config)
