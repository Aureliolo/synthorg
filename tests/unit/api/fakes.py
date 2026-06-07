"""In-memory fake implementations for API unit tests."""

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import AwareDatetime

from synthorg.budget.cost_record import CostRecord
from synthorg.communication.channel import Channel
from synthorg.communication.message import Message
from synthorg.communication.subscription import DeliveryEnvelope, Subscription
from synthorg.core.artifact import Artifact
from synthorg.core.auth.models import ApiKey
from synthorg.core.codebase_structure_map import CodebaseStructureMap
from synthorg.core.enums import (
    ExecutionStatus,
    TaskStatus,
)
from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.core.project import Project
from synthorg.core.project_environment import ProjectEnvironment
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.models import DocMetadata
from synthorg.engine.agent_state import AgentRuntimeState
from synthorg.engine.checkpoint.models import Checkpoint, Heartbeat
from synthorg.hr.enums import LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    TaskMetricRecord,
)
from synthorg.persistence.artifact_protocol import ArtifactFilterSpec
from synthorg.persistence.audit_protocol import AuditFilterSpec
from synthorg.persistence.checkpoint_protocol import CheckpointFilterSpec
from synthorg.persistence.docs_protocol import DocsFilterSpec
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameAggregate,
    FlightRecorderFrameFilterSpec,
)
from synthorg.persistence.message_protocol import MessageFilterSpec
from synthorg.persistence.preset_protocol import Preset
from synthorg.persistence.project_brain_protocol import BrainFilterSpec
from synthorg.persistence.project_protocol import ProjectFilterSpec
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec
from synthorg.persistence.settings_protocol import SettingRow
from synthorg.persistence.user_protocol import ApiKeyFilterSpec
from synthorg.project_brain.errors import BrainEntryRevisionConflictError
from synthorg.project_brain.models import BrainEntry
from synthorg.security.models import AuditEntry
from synthorg.security.redteam.models import RedTeamReportRecord
from synthorg.security.timeout.parked_context import ParkedContext

if TYPE_CHECKING:
    # Type-only re-export of ``FakePersistenceBackend`` so callers can
    # write ``from tests.unit.api.fakes import FakePersistenceBackend``
    # and have mypy resolve the symbol to its real type. At runtime the
    # name resolves via ``__getattr__`` below (PEP 562 lazy attribute
    # access), preserving the historical circular-import workaround
    # without forcing 36 call sites onto a direct ``fakes_backend``
    # import path.
    from tests.unit.api.fakes_backend import FakePersistenceBackend

    __all__ = ["FakePersistenceBackend"]


