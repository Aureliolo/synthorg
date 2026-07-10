# module-kind: service
"""Real org-state read model for the Chief of Staff chat context.

Aggregates the organisation's in-flight work (in-progress / in-review
tasks, active projects, and the pending approval queue) into a frozen
:class:`OrgStateSnapshot` so the Chief of Staff can answer "what is the
org working on?" from observed state rather than inferring idleness.

Read-only over the existing task / project / approval repositories. The
reader is fail-loud: it either builds the full snapshot (every read
succeeds) or a genuine backend fault propagates; there is no per-source
degradation. The absence of the whole read model (persistence
disconnected) is handled one layer up, in the request helper.
"""

import asyncio
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import CitedRecord
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import COS_ORG_STATE_READ
from synthorg.persistence.project_protocol import ProjectFilterSpec, ProjectRepository
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository

logger = get_logger(__name__)


def _first_leaf(exc: BaseException) -> BaseException:
    """Descend an ``ExceptionGroup`` to its first non-group leaf.

    Returns:
        The first leaf exception (``exc`` itself when it is not a group).
    """
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return _first_leaf(exc.exceptions[0])
    return exc


# ── Digests ───────────────────────────────────────────────────────


class TaskDigest(BaseModel):
    """One in-flight task, projected for the chat context.

    Attributes:
        task_id: Task identifier.
        title: Task title.
        status: Current task status.
        project: Owning project id.
        assigned_to: Assignee agent id, or ``None`` when unassigned.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr
    title: NotBlankStr
    status: TaskStatus
    project: NotBlankStr
    assigned_to: NotBlankStr | None = None


class ProjectDigest(BaseModel):
    """One active project, projected for the chat context.

    Attributes:
        project_id: Project identifier.
        name: Project display name.
        status: Current project status.
        lead: Project lead agent id, or ``None`` when unassigned.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr
    name: NotBlankStr
    status: ProjectStatus
    lead: NotBlankStr | None = None


class ApprovalDigest(BaseModel):
    """One pending approval, projected for the chat context.

    Attributes:
        approval_id: Approval-queue item identifier.
        title: Approval request title.
        action_type: What kind of action awaits approval.
        risk_level: Assessed risk level.
        requested_by: Agent or system that raised the request.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approval_id: NotBlankStr
    title: NotBlankStr
    action_type: NotBlankStr
    risk_level: ApprovalRiskLevel
    requested_by: NotBlankStr


class OrgStateSnapshot(BaseModel):
    """Point-in-time view of the organisation's in-flight work.

    Each section carries a bounded sample of records plus the full total,
    so the chat context can say "showing N of M" without listing every
    row. ``has_work`` is the authoritative "the org is doing something"
    signal the chat prompt grounds its anti-idleness answer on.

    Attributes:
        in_progress_tasks: Sampled in-progress tasks.
        in_progress_total: Full count of in-progress tasks.
        in_review_tasks: Sampled in-review tasks.
        in_review_total: Full count of in-review tasks.
        active_projects: Sampled active projects.
        active_projects_total: Full count of active projects.
        pending_approvals: Sampled pending approvals.
        pending_approvals_total: Full count of pending approvals.
        read_at: When the snapshot was assembled.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    in_progress_tasks: tuple[TaskDigest, ...] = ()
    in_progress_total: int = Field(default=0, ge=0)
    in_review_tasks: tuple[TaskDigest, ...] = ()
    in_review_total: int = Field(default=0, ge=0)
    active_projects: tuple[ProjectDigest, ...] = ()
    active_projects_total: int = Field(default=0, ge=0)
    pending_approvals: tuple[ApprovalDigest, ...] = ()
    pending_approvals_total: int = Field(default=0, ge=0)
    read_at: AwareDatetime

    @model_validator(mode="after")
    def _validate_sample_within_total(self) -> Self:
        """Reject a snapshot whose sample outnumbers its reported total.

        Each section carries a bounded sample plus the full count; the
        sample is a prefix of the total, so its length can never exceed
        it. The reader clamps the total to the sample length to absorb a
        query/count read race, so a violation here can only come from a
        construction bug (a transcribed / mismatched pair).

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: When any section's sample is larger than its total.
        """
        pairs = (
            (self.in_progress_tasks, self.in_progress_total),
            (self.in_review_tasks, self.in_review_total),
            (self.active_projects, self.active_projects_total),
            (self.pending_approvals, self.pending_approvals_total),
        )
        for sample, total in pairs:
            if len(sample) > total:
                msg = f"sample size {len(sample)} exceeds reported total {total}"
                raise ValueError(msg)
        return self

    @computed_field
    @property
    def has_work(self) -> bool:
        """Whether any task or active project is in flight.

        A convenience predicate for consumers of the snapshot: true when
        a task or active-project total is non-zero. Pending approvals are
        queued decisions, not active work, so they are excluded. The chat
        prompt path derives its own per-domain source tags separately
        (see ``free_form_sources``); this field is not read there.
        """
        return bool(
            self.in_progress_total or self.in_review_total or self.active_projects_total
        )


