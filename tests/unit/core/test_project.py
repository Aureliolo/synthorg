"""Tests for the Project domain model."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ProjectStatus
from synthorg.core.project import Project
from tests.unit.core.conftest import ProjectFactory

pytestmark = pytest.mark.timeout(30)

# ── Helpers ──────────────────────────────────────────────────────

_PROJECT_KWARGS: dict[str, object] = {
    "id": "proj-456",
    "name": "Auth System",
}


def _make_project(**overrides: object) -> Project:
    """Create a Project with sensible defaults, applying overrides."""
    kwargs = {**_PROJECT_KWARGS, **overrides}
    return Project(**kwargs)  # type: ignore[arg-type]


# ── Construction & Defaults ──────────────────────────────────────


@pytest.mark.unit
class TestProjectConstruction:
    def test_minimal_valid_project(self) -> None:
        project = _make_project()
        assert project.id == "proj-456"
        assert project.name == "Auth System"

    def test_all_fields_set(self) -> None:
        project = Project(
            id="proj-789",
            name="Full Project",
            description="A complete project",
            team=("agent-1", "agent-2"),
            lead="agent-1",
            task_ids=("task-1", "task-2"),
            deadline="2026-12-31",
            budget=100.0,
            status=ProjectStatus.ACTIVE,
        )
        assert project.description == "A complete project"
        assert project.team == ("agent-1", "agent-2")
        assert project.lead == "agent-1"
        assert project.task_ids == ("task-1", "task-2")
        assert project.deadline == "2026-12-31"
        assert project.budget == 100.0
        assert project.status is ProjectStatus.ACTIVE

    def test_default_values(self) -> None:
        project = _make_project()
        assert project.description == ""
        assert project.team == ()
        assert project.lead is None
        assert project.task_ids == ()
        assert project.deadline is None
        assert project.budget == 0.0
        assert project.status is ProjectStatus.PLANNING


# ── String Validation ────────────────────────────────────────────


@pytest.mark.unit
class TestProjectStringValidation:
    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_project(id="")

    def test_whitespace_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_project(id="   ")

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_project(name="   ")

    def test_whitespace_lead_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_project(lead="   ")

    def test_empty_deadline_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="deadline must not be whitespace-only"
        ):
            _make_project(deadline="")

    def test_whitespace_deadline_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="deadline must not be whitespace-only"
        ):
            _make_project(deadline="   ")

    def test_invalid_deadline_format_rejected(self) -> None:
        with pytest.raises(ValidationError, match="valid ISO 8601"):
            _make_project(deadline="not-a-date")

    def test_valid_iso_date_deadline_accepted(self) -> None:
        project = _make_project(deadline="2026-12-31")
        assert project.deadline == "2026-12-31"

    def test_valid_iso_datetime_deadline_accepted(self) -> None:
        project = _make_project(deadline="2026-12-31T23:59:59")
        assert project.deadline == "2026-12-31T23:59:59"

    def test_whitespace_team_member_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_project(team=("agent-1", "   "))

    def test_empty_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            _make_project(task_ids=("task-1", ""))


# ── Duplicate Validation ─────────────────────────────────────────


@pytest.mark.unit
class TestProjectDuplicates:
    def test_duplicate_team_members_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate entries in team"):
            _make_project(team=("agent-1", "agent-2", "agent-1"))

    def test_duplicate_task_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate entries in task_ids"):
            _make_project(task_ids=("task-1", "task-2", "task-1"))

    def test_unique_team_members(self) -> None:
        project = _make_project(team=("agent-1", "agent-2", "agent-3"))
        assert len(project.team) == 3

    def test_unique_task_ids(self) -> None:
        project = _make_project(task_ids=("task-1", "task-2", "task-3"))
        assert len(project.task_ids) == 3


# ── Budget ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestProjectBudget:
    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_project(budget=-1.0)

    def test_zero_budget_allowed(self) -> None:
        project = _make_project(budget=0.0)
        assert project.budget == 0.0

    def test_positive_budget(self) -> None:
        project = _make_project(budget=500.0)
        assert project.budget == 500.0


# ── Immutability ─────────────────────────────────────────────────


@pytest.mark.unit
class TestProjectImmutability:
    def test_frozen(self) -> None:
        project = _make_project()
        with pytest.raises(ValidationError):
            project.name = "New Name"  # type: ignore[misc]

    def test_frozen_status(self) -> None:
        project = _make_project()
        with pytest.raises(ValidationError):
            project.status = ProjectStatus.ACTIVE  # type: ignore[misc]


# ── Factory ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestProjectFactory:
    def test_factory(self) -> None:
        project = ProjectFactory.build()
        assert isinstance(project, Project)
        assert isinstance(project.status, ProjectStatus)


# ── Serialization ────────────────────────────────────────────────


@pytest.mark.unit
class TestProjectSerialization:
    def test_json_roundtrip(self) -> None:
        project = Project(
            id="proj-rt",
            name="Roundtrip Test",
            description="Test JSON roundtrip",
            team=("agent-1", "agent-2"),
            lead="agent-1",
            task_ids=("task-1",),
            deadline="2026-06-30",
            budget=25.0,
            status=ProjectStatus.ACTIVE,
        )
        json_str = project.model_dump_json()
        restored = Project.model_validate_json(json_str)
        assert restored.id == project.id
        assert restored.name == project.name
        assert restored.team == project.team
        assert restored.lead == project.lead
        assert restored.task_ids == project.task_ids
        assert restored.budget == project.budget
        assert restored.status is project.status

    def test_model_dump(self) -> None:
        project = _make_project()
        dumped = project.model_dump()
        assert dumped["id"] == "proj-456"
        assert dumped["status"] == "planning"


# ── Fixture ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestProjectFixtures:
    def test_sample_project_fixture(self, sample_project: Project) -> None:
        assert sample_project.id == "proj-456"
        assert sample_project.name == "Auth System"
        assert sample_project.lead == "engineering_lead"
        assert len(sample_project.team) == 2