class FakeTaskRepository:
    """In-memory task repository for tests."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    async def save(self, entity: Task) -> None:
        self._tasks[str(entity.id)] = entity

    async def get(self, entity_id: str) -> Task | None:
        return self._tasks.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        result = sorted(self._tasks.values(), key=lambda t: t.id)
        return tuple(result[offset : offset + limit])

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        result = self._filtered(
            getattr(filter_spec, "status", None),
            getattr(filter_spec, "assigned_to", None),
            getattr(filter_spec, "project", None),
        )
        return tuple(result[offset : offset + limit])

    async def count(self, filter_spec: object) -> int:
        return len(
            self._filtered(
                getattr(filter_spec, "status", None),
                getattr(filter_spec, "assigned_to", None),
                getattr(filter_spec, "project", None),
            )
        )

    def _filtered(
        self,
        status: TaskStatus | None,
        assigned_to: str | None,
        project: str | None,
    ) -> list[Task]:
        result = sorted(self._tasks.values(), key=lambda t: t.id)
        if status is not None:
            result = [t for t in result if t.status == status]
        if assigned_to is not None:
            result = [t for t in result if t.assigned_to == assigned_to]
        if project is not None:
            result = [t for t in result if t.project == project]
        return result

    async def delete(self, entity_id: str) -> bool:
        return self._tasks.pop(entity_id, None) is not None


class FakeCostRecordRepository:
    """In-memory cost record repository for tests."""

    def __init__(self) -> None:
        self._records: list[CostRecord] = []

    async def append(self, event: CostRecord) -> None:
        self._records.append(event)

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CostRecord, ...]:
        result = self._records
        agent_id = getattr(filter_spec, "agent_id", None)
        task_id = getattr(filter_spec, "task_id", None)
        if agent_id is not None:
            result = [r for r in result if r.agent_id == agent_id]
        if task_id is not None:
            result = [r for r in result if r.task_id == task_id]
        return tuple(result[offset : offset + limit])

    async def purge_before(self, threshold: object) -> int:
        del threshold
        return 0

    async def aggregate(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> float:
        from synthorg.persistence.cost_record_protocol import (
            CostRecordFilterSpec,
        )

        records = await self.query(
            CostRecordFilterSpec(agent_id=agent_id, task_id=task_id),
        )
        return sum(r.cost for r in records)


class FakeMessageRepository:
    """In-memory message repository for tests."""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    async def append(self, message: Message) -> None:
        if any(m.id == message.id for m in self._messages):
            msg = f"Message {message.id} already exists"
            raise DuplicateRecordError(msg)
        self._messages.append(message)

    async def get_history(
        self,
        channel: NotBlankStr,
        *,
        limit: int = 100,
    ) -> tuple[Message, ...]:
        if limit < 1:
            msg = f"limit must be a positive integer, got {limit}"
            raise QueryError(msg)
        result = sorted(
            (m for m in self._messages if m.channel == channel),
            key=lambda m: m.timestamp,
            reverse=True,
        )
        return tuple(result[:limit])

    async def get_by_id(
        self,
        channel: NotBlankStr,
        message_id: NotBlankStr,
    ) -> Message | None:
        for m in self._messages:
            if m.channel == channel and str(m.id) == str(message_id):
                return m
        return None

    async def query(
        self,
        filter_spec: MessageFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Message, ...]:
        result = sorted(
            self._messages,
            key=lambda m: m.timestamp,
            reverse=True,
        )
        if filter_spec.channel is not None:
            result = [m for m in result if m.channel == filter_spec.channel]
        return tuple(result[offset : offset + limit])

    async def purge_before(self, threshold: datetime) -> int:
        before = len(self._messages)
        self._messages = [m for m in self._messages if m.timestamp >= threshold]
        return before - len(self._messages)

    async def delete(self, message_id: NotBlankStr) -> bool:
        for i, m in enumerate(self._messages):
            if str(m.id) == message_id:
                self._messages.pop(i)
                return True
        return False


class FakeLifecycleEventRepository:
    """In-memory lifecycle event repository for tests."""

    def __init__(self) -> None:
        self._events: list[AgentLifecycleEvent] = []

    async def save(self, event: AgentLifecycleEvent) -> None:
        self._events.append(event)

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        event_type: LifecycleEventType | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[AgentLifecycleEvent, ...]:
        result = self._events
        if agent_id is not None:
            result = [e for e in result if e.agent_id == agent_id]
        if event_type is not None:
            result = [e for e in result if e.event_type == event_type]
        if since is not None:
            result = [e for e in result if e.timestamp >= since]
        result = sorted(result, key=lambda e: e.timestamp, reverse=True)
        if limit is not None:
            result = result[:limit]
        return tuple(result)


class FakeTaskMetricRepository:
    """In-memory task metric repository for tests."""

    def __init__(self) -> None:
        self._records: list[TaskMetricRecord] = []

    async def save(self, record: TaskMetricRecord) -> None:
        self._records.append(record)

    async def query(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = 100,
    ) -> tuple[TaskMetricRecord, ...]:
        result = self._records
        if agent_id is not None:
            result = [r for r in result if r.agent_id == agent_id]
        if since is not None:
            result = [r for r in result if r.completed_at >= since]
        if until is not None:
            result = [r for r in result if r.completed_at <= until]
        return tuple(result[:limit])


class FakeCollaborationMetricRepository:
    """In-memory collaboration metric repository for tests."""

    def __init__(self) -> None:
        self._records: list[CollaborationMetricRecord] = []

    async def save(self, record: CollaborationMetricRecord) -> None:
        self._records.append(record)

    async def query(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: AwareDatetime | None = None,
        limit: int = 100,
    ) -> tuple[CollaborationMetricRecord, ...]:
        result = self._records
        if agent_id is not None:
            result = [r for r in result if r.agent_id == agent_id]
        if since is not None:
            result = [r for r in result if r.recorded_at >= since]
        return tuple(result[:limit])


class FakeParkedContextRepository:
    """In-memory parked context repository for tests."""

    def __init__(self) -> None:
        self._contexts: dict[str, ParkedContext] = {}

    async def save(self, entity: ParkedContext) -> None:
        self._contexts[entity.id] = entity

    async def get(self, entity_id: NotBlankStr) -> ParkedContext | None:
        return self._contexts.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        ordered = sorted(self._contexts.values(), key=lambda c: c.id)
        return tuple(ordered[offset : offset + limit])

    async def get_by_approval(self, approval_id: NotBlankStr) -> ParkedContext | None:
        for ctx in self._contexts.values():
            if ctx.approval_id == approval_id:
                return ctx
        return None

    async def get_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        matching = sorted(
            (ctx for ctx in self._contexts.values() if ctx.agent_id == agent_id),
            key=lambda c: c.id,
        )
        return tuple(matching[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._contexts.pop(entity_id, None) is not None


class FakeAuditRepository:
    """In-memory audit entry repository for tests.

    Extra attributes exposed for lifecycle-helper tests:

    * ``purge_calls`` -- counter incremented on every ``purge_before``
      call so retention-loop tests can assert the tick actually invoked
      the repository.
    * ``raise_on_purge`` -- optional exception raised from
      ``purge_before`` to exercise the repo-error branch.
    """

    def __init__(self) -> None:
        self._entries: dict[str, AuditEntry] = {}
        self.purge_calls: int = 0
        self.raise_on_purge: BaseException | None = None

    async def append(self, entry: AuditEntry) -> None:
        if entry.id in self._entries:
            msg = f"Duplicate audit entry {entry.id!r}"
            raise DuplicateRecordError(msg)
        self._entries[entry.id] = entry

    async def query(
        self,
        filter_spec: AuditFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[AuditEntry, ...]:
        if limit < 1:
            msg = "limit must be >= 1"
            raise QueryError(msg)
        results = sorted(
            self._entries.values(),
            key=lambda e: e.timestamp,
            reverse=True,
        )
        if filter_spec.agent_id is not None:
            results = [e for e in results if e.agent_id == filter_spec.agent_id]
        if filter_spec.action_type is not None:
            results = [e for e in results if e.action_type == filter_spec.action_type]
        if filter_spec.verdict is not None:
            results = [e for e in results if e.verdict == filter_spec.verdict]
        if filter_spec.risk_level is not None:
            results = [e for e in results if e.risk_level == filter_spec.risk_level]
        if filter_spec.since is not None:
            results = [e for e in results if e.timestamp >= filter_spec.since]
        if filter_spec.until is not None:
            results = [e for e in results if e.timestamp <= filter_spec.until]
        return tuple(results[offset : offset + limit])

    async def purge_before(self, cutoff: AwareDatetime) -> int:
        self.purge_calls += 1
        if self.raise_on_purge is not None:
            raise self.raise_on_purge
        before = len(self._entries)
        self._entries = {
            k: e for k, e in self._entries.items() if e.timestamp >= cutoff
        }
        return before - len(self._entries)


# FakeDecisionRepository lives in a sibling module to keep this file
# under the 800-line limit.  Re-exported here so existing test imports
# (``from tests.unit.api.fakes import FakeDecisionRepository``) keep
# working.
from tests.unit.api.fakes_decisions import (  # noqa: E402
    FakeDecisionRepository as FakeDecisionRepository,  # noqa: PLC0414
)


class FakeApiKeyRepository:
    """In-memory API key repository for tests."""

    def __init__(self) -> None:
        self._keys: dict[str, ApiKey] = {}

    async def save(self, entity: ApiKey) -> None:
        self._keys[entity.id] = entity

    async def get(self, entity_id: str) -> ApiKey | None:
        return self._keys.get(entity_id)

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        for key in self._keys.values():
            if key.key_hash == key_hash:
                return key
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        keys = sorted(self._keys.values(), key=lambda k: k.id)
        return tuple(keys[offset : offset + limit])

    async def query(
        self,
        filter_spec: ApiKeyFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        results = list(self._keys.values())
        if filter_spec.user_id is not None:
            results = [k for k in results if k.user_id == filter_spec.user_id]
        if filter_spec.revoked_only:
            results = [k for k in results if k.revoked]
        results.sort(key=lambda k: k.id)
        return tuple(results[offset : offset + limit])

    async def count(self, filter_spec: ApiKeyFilterSpec) -> int:
        results = list(self._keys.values())
        if filter_spec.user_id is not None:
            results = [k for k in results if k.user_id == filter_spec.user_id]
        if filter_spec.revoked_only:
            results = [k for k in results if k.revoked]
        return len(results)

    async def delete(self, entity_id: str) -> bool:
        return self._keys.pop(entity_id, None) is not None


class FakeCheckpointRepository:
    """In-memory checkpoint repository for tests."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, Checkpoint] = {}

    async def append(self, checkpoint: Checkpoint) -> None:
        self._checkpoints[checkpoint.id] = checkpoint

    async def get_latest(
        self,
        *,
        execution_id: str | None = None,
        task_id: str | None = None,
    ) -> Checkpoint | None:
        if execution_id is None and task_id is None:
            msg = "At least one of execution_id or task_id is required"
            raise ValueError(msg)
        candidates = list(self._checkpoints.values())
        if execution_id is not None:
            candidates = [c for c in candidates if c.execution_id == execution_id]
        if task_id is not None:
            candidates = [c for c in candidates if c.task_id == task_id]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.turn_number)

    async def query(
        self,
        filter_spec: CheckpointFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Checkpoint, ...]:
        candidates = list(self._checkpoints.values())
        if filter_spec.execution_id is not None:
            candidates = [
                c for c in candidates if c.execution_id == filter_spec.execution_id
            ]
        if filter_spec.task_id is not None:
            candidates = [c for c in candidates if c.task_id == filter_spec.task_id]
        candidates.sort(key=lambda c: c.turn_number, reverse=True)
        return tuple(candidates[offset : offset + limit])

    async def purge_before(self, threshold: datetime) -> int:
        before = len(self._checkpoints)
        self._checkpoints = {
            k: v for k, v in self._checkpoints.items() if v.created_at >= threshold
        }
        return before - len(self._checkpoints)

    async def delete_by_execution(self, execution_id: str) -> int:
        to_delete = [
            k for k, v in self._checkpoints.items() if v.execution_id == execution_id
        ]
        for k in to_delete:
            del self._checkpoints[k]
        return len(to_delete)