# ── Reader ────────────────────────────────────────────────────────


class OrgStateReader:
    """Assembles an :class:`OrgStateSnapshot` from live repositories.

    Args:
        task_repo: Task repository (read via status-filtered query).
        project_repo: Project repository (read via status-filtered query).
        approval_store: Approval store (pending items listed directly).
        max_items_per_section: Upper bound on records sampled per section.
        clock: Clock seam for the ``read_at`` stamp.
    """

    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        project_repo: ProjectRepository,
        approval_store: ApprovalStoreProtocol,
        max_items_per_section: int,
        clock: Clock | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._project_repo = project_repo
        self._approval_store = approval_store
        self._max_items = max_items_per_section
        self._clock = clock or SystemClock()

    async def read(self) -> OrgStateSnapshot:
        """Read all four surfaces concurrently into one snapshot.

        The four reads fan out over an ``asyncio.TaskGroup`` so snapshot
        latency is bounded by the slowest surface rather than the sum of
        all four (each surface itself does a bounded ``query`` plus a
        ``count``). No read is wrapped: a backend fault propagates
        (fail-loud) rather than yielding a partial or fabricated-empty
        snapshot. The ``TaskGroup``'s ``ExceptionGroup`` is unwrapped to
        its first leaf so the caller's typed-error handling (the SSE
        error frame / RFC 9457 mapper) sees the original ``DomainError``
        instead of an opaque group.

        Returns:
            The assembled :class:`OrgStateSnapshot`.

        Raises:
            BaseException: An interpreter-critical leaf
                (``MemoryError`` / ``RecursionError``) is re-raised via the
                group first; otherwise the first leaf of a fan-out failure
                is re-raised from the ``ExceptionGroup``.
        """
        try:
            async with asyncio.TaskGroup() as tg:
                in_progress = tg.create_task(self._read_tasks(TaskStatus.IN_PROGRESS))
                in_review = tg.create_task(self._read_tasks(TaskStatus.IN_REVIEW))
                projects = tg.create_task(self._read_projects())
                approvals = tg.create_task(self._read_approvals())
        except* Exception as eg:
            # A mixed group may carry an interpreter-critical leaf
            # (MemoryError / RecursionError) alongside ordinary faults;
            # re-raise the whole group so that never gets buried behind the
            # ordinary first leaf _first_leaf would otherwise surface.
            reraise_critical(eg)
            leaf = _first_leaf(eg)
            raise leaf from eg

        ip_digests, ip_total = in_progress.result()
        ir_digests, ir_total = in_review.result()
        proj_digests, proj_total = projects.result()
        appr_digests, appr_total = approvals.result()

        snapshot = OrgStateSnapshot(
            in_progress_tasks=ip_digests,
            in_progress_total=ip_total,
            in_review_tasks=ir_digests,
            in_review_total=ir_total,
            active_projects=proj_digests,
            active_projects_total=proj_total,
            pending_approvals=appr_digests,
            pending_approvals_total=appr_total,
            read_at=self._clock.now(),
        )
        logger.info(
            COS_ORG_STATE_READ,
            in_progress=ip_total,
            in_review=ir_total,
            active_projects=proj_total,
            pending_approvals=appr_total,
        )
        return snapshot

    async def _read_tasks(
        self,
        status: TaskStatus,
    ) -> tuple[tuple[TaskDigest, ...], int]:
        """Read a bounded task sample plus the full count for one status.

        Returns:
            The sampled digests and the total matching-task count.
        """
        spec = TaskFilterSpec(status=status)
        tasks = await self._task_repo.query(spec, limit=self._max_items)
        total = await self._task_repo.count(spec)
        digests = tuple(_task_digest(task) for task in tasks)
        # Clamp against a query/count read race: if rows transitioned out
        # between the two reads the count can trail the sample, and a total
        # below the rendered sample would drop the "showing N of M" note.
        return digests, max(total, len(digests))

    async def _read_projects(self) -> tuple[tuple[ProjectDigest, ...], int]:
        """Read a bounded active-project sample plus the full count.

        Returns:
            The sampled digests and the total active-project count.
        """
        spec = ProjectFilterSpec(status=ProjectStatus.ACTIVE)
        projects = await self._project_repo.query(spec, limit=self._max_items)
        total = await self._project_repo.count(spec)
        digests = tuple(_project_digest(project) for project in projects)
        return digests, max(total, len(digests))

    async def _read_approvals(self) -> tuple[tuple[ApprovalDigest, ...], int]:
        """Read pending approvals, sampling the head and keeping the total.

        ``list_items`` applies lazy expiration, so the total counts only
        genuinely-still-pending items.

        Returns:
            The sampled digests and the total pending-approval count.
        """
        pending = await self._approval_store.list_items(status=ApprovalStatus.PENDING)
        sample = pending[: self._max_items]
        return tuple(_approval_digest(item) for item in sample), len(pending)


