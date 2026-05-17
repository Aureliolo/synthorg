"""Unit tests for :class:`ProjectService`.

Mirrors the structure of ``tests/unit/api/auth/test_user_service.py``: a
fake in-memory repository implements the :class:`ProjectRepository`
protocol, the service is constructed against it, and tests assert
behaviour + the audit event fired on each mutation.
"""

import pytest
import structlog

from synthorg.api.services.project_service import ProjectService
from synthorg.core.enums import ProjectStatus
from synthorg.core.persistence_errors import DuplicateRecordError, RecordNotFoundError
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.api import (
    API_PROJECT_CREATED,
    API_PROJECT_DELETED,
    API_PROJECT_UPDATED,
)
from synthorg.persistence.project_protocol import (
    ProjectFilterSpec,
)

pytestmark = pytest.mark.unit


class _FakeProjectRepo:
    """In-memory ProjectRepository used as a test stub."""

    def __init__(self) -> None:
        self._rows: dict[str, Project] = {}

    async def create(self, project: Project) -> None:
        if project.id in self._rows:
            msg = f"Project with id {project.id!r} already exists"
            raise DuplicateRecordError(msg)
        self._rows[project.id] = project

    async def update(self, project: Project) -> None:
        if project.id not in self._rows:
            msg = f"No project with id {project.id!r}"
            raise RecordNotFoundError(msg)
        self._rows[project.id] = project

    async def save(self, project: Project) -> None:
        self._rows[project.id] = project

    async def get(self, project_id: NotBlankStr) -> Project | None:
        return self._rows.get(project_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Project, ...]:
        return await self.query(ProjectFilterSpec(), limit=limit, offset=offset)

    async def query(
        self,
        filter_spec: ProjectFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Project, ...]:
        rows = sorted(self._rows.values(), key=lambda p: p.id)
        if filter_spec.status is not None:
            rows = [p for p in rows if p.status == filter_spec.status]
        if filter_spec.lead is not None:
            rows = [p for p in rows if p.lead == filter_spec.lead]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: ProjectFilterSpec) -> int:
        rows = list(self._rows.values())
        if filter_spec.status is not None:
            rows = [p for p in rows if p.status == filter_spec.status]
        if filter_spec.lead is not None:
            rows = [p for p in rows if p.lead == filter_spec.lead]
        return len(rows)

    async def delete(self, project_id: NotBlankStr) -> bool:
        return self._rows.pop(project_id, None) is not None


def _make_project(
    *,
    project_id: str = "proj-1",
    name: str = "Test project",
    lead: str = "agent-lead",
) -> Project:
    return Project(
        id=NotBlankStr(project_id),
        name=NotBlankStr(name),
        description="",
        team=(),
        lead=NotBlankStr(lead),
        deadline=None,
        budget=0.0,
    )


async def test_create_persists_and_emits_api_project_created() -> None:
    """``create`` saves the project and fires ``API_PROJECT_CREATED``.

    Asserts the structured kwargs (``project_id``, ``status``, ``lead``)
    so that a future refactor that drops a field from the audit payload
    is caught. Monitoring filters key on these fields.
    """
    repo = _FakeProjectRepo()
    service = ProjectService(repo=repo)
    project = _make_project()

    with structlog.testing.capture_logs() as logs:
        result = await service.create(project)

    assert result == project
    fetched = await repo.get(project.id)
    assert fetched == project
    created_events = [log for log in logs if log["event"] == API_PROJECT_CREATED]
    assert len(created_events) == 1, (
        f"expected exactly one {API_PROJECT_CREATED} event in {logs}"
    )
    event = created_events[0]
    assert event["project_id"] == project.id
    assert event["status"] == project.status.value
    assert event["lead"] == project.lead


async def test_update_persists_and_emits_api_project_updated() -> None:
    """``update`` saves the project and fires ``API_PROJECT_UPDATED``.

    Asserts the structured kwargs (``project_id``, ``status``) to pin
    the audit payload shape against accidental field removal.
    """
    repo = _FakeProjectRepo()
    service = ProjectService(repo=repo)
    project = _make_project()
    await repo.save(project)

    updated = project.model_copy(update={"name": NotBlankStr("Renamed")})
    with structlog.testing.capture_logs() as logs:
        await service.update(updated)

    fetched = await repo.get(project.id)
    assert fetched is not None
    assert fetched.name == "Renamed"
    updated_events = [log for log in logs if log["event"] == API_PROJECT_UPDATED]
    assert len(updated_events) == 1
    event = updated_events[0]
    assert event["project_id"] == updated.id
    assert event["status"] == updated.status.value


