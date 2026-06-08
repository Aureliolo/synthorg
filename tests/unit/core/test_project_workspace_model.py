"""Unit tests for the ``ProjectWorkspace`` domain model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.project_enums import GitBackendType
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


def _workspace(**overrides: object) -> ProjectWorkspace:
    base: dict[str, object] = {
        "project_id": NotBlankStr("proj-1"),
        "workspace_path": NotBlankStr("/data/projects/proj-1"),
        "git_backend_kind": GitBackendType.EMBEDDED,
        "created_at": datetime(2026, 5, 19, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 19, tzinfo=UTC),
    }
    base.update(overrides)
    return ProjectWorkspace(**base)  # type: ignore[arg-type]


class TestProjectWorkspaceModel:
    def test_minimal_construction_defaults(self) -> None:
        ws = _workspace()
        assert ws.project_id == "proj-1"
        assert ws.git_backend_kind is GitBackendType.EMBEDDED
        assert ws.remote_ref is None
        assert ws.default_branch == "main"

    def test_is_frozen(self) -> None:
        ws = _workspace()
        with pytest.raises(ValidationError):
            ws.project_id = NotBlankStr("other")  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            _workspace(unexpected="x")

    def test_git_backend_kind_round_trips(self) -> None:
        ws = _workspace(git_backend_kind=GitBackendType.LOCAL_PATH)
        dumped = ws.model_dump()
        assert dumped["git_backend_kind"] == "local_path"
        assert ProjectWorkspace.model_validate(dumped).git_backend_kind is (
            GitBackendType.LOCAL_PATH
        )

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _workspace(created_at=datetime(2026, 5, 19))  # noqa: DTZ001

    def test_blank_project_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _workspace(project_id="   ")

    def test_remote_ref_optional_set(self) -> None:
        ws = _workspace(
            git_backend_kind=GitBackendType.EXTERNAL_REMOTE,
            remote_ref=NotBlankStr("github-main"),
        )
        assert ws.remote_ref == "github-main"