class FakeFlightRecorderFrameRepository:
    """In-memory flight-recorder frame repository for tests."""

    def __init__(self) -> None:
        self._frames: dict[str, FlightRecorderFrame] = {}

    async def append(self, frame: FlightRecorderFrame) -> None:
        if frame.id in self._frames:
            msg = f"Flight recorder frame {frame.id!r} already exists"
            raise DuplicateRecordError(msg)
        # Mirror the backend ``UNIQUE (execution_id, turn_index)`` index so
        # the fake catches duplicate-turn writes the same way real backends do.
        for existing in self._frames.values():
            if (
                existing.execution_id == frame.execution_id
                and existing.turn_index == frame.turn_index
            ):
                msg = (
                    f"Flight recorder frame for execution {frame.execution_id!r}"
                    f" turn {frame.turn_index} already exists"
                )
                raise DuplicateRecordError(msg)
        self._frames[frame.id] = frame

    async def append_many(self, frames: tuple[FlightRecorderFrame, ...]) -> None:
        # Atomic batch: validate every frame first, then commit; on conflict
        # the in-memory state is unchanged so the fake matches the backend
        # rollback semantics on ``UNIQUE`` collisions.
        if not frames:
            return
        seen_ids: set[str] = set()
        seen_turns: set[tuple[str, int]] = set()
        for frame in frames:
            if frame.id in self._frames or frame.id in seen_ids:
                msg = f"Flight recorder frame {frame.id!r} already exists"
                raise DuplicateRecordError(msg)
            key = (frame.execution_id, frame.turn_index)
            existing_clash = any(
                existing.execution_id == frame.execution_id
                and existing.turn_index == frame.turn_index
                for existing in self._frames.values()
            )
            if existing_clash or key in seen_turns:
                msg = (
                    f"Flight recorder batch ({len(frames)} frames) failed:"
                    " duplicate id or (execution_id, turn_index)"
                )
                raise DuplicateRecordError(msg)
            seen_ids.add(frame.id)
            seen_turns.add(key)
        for frame in frames:
            self._frames[frame.id] = frame

    async def query(
        self,
        filter_spec: FlightRecorderFrameFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[FlightRecorderFrame, ...]:
        candidates = self._filtered(filter_spec)
        candidates.sort(key=lambda f: (f.turn_index, f.timestamp), reverse=True)
        return tuple(candidates[offset : offset + limit])

    async def get_aggregate(
        self,
        filter_spec: FlightRecorderFrameFilterSpec,
    ) -> FlightRecorderFrameAggregate:
        candidates = self._filtered(filter_spec)
        if not candidates:
            return FlightRecorderFrameAggregate()
        # Pick the latest row by (timestamp DESC, turn_index DESC) so
        # ``latest_timestamp`` reflects the most recent recorded
        # activity, not the row that happens to carry the highest
        # ``turn_index``. Matches the SQL backends.
        latest = max(candidates, key=lambda f: (f.timestamp, f.turn_index))
        # lint-allow: currency-aggregation -- single budget; test fake
        total_cost = sum(f.cost for f in candidates)
        return FlightRecorderFrameAggregate(
            total_cost=total_cost,
            max_turn_index=max(f.turn_index for f in candidates),
            latest_timestamp=latest.timestamp,
            latest_execution_id=latest.execution_id,
        )

    async def purge_before(self, threshold: AwareDatetime) -> int:
        before = len(self._frames)
        self._frames = {
            k: v for k, v in self._frames.items() if v.timestamp >= threshold
        }
        return before - len(self._frames)

    def _filtered(
        self, filter_spec: FlightRecorderFrameFilterSpec
    ) -> list[FlightRecorderFrame]:
        candidates = list(self._frames.values())
        if filter_spec.execution_id is not None:
            candidates = [
                f for f in candidates if f.execution_id == filter_spec.execution_id
            ]
        if filter_spec.task_id is not None:
            candidates = [f for f in candidates if f.task_id == filter_spec.task_id]
        if filter_spec.agent_id is not None:
            candidates = [f for f in candidates if f.agent_id == filter_spec.agent_id]
        if filter_spec.turn_index_min is not None:
            candidates = [
                f for f in candidates if f.turn_index >= filter_spec.turn_index_min
            ]
        if filter_spec.turn_index_max is not None:
            candidates = [
                f for f in candidates if f.turn_index <= filter_spec.turn_index_max
            ]
        return candidates


class FakeRedTeamReportArchiveRepository:
    """In-memory red-team report archive for tests.

    Mirrors the backend single-shot-per-execution invariant (the primary
    key on ``execution_id``) so the api fixture exercises the same
    duplicate-append behaviour the SQL backends enforce.
    """

    def __init__(self) -> None:
        self._records: dict[str, RedTeamReportRecord] = {}

    async def append(self, record: RedTeamReportRecord) -> None:
        if record.execution_id in self._records:
            msg = (
                f"Red-team report for execution {record.execution_id!r} already exists"
            )
            raise DuplicateRecordError(msg)
        self._records[record.execution_id] = record

    async def query(
        self,
        filter_spec: RedTeamReportFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[RedTeamReportRecord, ...]:
        candidates = self._filtered(filter_spec)
        candidates.sort(key=lambda r: (r.recorded_at, r.execution_id), reverse=True)
        return tuple(candidates[offset : offset + limit])

    async def purge_before(self, threshold: AwareDatetime) -> int:
        before = len(self._records)
        self._records = {
            k: v for k, v in self._records.items() if v.recorded_at >= threshold
        }
        return before - len(self._records)

    def _filtered(
        self, filter_spec: RedTeamReportFilterSpec
    ) -> list[RedTeamReportRecord]:
        candidates = list(self._records.values())
        if filter_spec.execution_id is not None:
            candidates = [
                r for r in candidates if r.execution_id == filter_spec.execution_id
            ]
        if filter_spec.task_id is not None:
            candidates = [r for r in candidates if r.task_id == filter_spec.task_id]
        if filter_spec.verdict is not None:
            candidates = [r for r in candidates if r.verdict == filter_spec.verdict]
        return candidates


class FakeHeartbeatRepository:
    """In-memory heartbeat repository for tests."""

    def __init__(self) -> None:
        self._heartbeats: dict[str, Heartbeat] = {}

    async def save(self, heartbeat: Heartbeat) -> None:
        self._heartbeats[heartbeat.execution_id] = heartbeat

    async def get(self, execution_id: NotBlankStr) -> Heartbeat | None:
        return self._heartbeats.get(execution_id)

    async def get_stale(
        self,
        threshold: AwareDatetime,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Heartbeat, ...]:
        stale = [
            h for h in self._heartbeats.values() if h.last_heartbeat_at < threshold
        ]
        stale.sort(key=lambda h: (h.last_heartbeat_at, h.execution_id))
        return tuple(stale[offset : offset + limit])

    async def delete(self, execution_id: NotBlankStr) -> bool:
        return self._heartbeats.pop(execution_id, None) is not None


class FakeArtifactRepository:
    """In-memory artifact repository for tests."""

    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}

    async def save(self, entity: Artifact) -> None:
        self._artifacts[entity.id] = entity

    async def save_returning_outcome(self, artifact: Artifact) -> bool:
        created = artifact.id not in self._artifacts
        self._artifacts[artifact.id] = artifact
        return created

    async def get(self, entity_id: NotBlankStr) -> Artifact | None:
        return self._artifacts.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Artifact, ...]:
        from synthorg.persistence.artifact_protocol import ArtifactFilterSpec

        return await self.query(
            ArtifactFilterSpec(),
            limit=limit,
            offset=offset,
        )

    async def query(
        self,
        filter_spec: ArtifactFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Artifact, ...]:
        result = list(self._artifacts.values())
        if filter_spec.task_id is not None:
            result = [a for a in result if a.task_id == filter_spec.task_id]
        if filter_spec.created_by is not None:
            result = [a for a in result if a.created_by == filter_spec.created_by]
        if filter_spec.artifact_type is not None:
            result = [a for a in result if a.type == filter_spec.artifact_type]
        # Match the SQLite repo contract (``ORDER BY id``) so tests
        # asserting list order do not depend on dict insertion order.
        result.sort(key=lambda a: a.id)
        return tuple(result[offset : offset + limit])

    async def count(self, filter_spec: ArtifactFilterSpec) -> int:
        result = list(self._artifacts.values())
        if filter_spec.task_id is not None:
            result = [a for a in result if a.task_id == filter_spec.task_id]
        if filter_spec.created_by is not None:
            result = [a for a in result if a.created_by == filter_spec.created_by]
        if filter_spec.artifact_type is not None:
            result = [a for a in result if a.type == filter_spec.artifact_type]
        return len(result)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._artifacts.pop(entity_id, None) is not None