@pytest.mark.parametrize(
    ("mode", "event_name", "expected_exc", "expected_error_type"),
    [
        (
            "create-duplicate",
            API_PROJECT_CREATED,
            DuplicateRecordError,
            "DuplicateRecordError",
        ),
        (
            "update-missing",
            API_PROJECT_UPDATED,
            RecordNotFoundError,
            "RecordNotFoundError",
        ),
    ],
    ids=["create-duplicate", "update-missing"],
)
async def test_mutation_failures_emit_single_warning_audit(
    mode: str,
    event_name: str,
    expected_exc: type[Exception],
    expected_error_type: str,
) -> None:
    """Failure paths fire exactly one WARNING audit; INFO success must not fire.

    Pins both atomic-mutation contracts (``create`` rejecting an
    existing id, ``update`` rejecting a missing id) -- the success
    INFO event must NOT fire, AND a single WARNING with
    ``error_type`` + ``project_id`` must fire so incident triage can
    correlate the failure (CLAUDE.md `## Logging`).
    """
    repo = _FakeProjectRepo()
    service = ProjectService(repo=repo)

    if mode == "create-duplicate":
        project = _make_project()
        await service.create(project)
        call = service.create(project)
    else:  # "update-missing"
        project = _make_project(project_id="proj-ghost")
        call = service.update(project)

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(expected_exc),
    ):
        await call

    audits = [log for log in logs if log["event"] == event_name]
    info_audits = [log for log in audits if log.get("log_level") == "info"]
    warning_audits = [log for log in audits if log.get("log_level") == "warning"]
    assert info_audits == [], (
        f"no INFO success audit may fire on {mode} -- got {info_audits}"
    )
    assert len(warning_audits) == 1, (
        f"expected one WARNING audit on {mode} -- got {warning_audits}"
    )
    assert warning_audits[0]["error_type"] == expected_error_type
    assert warning_audits[0]["project_id"] == project.id


async def test_delete_returns_true_and_emits_api_project_deleted() -> None:
    """``delete`` returns ``True`` and emits ``API_PROJECT_DELETED``.

    Asserts ``project_id`` in the audit payload.
    """
    repo = _FakeProjectRepo()
    service = ProjectService(repo=repo)
    project = _make_project()
    await repo.save(project)

    with structlog.testing.capture_logs() as logs:
        deleted = await service.delete(project.id)

    assert deleted is True
    assert await repo.get(project.id) is None
    deleted_events = [log for log in logs if log["event"] == API_PROJECT_DELETED]
    assert len(deleted_events) == 1
    assert deleted_events[0]["project_id"] == project.id


async def test_delete_returns_false_for_missing_and_does_not_emit_event() -> None:
    """Missing project: ``delete`` returns ``False``, no audit fired."""
    repo = _FakeProjectRepo()
    service = ProjectService(repo=repo)

    with structlog.testing.capture_logs() as logs:
        deleted = await service.delete(NotBlankStr("proj-missing"))

    assert deleted is False
    assert not any(log["event"] == API_PROJECT_DELETED for log in logs)


async def test_get_passes_through_to_repo() -> None:
    """``get`` is a thin pass-through; no audit fired."""
    repo = _FakeProjectRepo()
    service = ProjectService(repo=repo)
    project = _make_project()
    await repo.save(project)

    fetched = await service.get(project.id)
    assert fetched == project
    assert await service.get(NotBlankStr("proj-missing")) is None


async def test_list_projects_filters_by_status_and_lead() -> None:
    """``list_projects`` honours both filters and is order-stable."""
    repo = _FakeProjectRepo()
    service = ProjectService(repo=repo)
    p1 = _make_project(project_id="proj-1", lead="agent-a")
    p2 = _make_project(project_id="proj-2", lead="agent-b")
    # ``p3`` is non-PLANNING -- otherwise the status-filter assertion
    # below could pass even if ``list_projects`` ignored ``status``
    # entirely (every project would default to PLANNING).
    p3 = _make_project(project_id="proj-3", lead="agent-c").model_copy(
        update={"status": ProjectStatus.ACTIVE},
    )
    await repo.save(p1)
    await repo.save(p2)
    await repo.save(p3)

    by_lead = await service.list_projects(
        status=None,
        lead=NotBlankStr("agent-a"),
    )
    assert by_lead == (p1,)

    all_planning = await service.list_projects(
        status=ProjectStatus.PLANNING,
        lead=None,
    )
    # The PLANNING filter must exclude the ACTIVE row.  Asserting the
    # exact tuple also pins the ordering contract documented on
    # ``ProjectRepository.list_projects`` (ascending id).
    assert all_planning == (p1, p2)

    only_active = await service.list_projects(
        status=ProjectStatus.ACTIVE,
        lead=None,
    )
    assert only_active == (p3,)


@pytest.mark.parametrize(
    ("seed_count", "requested_limit"),
    [
        (5, 2),
        (5, 3),
        (10, 1),
        (3, 10),
    ],
)
async def test_list_projects_respects_limit(
    seed_count: int,
    requested_limit: int,
) -> None:
    """``list_projects`` truncates to *limit* when more rows exist."""
    repo = _FakeProjectRepo()
    service = ProjectService(repo=repo)
    for i in range(seed_count):
        await repo.save(_make_project(project_id=f"proj-{i:02d}"))

    rows = await service.list_projects(
        status=None,
        lead=None,
        limit=requested_limit,
    )
    assert len(rows) <= requested_limit
    assert len(rows) == min(seed_count, requested_limit)
