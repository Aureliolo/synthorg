"""In-memory fake implementations for API unit tests."""

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from synthorg.budget.cost_record import CostRecord
from synthorg.communication.channel import Channel
from synthorg.communication.message import Message
from synthorg.core.artifact import Artifact
from synthorg.core.auth.models import ApiKey
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ArtifactType,
    ExecutionStatus,
    ProjectStatus,
    TaskStatus,
)
from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.core.project import Project
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_state import AgentRuntimeState
from synthorg.engine.checkpoint.models import Checkpoint, Heartbeat
from synthorg.hr.enums import LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    TaskMetricRecord,
)
from synthorg.persistence.preset_protocol import PresetListRow, PresetRow
from synthorg.security.models import AuditEntry, AuditVerdictStr
from synthorg.security.timeout.parked_context import ParkedContext


class FakeTaskRepository:
    """In-memory task repository for tests."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    async def save(self, task: Task) -> None:
        self._tasks[task.id] = task

    async def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        result = self._filtered(status, assigned_to, project)
        if limit is None:
            return tuple(result[offset:])
        return tuple(result[offset : offset + limit])

    async def count_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
    ) -> int:
        return len(self._filtered(status, assigned_to, project))

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

    async def delete(self, task_id: str) -> bool:
        return self._tasks.pop(task_id, None) is not None


class FakeCostRecordRepository:
    """In-memory cost record repository for tests."""

    def __init__(self) -> None:
        self._records: list[CostRecord] = []

    async def save(self, record: CostRecord) -> None:
        self._records.append(record)

    async def query(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> tuple[CostRecord, ...]:
        result = self._records
        if agent_id is not None:
            result = [r for r in result if r.agent_id == agent_id]
        if task_id is not None:
            result = [r for r in result if r.task_id == task_id]
        return tuple(result)

    async def aggregate(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> float:
        records = await self.query(agent_id=agent_id, task_id=task_id)
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
        channel: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        if limit is not None and limit < 1:
            msg = f"limit must be a positive integer, got {limit}"
            raise QueryError(msg)
        result = sorted(
            (m for m in self._messages if m.channel == channel),
            key=lambda m: m.timestamp,
            reverse=True,
        )
        if limit is not None:
            result = result[:limit]
        return tuple(result)

    async def query(
        self,
        filter_spec: Any,
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

    async def delete(self, message_id: str) -> bool:
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
        agent_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[TaskMetricRecord, ...]:
        result = self._records
        if agent_id is not None:
            result = [r for r in result if r.agent_id == agent_id]
        if since is not None:
            result = [r for r in result if r.completed_at >= since]
        if until is not None:
            result = [r for r in result if r.completed_at <= until]
        return tuple(result)


class FakeCollaborationMetricRepository:
    """In-memory collaboration metric repository for tests."""

    def __init__(self) -> None:
        self._records: list[CollaborationMetricRecord] = []

    async def save(self, record: CollaborationMetricRecord) -> None:
        self._records.append(record)

    async def query(
        self,
        *,
        agent_id: str | None = None,
        since: datetime | None = None,
    ) -> tuple[CollaborationMetricRecord, ...]:
        result = self._records
        if agent_id is not None:
            result = [r for r in result if r.agent_id == agent_id]
        if since is not None:
            result = [r for r in result if r.recorded_at >= since]
        return tuple(result)


class FakeParkedContextRepository:
    """In-memory parked context repository for tests."""

    def __init__(self) -> None:
        self._contexts: dict[str, ParkedContext] = {}

    async def save(self, context: ParkedContext) -> None:
        self._contexts[context.id] = context

    async def get(self, parked_id: str) -> ParkedContext | None:
        return self._contexts.get(parked_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        ordered = sorted(self._contexts.values(), key=lambda c: c.id)
        return tuple(ordered[offset : offset + limit])

    async def get_by_approval(self, approval_id: str) -> ParkedContext | None:
        for ctx in self._contexts.values():
            if ctx.approval_id == approval_id:
                return ctx
        return None

    async def get_by_agent(self, agent_id: str) -> tuple[ParkedContext, ...]:
        return tuple(ctx for ctx in self._contexts.values() if ctx.agent_id == agent_id)

    async def delete(self, parked_id: str) -> bool:
        return self._contexts.pop(parked_id, None) is not None


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

    async def save(self, entry: AuditEntry) -> None:
        if entry.id in self._entries:
            msg = f"Duplicate audit entry {entry.id!r}"
            raise DuplicateRecordError(msg)
        self._entries[entry.id] = entry

    async def query(  # noqa: PLR0913
        self,
        *,
        agent_id: str | None = None,
        action_type: str | None = None,
        verdict: AuditVerdictStr | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> tuple[AuditEntry, ...]:
        if limit < 1:
            msg = "limit must be >= 1"
            raise QueryError(msg)
        if since is not None and until is not None and until < since:
            msg = "until must not be earlier than since"
            raise QueryError(msg)
        results = sorted(
            self._entries.values(),
            key=lambda e: e.timestamp,
            reverse=True,
        )
        if agent_id is not None:
            results = [e for e in results if e.agent_id == agent_id]
        if action_type is not None:
            results = [e for e in results if e.action_type == action_type]
        if verdict is not None:
            results = [e for e in results if e.verdict == verdict]
        if risk_level is not None:
            results = [e for e in results if e.risk_level == risk_level]
        if since is not None:
            results = [e for e in results if e.timestamp >= since]
        if until is not None:
            results = [e for e in results if e.timestamp <= until]
        return tuple(results[:limit])

    async def purge_before(self, cutoff: datetime) -> int:
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

    async def save(self, key: ApiKey) -> None:
        self._keys[key.id] = key

    async def get(self, key_id: str) -> ApiKey | None:
        return self._keys.get(key_id)

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        for key in self._keys.values():
            if key.key_hash == key_hash:
                return key
        return None

    async def list_by_user(self, user_id: str) -> tuple[ApiKey, ...]:
        return tuple(k for k in self._keys.values() if k.user_id == user_id)

    async def delete(self, key_id: str) -> bool:
        return self._keys.pop(key_id, None) is not None


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
        filter_spec: Any,
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


class FakeHeartbeatRepository:
    """In-memory heartbeat repository for tests."""

    def __init__(self) -> None:
        self._heartbeats: dict[str, Heartbeat] = {}

    async def save(self, heartbeat: Heartbeat) -> None:
        self._heartbeats[heartbeat.execution_id] = heartbeat

    async def get(self, execution_id: str) -> Heartbeat | None:
        return self._heartbeats.get(execution_id)

    async def get_stale(self, threshold: datetime) -> tuple[Heartbeat, ...]:
        stale = [
            h for h in self._heartbeats.values() if h.last_heartbeat_at < threshold
        ]
        stale.sort(key=lambda h: h.last_heartbeat_at)
        return tuple(stale)

    async def delete(self, execution_id: str) -> bool:
        return self._heartbeats.pop(execution_id, None) is not None


class FakeArtifactRepository:
    """In-memory artifact repository for tests."""

    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}

    async def save(self, artifact: Artifact) -> bool:
        created = artifact.id not in self._artifacts
        self._artifacts[artifact.id] = artifact
        return created

    async def get(self, artifact_id: NotBlankStr) -> Artifact | None:
        return self._artifacts.get(artifact_id)

    async def list_artifacts(
        self,
        *,
        task_id: NotBlankStr | None = None,
        created_by: NotBlankStr | None = None,
        artifact_type: ArtifactType | None = None,
    ) -> tuple[Artifact, ...]:
        result = list(self._artifacts.values())
        if task_id is not None:
            result = [a for a in result if a.task_id == task_id]
        if created_by is not None:
            result = [a for a in result if a.created_by == created_by]
        if artifact_type is not None:
            result = [a for a in result if a.type == artifact_type]
        # Match the SQLite repo contract (``ORDER BY id``) so tests
        # asserting list order do not depend on dict insertion order.
        result.sort(key=lambda a: a.id)
        return tuple(result)

    async def delete(self, artifact_id: NotBlankStr) -> bool:
        return self._artifacts.pop(artifact_id, None) is not None


class FakeProjectRepository:
    """In-memory project repository for tests."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}

    async def create(self, project: Project) -> None:
        if project.id in self._projects:
            msg = f"Project with id {project.id!r} already exists"
            raise DuplicateRecordError(msg)
        self._projects[project.id] = project

    async def update(self, project: Project) -> None:
        if project.id not in self._projects:
            msg = f"No project with id {project.id!r}"
            raise RecordNotFoundError(msg)
        self._projects[project.id] = project

    async def save(self, project: Project) -> None:
        self._projects[project.id] = project

    async def get(self, project_id: NotBlankStr) -> Project | None:
        return self._projects.get(project_id)

    async def list_projects(
        self,
        *,
        status: ProjectStatus | None = None,
        lead: NotBlankStr | None = None,
        limit: int = 100,
    ) -> tuple[Project, ...]:
        result = sorted(self._projects.values(), key=lambda p: p.id)
        if status is not None:
            result = [p for p in result if p.status == status]
        if lead is not None:
            result = [p for p in result if p.lead == lead]
        return tuple(result[:limit])

    async def delete(self, project_id: NotBlankStr) -> bool:
        return self._projects.pop(project_id, None) is not None


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

    async def save(self, state: AgentRuntimeState) -> None:
        self._states[state.agent_id] = state

    async def get(self, agent_id: str) -> AgentRuntimeState | None:
        return self._states.get(agent_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[AgentRuntimeState, ...]:
        ordered = sorted(self._states.values(), key=lambda s: s.agent_id)
        return tuple(ordered[offset : offset + limit])

    async def get_active(self) -> tuple[AgentRuntimeState, ...]:
        active = (s for s in self._states.values() if s.status != ExecutionStatus.IDLE)
        return tuple(sorted(active, key=lambda s: s.last_activity_at, reverse=True))

    async def delete(self, agent_id: str) -> bool:
        return self._states.pop(agent_id, None) is not None


class FakePersonalityPresetRepository:
    """In-memory custom personality preset repository for tests."""

    def __init__(self) -> None:
        self._presets: dict[str, PresetRow] = {}

    async def save(
        self,
        name: NotBlankStr,
        config_json: str,
        description: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        existing = self._presets.get(name)
        self._presets[name] = PresetRow(
            config_json,
            description,
            existing.created_at if existing else created_at,
            updated_at,
        )

    async def get(self, name: NotBlankStr) -> PresetRow | None:
        return self._presets.get(name)

    async def list_all(self, *, limit: int = 100) -> tuple[PresetListRow, ...]:
        rows = tuple(
            PresetListRow(name, *row) for name, row in sorted(self._presets.items())
        )
        return rows[:limit]

    async def delete(self, name: NotBlankStr) -> bool:
        return self._presets.pop(name, None) is not None

    async def count(self) -> int:
        return len(self._presets)


class FakeSettingsRepository:
    """In-memory namespaced settings repository for tests."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], tuple[str, str]] = {}

    async def get(self, namespace: str, key: str) -> tuple[str, str] | None:
        return self._store.get((namespace, key))

    async def get_namespace(self, namespace: str) -> tuple[tuple[str, str, str], ...]:
        result = [
            (k, v, ts)
            for (ns, k), (v, ts) in sorted(self._store.items())
            if ns == namespace
        ]
        return tuple(result)

    async def get_all(self) -> tuple[tuple[str, str, str, str], ...]:
        result = [(ns, k, v, ts) for (ns, k), (v, ts) in sorted(self._store.items())]
        return tuple(result)

    async def set(
        self,
        namespace: str,
        key: str,
        value: str,
        updated_at: str,
        *,
        expected_updated_at: str | None = None,
    ) -> bool:
        if expected_updated_at is not None:
            current = self._store.get((namespace, key))
            if current is None:
                if expected_updated_at != "":
                    return False
            elif current[1] != expected_updated_at:
                return False
        self._store = {
            **self._store,
            (namespace, key): (value, updated_at),
        }
        return True

    async def set_many(
        self,
        items: Sequence[tuple[str, str, str, str]],
        *,
        expected_updated_at_map: Mapping[tuple[str, str], str] | None = None,
    ) -> bool:
        if not items:
            return True
        cas_map = expected_updated_at_map or {}
        draft = dict(self._store)
        for namespace, key, value, updated_at in items:
            expected = cas_map.get((namespace, key))
            if expected is not None:
                current = draft.get((namespace, key))
                if current is None:
                    if expected != "":
                        return False
                elif current[1] != expected:
                    return False
            draft[(namespace, key)] = (value, updated_at)
        self._store = draft
        return True

    async def delete(self, namespace: str, key: str) -> bool:
        if (namespace, key) in self._store:
            self._store = {
                k: v for k, v in self._store.items() if k != (namespace, key)
            }
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

    async def subscribe(self, channel_name: str, subscriber_id: str) -> Any:
        return None

    async def unsubscribe(self, channel_name: str, subscriber_id: str) -> None:
        pass

    async def receive(
        self,
        channel_name: str,
        subscriber_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> Any:
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
