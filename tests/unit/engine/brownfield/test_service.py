"""Unit tests for ``BrownfieldImportService`` orchestration."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr
from synthorg.engine.brownfield.errors import BrownfieldWorkspaceNotEmptyError
from synthorg.engine.brownfield.models import CodebaseImportSubmission
from synthorg.engine.brownfield.scanner import build_structure_map_scanners
from synthorg.engine.brownfield.service import BrownfieldImportService
from synthorg.engine.brownfield.source_resolver import BrownfieldSourceResolver
from synthorg.engine.workspace.git_backend import GitBackend
from synthorg.engine.workspace.git_backend.protocol import ResolvedSource, SourceKind
from synthorg.engine.workspace.project_workspace_service import ProjectWorkspaceService
from synthorg.knowledge.service import KnowledgeService
from tests._shared import FakeClock, mock_of
from tests.unit.api.fakes import FakeCodebaseStructureMapRepository

pytestmark = pytest.mark.unit


def _workspace(path: Path) -> ProjectWorkspace:
    ts = datetime(2026, 5, 22, tzinfo=UTC)
    return ProjectWorkspace(
        project_id=NotBlankStr("proj-1"),
        workspace_path=NotBlankStr(str(path)),
        git_backend_kind="embedded",  # type: ignore[arg-type]
        default_branch=NotBlankStr("main"),
        created_at=ts,
        updated_at=ts,
    )


def _knowledge_source() -> SimpleNamespace:
    # The service only reads ``.source_id`` off the ingest return value.
    return SimpleNamespace(source_id="src-1")


def _build_service(
    tmp_path: Path,
    repo: FakeCodebaseStructureMapRepository,
) -> BrownfieldImportService:
    workspace = _workspace(tmp_path)
    ws_service = mock_of[ProjectWorkspaceService]()
    ws_service.get_or_provision.return_value = workspace
    git_backend = mock_of[GitBackend]()
    ws_service.git_backend = git_backend

    resolver = mock_of[BrownfieldSourceResolver]()
    resolver.resolve.return_value = ResolvedSource(
        fetch_url=NotBlankStr(str(tmp_path)),
        source_kind=SourceKind.LOCAL_PATH,
    )
    knowledge = mock_of[KnowledgeService]()
    knowledge.ingest.return_value = _knowledge_source()

    return BrownfieldImportService(
        workspace_service=ws_service,
        source_resolver=resolver,
        scanners=build_structure_map_scanners(),
        structure_map_repo=repo,
        knowledge_service=knowledge,
        clock=FakeClock(),
    )


def _submission(source_ref: str = "/src/legacy") -> CodebaseImportSubmission:
    return CodebaseImportSubmission(
        project_id=NotBlankStr("proj-1"),
        source_ref=NotBlankStr(source_ref),
        title=NotBlankStr("Legacy"),
        requested_by=NotBlankStr("operator"),
    )


def _seed_python_tree(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["httpx"]\n', encoding="utf-8"
    )
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "__init__.py").write_text("", encoding="utf-8")


class TestBrownfieldImportService:
    async def test_fresh_import_seeds_scans_and_indexes(self, tmp_path: Path) -> None:
        _seed_python_tree(tmp_path)
        repo = FakeCodebaseStructureMapRepository()
        service = _build_service(tmp_path, repo)

        result = await service.import_codebase(_submission())

        assert result.unchanged is False
        assert result.module_count >= 1
        assert result.knowledge_source_id == "src-1"
        stored = await repo.get(NotBlankStr("proj-1"))
        assert stored is not None
        assert stored.source_ref == "/src/legacy"

    async def test_reimport_same_source_short_circuits(self, tmp_path: Path) -> None:
        _seed_python_tree(tmp_path)
        repo = FakeCodebaseStructureMapRepository()
        service = _build_service(tmp_path, repo)

        first = await service.import_codebase(_submission())
        second = await service.import_codebase(_submission())

        assert second.unchanged is True
        assert second.content_hash == first.content_hash
        assert second.knowledge_source_id is None

    async def test_reimport_different_source_rejected(self, tmp_path: Path) -> None:
        _seed_python_tree(tmp_path)
        repo = FakeCodebaseStructureMapRepository()
        service = _build_service(tmp_path, repo)

        await service.import_codebase(_submission(source_ref="/src/legacy"))

        with pytest.raises(BrownfieldWorkspaceNotEmptyError):
            await service.import_codebase(_submission(source_ref="/src/other"))