# ── Entity to digest ──────────────────────────────────────────────


def _task_digest(task: Task) -> TaskDigest:
    """Project a task onto its digest.

    Returns:
        The :class:`TaskDigest`.
    """
    return TaskDigest(
        task_id=NotBlankStr(str(task.id)),
        title=task.title,
        status=task.status,
        project=task.project,
        assigned_to=task.assigned_to,
    )


def _project_digest(project: Project) -> ProjectDigest:
    """Project a project onto its digest.

    Returns:
        The :class:`ProjectDigest`.
    """
    return ProjectDigest(
        project_id=NotBlankStr(str(project.id)),
        name=project.name,
        status=project.status,
        lead=project.lead,
    )


def _approval_digest(item: ApprovalItem) -> ApprovalDigest:
    """Project an approval item onto its digest.

    Returns:
        The :class:`ApprovalDigest`.
    """
    return ApprovalDigest(
        approval_id=NotBlankStr(str(item.id)),
        title=item.title,
        action_type=item.action_type,
        risk_level=item.risk_level,
        requested_by=item.requested_by,
    )


# ── Rendering + citations ─────────────────────────────────────────


def _task_line(digest: TaskDigest) -> str:
    """Render one task bullet.

    Returns:
        The bullet line.
    """
    assignee = digest.assigned_to or "unassigned"
    return (
        f"- {digest.title} [{digest.status.value}] "
        f"(project {digest.project}, assigned to {assignee})"
    )


def _project_line(digest: ProjectDigest) -> str:
    """Render one project bullet.

    Returns:
        The bullet line.
    """
    lead = digest.lead or "unassigned"
    return f"- {digest.name} [{digest.status.value}] (lead {lead})"


def _approval_line(digest: ApprovalDigest) -> str:
    """Render one approval bullet.

    Returns:
        The bullet line.
    """
    return (
        f"- {digest.title} [{digest.risk_level.value} risk] "
        f"({digest.action_type}, requested by {digest.requested_by})"
    )


def _section(title: str, lines: tuple[str, ...], total: int) -> str:
    """Render one titled section with a truncation note when sampled.

    Returns:
        The rendered section text.
    """
    header = f"{title} ({total}):"
    if total == 0:
        return f"{header} none"
    body = "\n".join(lines)
    if len(lines) < total:
        body += f"\n(showing {len(lines)} of {total})"
    return f"{header}\n{body}"


def format_org_state(state: OrgStateSnapshot) -> str:
    """Render an org-state snapshot into a plain-text context block.

    Returns text only; the caller wraps the whole block in the
    untrusted-content fence, since every title / name here is human- or
    agent-authored.

    Returns:
        The rendered multi-section block.
    """
    sections = (
        _section(
            "In-progress tasks",
            tuple(_task_line(d) for d in state.in_progress_tasks),
            state.in_progress_total,
        ),
        _section(
            "In-review tasks",
            tuple(_task_line(d) for d in state.in_review_tasks),
            state.in_review_total,
        ),
        _section(
            "Active projects",
            tuple(_project_line(d) for d in state.active_projects),
            state.active_projects_total,
        ),
        _section(
            "Pending approvals",
            tuple(_approval_line(d) for d in state.pending_approvals),
            state.pending_approvals_total,
        ),
    )
    return "\n\n".join(sections)


def cited_records(state: OrgStateSnapshot) -> tuple[CitedRecord, ...]:
    """Build structured citations for the records the answer can draw on.

    Returns:
        One :class:`CitedRecord` per sampled task / project / approval.
    """
    tasks = tuple(
        CitedRecord(
            kind="task",
            record_id=task.task_id,
            label=task.title,
            status=NotBlankStr(task.status.value),
        )
        for task in (*state.in_progress_tasks, *state.in_review_tasks)
    )
    projects = tuple(
        CitedRecord(
            kind="project",
            record_id=project.project_id,
            label=project.name,
            status=NotBlankStr(project.status.value),
        )
        for project in state.active_projects
    )
    approvals = tuple(
        CitedRecord(
            kind="approval",
            record_id=approval.approval_id,
            label=approval.title,
            status=NotBlankStr(ApprovalStatus.PENDING.value),
        )
        for approval in state.pending_approvals
    )
    return (*tasks, *projects, *approvals)