class FakeProjectWorkspaceRepository:
    """In-memory project_workspaces repository for tests."""

    def __init__(self) -> None:
        self._rows: dict[str, ProjectWorkspace] = {}

    async def save(self, entity: ProjectWorkspace) -> None:
        self._rows[entity.project_id] = entity

    async def get(self, entity_id: NotBlankStr) -> ProjectWorkspace | None:
        return self._rows.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[ProjectWorkspace, ...]:
        result = sorted(self._rows.values(), key=lambda r: r.project_id)
        return tuple(result[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None


class FakeCodebaseStructureMapRepository:
    """In-memory codebase_structure_maps repository for tests."""

    def __init__(self) -> None:
        self._rows: dict[str, CodebaseStructureMap] = {}

    async def save(self, entity: CodebaseStructureMap) -> None:
        self._rows[entity.project_id] = entity

    async def get(self, entity_id: NotBlankStr) -> CodebaseStructureMap | None:
        return self._rows.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[CodebaseStructureMap, ...]:
        result = sorted(self._rows.values(), key=lambda r: r.project_id)
        return tuple(result[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None

    def clear(self) -> None:
        """Reset all stored structure maps for test isolation."""
        self._rows.clear()


class FakeProjectEnvironmentRepository:
    """In-memory project_environments repository for tests."""

    def __init__(self) -> None:
        self._rows: dict[str, ProjectEnvironment] = {}

    async def save(self, entity: ProjectEnvironment) -> None:
        self._rows[entity.project_id] = entity

    async def get(self, entity_id: NotBlankStr) -> ProjectEnvironment | None:
        return self._rows.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[ProjectEnvironment, ...]:
        result = sorted(self._rows.values(), key=lambda r: r.project_id)
        return tuple(result[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None

    def clear(self) -> None:
        """Reset all stored environments for test isolation."""
        self._rows.clear()


class FakeDocsRepository:
    """In-memory living-doc metadata repository for tests."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], DocMetadata] = {}

    async def save(self, entity: DocMetadata) -> None:
        self._rows[(entity.project_id, entity.slug)] = entity

    async def get(
        self, entity_id: tuple[NotBlankStr, NotBlankStr]
    ) -> DocMetadata | None:
        return self._rows.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[DocMetadata, ...]:
        result = sorted(
            self._rows.values(),
            key=lambda r: (r.updated_at, r.project_id, r.slug),
            reverse=True,
        )
        return tuple(result[offset : offset + limit])

    async def delete(self, entity_id: tuple[NotBlankStr, NotBlankStr]) -> bool:
        return self._rows.pop(entity_id, None) is not None

    async def query(
        self,
        filter_spec: DocsFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[DocMetadata, ...]:
        rows = [
            r for r in self._rows.values() if r.project_id == filter_spec.project_id
        ]
        if filter_spec.doc_type is not None:
            rows = [r for r in rows if r.doc_type == filter_spec.doc_type]
        if filter_spec.tag is not None:
            rows = [r for r in rows if filter_spec.tag in r.tags]
        if filter_spec.updated_since is not None:
            rows = [r for r in rows if r.updated_at >= filter_spec.updated_since]
        rows.sort(key=lambda r: (r.updated_at, r.slug), reverse=True)
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: DocsFilterSpec) -> int:
        rows = [
            r for r in self._rows.values() if r.project_id == filter_spec.project_id
        ]
        if filter_spec.doc_type is not None:
            rows = [r for r in rows if r.doc_type == filter_spec.doc_type]
        if filter_spec.tag is not None:
            rows = [r for r in rows if filter_spec.tag in r.tags]
        if filter_spec.updated_since is not None:
            rows = [r for r in rows if r.updated_at >= filter_spec.updated_since]
        return len(rows)


class FakeProjectBrainRepository:
    """In-memory append-only project-brain repository for tests.

    Stores every revision; current state is the latest revision per
    ``entry_id``. Mirrors the SQLite/Postgres repositories' observable
    behaviour for the API-fixture wiring and brain controller tests.
    """

    def __init__(self) -> None:
        self._rows: list[BrainEntry] = []
        self._indexed: dict[tuple[str, str], int] = {}

    @classmethod
    def reopen(cls, other: FakeProjectBrainRepository) -> FakeProjectBrainRepository:
        """Build a fresh repo over another's persisted rows.

        Models a process restart: a new repository object reads the durable
        store that survived. ``BrainEntry`` is frozen, so copying the row list
        and the index map is enough to make this an independent instance --
        mutating one repo never reaches the other.

        Args:
            other: The repository whose persisted state should be carried over.

        Returns:
            A new repository seeded with copies of *other*'s rows and index.
        """
        fresh = cls()
        fresh._rows = list(other._rows)
        fresh._indexed = dict(other._indexed)
        return fresh

    def _for_entry(self, project_id: str, entry_id: str) -> list[BrainEntry]:
        return [
            row
            for row in self._rows
            if row.project_id == project_id and row.entry_id == entry_id
        ]

    async def mark_indexed(
        self,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        revision: int,
    ) -> None:
        self._indexed[(project_id, entry_id)] = revision

    async def indexed_revisions(
        self, project_id: NotBlankStr
    ) -> dict[NotBlankStr, int]:
        return {
            NotBlankStr(entry_id): revision
            for (pid, entry_id), revision in self._indexed.items()
            if pid == project_id
        }

    async def append(self, event: BrainEntry) -> None:
        clash = any(
            row.entry_id == event.entry_id and row.revision == event.revision
            for row in self._rows
        )
        if clash:
            msg = f"Brain revision conflict for {event.entry_id!r} r{event.revision}"
            raise BrainEntryRevisionConflictError(msg)
        self._rows.append(event)

    async def append_with_next_revision(self, entry: BrainEntry) -> BrainEntry:
        existing = self._for_entry(entry.project_id, entry.entry_id)
        next_rev = max((row.revision for row in existing), default=0) + 1
        stored = entry.model_copy(update={"revision": next_rev})
        self._rows.append(stored)
        return stored

    async def get(
        self, entity_id: tuple[NotBlankStr, NotBlankStr, int]
    ) -> BrainEntry | None:
        project_id, entry_id, revision = entity_id
        for row in self._rows:
            if (
                row.project_id == project_id
                and row.entry_id == entry_id
                and row.revision == revision
            ):
                return row
        return None

    async def get_current(
        self, project_id: NotBlankStr, entry_id: NotBlankStr
    ) -> BrainEntry | None:
        rows = self._for_entry(project_id, entry_id)
        if not rows:
            return None
        return max(rows, key=lambda r: r.revision)

    def _current_rows(self, filter_spec: BrainFilterSpec) -> list[BrainEntry]:
        latest: dict[str, BrainEntry] = {}
        for row in self._rows:
            if row.project_id != filter_spec.project_id:
                continue
            prior = latest.get(row.entry_id)
            if prior is None or row.revision > prior.revision:
                latest[row.entry_id] = row
        rows = [row for row in latest.values() if _brain_matches(row, filter_spec)]
        rows.sort(key=lambda r: (r.recorded_at, r.entry_id), reverse=True)
        return rows

    async def list_current(
        self,
        filter_spec: BrainFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[BrainEntry, ...]:
        rows = self._current_rows(filter_spec)
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: BrainFilterSpec) -> int:
        return len(self._current_rows(filter_spec))

    async def history(
        self,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[BrainEntry, ...]:
        rows = sorted(self._for_entry(project_id, entry_id), key=lambda r: r.revision)
        return tuple(rows[offset : offset + limit])

    async def query(
        self,
        filter_spec: BrainFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[BrainEntry, ...]:
        rows = [
            row
            for row in self._rows
            if row.project_id == filter_spec.project_id
            and _brain_matches(row, filter_spec)
        ]
        rows.sort(key=lambda r: (r.recorded_at, r.revision), reverse=True)
        return tuple(rows[offset : offset + limit])

    async def purge_before(self, threshold: datetime) -> int:
        current_keys = {
            (row.project_id, row.entry_id, row.revision)
            for row in (
                max(group, key=lambda r: r.revision)
                for group in _group_by_entry(self._rows)
            )
        }
        before = len(self._rows)
        self._rows = [
            row
            for row in self._rows
            if (row.project_id, row.entry_id, row.revision) in current_keys
            or row.recorded_at >= threshold
        ]
        return before - len(self._rows)


def _brain_matches(row: BrainEntry, filter_spec: BrainFilterSpec) -> bool:
    """Return whether *row* satisfies the non-project filter dimensions.

    Returns:
        ``True`` when every set filter field matches the row.
    """
    if filter_spec.entry_kind is not None and row.entry_kind != filter_spec.entry_kind:
        return False
    if filter_spec.status is not None and row.status != filter_spec.status:
        return False
    if filter_spec.author is not None and row.author != filter_spec.author:
        return False
    if filter_spec.tag is not None and filter_spec.tag not in row.tags:
        return False
    if (
        filter_spec.related_task_id is not None
        and filter_spec.related_task_id not in row.related_task_ids
    ):
        return False
    return not (
        filter_spec.updated_since is not None
        and row.recorded_at < filter_spec.updated_since
    )


def _group_by_entry(rows: list[BrainEntry]) -> list[list[BrainEntry]]:
    """Group brain rows by ``(project_id, entry_id)``.

    Returns:
        A list of per-entry revision groups.
    """
    groups: dict[tuple[str, str], list[BrainEntry]] = {}
    for row in rows:
        groups.setdefault((row.project_id, row.entry_id), []).append(row)
    return list(groups.values())


class FakeProjectRepository:
    """In-memory project repository for tests."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}

    async def create(self, project: Project) -> None:
        if str(project.id) in self._projects:
            msg = f"Project with id {project.id!r} already exists"
            raise DuplicateRecordError(msg)
        self._projects[str(project.id)] = project

    async def update(self, project: Project) -> None:
        if str(project.id) not in self._projects:
            msg = f"No project with id {project.id!r}"
            raise RecordNotFoundError(msg)
        self._projects[str(project.id)] = project

    async def save(self, entity: Project) -> None:
        self._projects[str(entity.id)] = entity

    async def get(self, entity_id: NotBlankStr) -> Project | None:
        return self._projects.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Project, ...]:
        result = sorted(self._projects.values(), key=lambda p: p.id)
        return tuple(result[offset : offset + limit])

    async def query(
        self,
        filter_spec: ProjectFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Project, ...]:
        result = sorted(self._projects.values(), key=lambda p: p.id)
        if filter_spec.status is not None:
            result = [p for p in result if p.status == filter_spec.status]
        if filter_spec.lead is not None:
            result = [p for p in result if p.lead == filter_spec.lead]
        return tuple(result[offset : offset + limit])

    async def count(self, filter_spec: ProjectFilterSpec) -> int:
        result = list(self._projects.values())
        if filter_spec.status is not None:
            result = [p for p in result if p.status == filter_spec.status]
        if filter_spec.lead is not None:
            result = [p for p in result if p.lead == filter_spec.lead]
        return len(result)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._projects.pop(entity_id, None) is not None


class FakeArtifactStorage:
    """In-memory artifact storage backend for tests.

    Supports error injection: set ``raise_too_large`` or
    ``raise_storage_full`` to ``True`` to simulate limit errors.
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self.raise_too_large: bool = False
        self.raise_storage_full: bool = False

    @property
    def backend_name(self) -> str:
        return "fake"

    async def store(self, artifact_id: str, content: bytes) -> int:
        if self.raise_too_large:
            from synthorg.core.persistence_errors import (
                ArtifactTooLargeError,
            )

            msg = "too large"
            raise ArtifactTooLargeError(msg)
        if self.raise_storage_full:
            from synthorg.core.persistence_errors import (
                ArtifactStorageFullError,
            )

            msg = "storage full"
            raise ArtifactStorageFullError(msg)
        self._store[artifact_id] = content
        return len(content)

    async def retrieve(self, artifact_id: str) -> bytes:
        if artifact_id not in self._store:
            from synthorg.core.persistence_errors import (
                RecordNotFoundError,
            )

            msg = f"Artifact content not found: {artifact_id!r}"
            raise RecordNotFoundError(msg)
        return self._store[artifact_id]

    async def delete(self, artifact_id: str) -> bool:
        return self._store.pop(artifact_id, None) is not None

    async def exists(self, artifact_id: str) -> bool:
        return artifact_id in self._store

    async def total_size(self) -> int:
        return sum(len(v) for v in self._store.values())


class FakeAgentStateRepository:
    """In-memory agent state repository for tests."""

    def __init__(self) -> None:
        self._states: dict[str, AgentRuntimeState] = {}

    async def save(self, entity: AgentRuntimeState) -> None:
        self._states[entity.agent_id] = entity

    async def get(self, entity_id: NotBlankStr) -> AgentRuntimeState | None:
        return self._states.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[AgentRuntimeState, ...]:
        ordered = sorted(self._states.values(), key=lambda s: s.agent_id)
        return tuple(ordered[offset : offset + limit])

    async def get_active(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[AgentRuntimeState, ...]:
        active = [s for s in self._states.values() if s.status != ExecutionStatus.IDLE]
        active.sort(key=lambda s: (-s.last_activity_at.timestamp(), s.agent_id))
        return tuple(active[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._states.pop(entity_id, None) is not None


class FakePersonalityPresetRepository:
    """In-memory custom personality preset repository for tests."""

    def __init__(self) -> None:
        self._presets: dict[str, Preset] = {}

    async def save(self, entity: Preset) -> None:
        existing = self._presets.get(entity.name)
        created_at = existing.created_at if existing else entity.created_at
        self._presets[entity.name] = entity.model_copy(
            update={"created_at": created_at},
        )

    async def get(self, entity_id: NotBlankStr) -> Preset | None:
        return self._presets.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Preset, ...]:
        rows = tuple(p for _, p in sorted(self._presets.items()))
        return rows[offset : offset + limit]

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Preset, ...]:
        return await self.list_items(limit=limit, offset=offset)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._presets.pop(entity_id, None) is not None

    async def count(self, filter_spec: object | None = None) -> int:
        return len(self._presets)


class FakeSettingsRepository:
    """In-memory namespaced settings repository for tests."""

    def __init__(self) -> None:
        from synthorg.persistence.settings_protocol import SettingRow

        self._SettingRow = SettingRow
        self._store: dict[tuple[str, str], tuple[str, str]] = {}

    def _row(self, namespace: str, key: str, value: str, ts: str) -> SettingRow:
        return self._SettingRow(
            namespace=NotBlankStr(namespace),
            key=NotBlankStr(key),
            value=value,
            updated_at=ts,
        )

    async def get(self, entity_id: tuple[str, str]) -> SettingRow | None:
        namespace, key = entity_id
        existing = self._store.get((namespace, key))
        if existing is None:
            return None
        value, ts = existing
        return self._row(namespace, key, value, ts)

    async def get_namespace(self, namespace: str) -> tuple[SettingRow, ...]:
        return tuple(
            self._row(ns, k, v, ts)
            for (ns, k), (v, ts) in sorted(self._store.items())
            if ns == namespace
        )

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[SettingRow, ...]:
        rows = [
            self._row(ns, k, v, ts) for (ns, k), (v, ts) in sorted(self._store.items())
        ]
        return tuple(rows[offset : offset + limit])

    async def save(self, entity: SettingRow) -> None:
        self._store = {
            **self._store,
            (entity.namespace, entity.key): (entity.value, entity.updated_at),
        }

    async def set_if_unchanged(
        self,
        entity: SettingRow,
        expected_updated_at: str | None = None,
    ) -> bool:
        if expected_updated_at is not None:
            current = self._store.get((entity.namespace, entity.key))
            if current is None:
                if expected_updated_at != "":
                    return False
            elif current[1] != expected_updated_at:
                return False
        self._store = {
            **self._store,
            (entity.namespace, entity.key): (entity.value, entity.updated_at),
        }
        return True

    async def set_many(
        self,
        items: Sequence[SettingRow],
        *,
        expected_updated_at_map: Mapping[tuple[str, str], str] | None = None,
    ) -> bool:
        if not items:
            return True
        cas_map = expected_updated_at_map or {}
        draft = dict(self._store)
        for row in items:
            expected = cas_map.get((row.namespace, row.key))
            if expected is not None:
                current = draft.get((row.namespace, row.key))
                if current is None:
                    if expected != "":
                        return False
                elif current[1] != expected:
                    return False
            draft[(row.namespace, row.key)] = (row.value, row.updated_at)
        self._store = draft
        return True

    async def delete(self, entity_id: tuple[str, str]) -> bool:
        if entity_id in self._store:
            self._store = {k: v for k, v in self._store.items() if k != entity_id}
            return True
        return False

    async def delete_namespace(self, namespace: str) -> int:
        keys = [k for k in self._store if k[0] == namespace]
        self._store = {k: v for k, v in self._store.items() if k[0] != namespace}
        return len(keys)

    async def delete_namespace_returning_keys(
        self,
        namespace: str,
    ) -> tuple[NotBlankStr, ...]:
        keys = tuple(NotBlankStr(k[1]) for k in self._store if k[0] == namespace)
        self._store = {k: v for k, v in self._store.items() if k[0] != namespace}
        return keys


class FakeMessageBus:
    """In-memory message bus for tests."""

    def __init__(self) -> None:
        self._running = False
        self._channels: list[Channel] = []

    def clear(self) -> None:
        """Reset all in-memory state for test isolation."""
        self._channels.clear()
        self._running = True

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def health_check(self) -> bool:
        return self._running

    async def publish(
        self,
        message: Message,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        pass

    async def send_direct(
        self,
        message: Message,
        *,
        recipient: str,
        ttl_seconds: float | None = None,
    ) -> None:
        pass

    async def publish_batch(
        self,
        messages: Sequence[Message],
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        pass

    async def subscribe(self, channel_name: str, subscriber_id: str) -> Subscription:
        from datetime import UTC

        return Subscription(
            channel_name=NotBlankStr(channel_name),
            subscriber_id=NotBlankStr(subscriber_id),
            subscribed_at=datetime.now(UTC),
        )

    async def unsubscribe(self, channel_name: str, subscriber_id: str) -> None:
        pass

    async def receive(
        self,
        channel_name: str,
        subscriber_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> DeliveryEnvelope | None:
        """Block up to *timeout* seconds before returning ``None``.

        The real ``MessageBus.receive`` blocks on an internal queue
        until a message arrives or *timeout* elapses.  Returning
        ``None`` immediately makes the API bridge's polling loop
        (``bus_bridge._poll_channel``) a busy-wait that spins at
        max speed, scheduling hundreds of thousands of
        ``asyncio.sleep(0)`` continuations per second and inflating
        event-loop teardown cost.  ``asyncio.Event().wait()`` with
        ``wait_for`` yields cleanly for the full timeout, so the
        loop runs at most once per timeout window and cancellation
        is a single ``asyncio.CancelledError`` on a suspended task.
        """
        if timeout is None:
            # No timeout -- block forever (until cancelled).
            await asyncio.Event().wait()
            return None
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.Event().wait(), timeout=timeout)
        return None

    async def create_channel(self, channel: Channel) -> Channel:
        self._channels.append(channel)
        return channel

    async def get_channel(self, channel_name: str) -> Channel:
        for ch in self._channels:
            if ch.name == channel_name:
                return ch
        msg = f"Channel {channel_name!r} not found"
        raise ValueError(msg)

    async def list_channels(self) -> tuple[Channel, ...]:
        return tuple(self._channels)

    async def get_channel_history(
        self,
        channel_name: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        return ()


# FakePersistenceBackend lives in a sibling module to keep this file
# under the 800-line limit. It depends on many Fake*Repository classes
# defined in THIS module, and this module is re-exported for backward
# compatibility via
# ``from tests.unit.api.fakes import FakePersistenceBackend``.
#
# The naive "from fakes_backend import ..." at module load time causes
# a circular-import failure when a caller imports `fakes_backend`
# FIRST (that module's own `from fakes import Fake*Repository` pulls
# `fakes` in mid-load, which then re-enters `fakes_backend` before
# `FakePersistenceBackend` is defined).
#
# PEP 562 module-level ``__getattr__`` solves it: the attribute is
# resolved on first access rather than at module load. By then
# ``fakes_backend`` has already completed its own initialisation.
def __getattr__(name: str) -> Any:
    """Lazy re-export of ``FakePersistenceBackend`` (see note above)."""
    if name == "FakePersistenceBackend":
        from tests.unit.api.fakes_backend import (
            FakePersistenceBackend,
        )

        return FakePersistenceBackend
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
