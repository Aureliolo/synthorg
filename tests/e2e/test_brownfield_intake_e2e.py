"""Acceptance: brownfield codebase intake under the runtime harness.

Points the studio at an existing repo (a real on-disk git fixture) and
proves the brownfield-intake acceptance end-to-end against the REAL
work-pipeline spine built by ``build_runtime_services``:

1. The :class:`BrownfieldEntryAdapter` imports the source into the
   project's persistent workspace (real ``EmbeddedGitBackend`` seed),
   scans it into a persisted :class:`CodebaseStructureMap`, indexes the
   codebase into the knowledge store, then drives an ANALYSIS work item
   through the spine so an agent actually runs the analysis pass.
2. An agent can retrieve its own understanding: ``QueryStructureMapTool``
   returns the persisted modules.
3. A follow-up ``TASK_BOARD`` directive for the same project flows
   through the spine (the org continues building on the ingested base).

Zero real LLM spend (scripted provider) and no real network (the source
is a local git repo; knowledge ingestion is mocked).
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.budget.tracker import CostTracker
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.enums import AgentStatus
from synthorg.core.project import Project
from synthorg.core.role import Authority, Skill
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.brownfield.models import CodebaseImportSubmission
from synthorg.engine.brownfield.scanner import build_structure_map_scanners
from synthorg.engine.brownfield.service import BrownfieldImportService
from synthorg.engine.brownfield.source_resolver import BrownfieldSourceResolver
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.pipeline.entry.brownfield_adapter import BrownfieldEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import (
    TaskBoardEntryAdapter,
    TaskBoardFiling,
)
from synthorg.engine.pipeline.models import WorkSource
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.engine.workspace.git_backend import EmbeddedGitBackend
from synthorg.engine.workspace.git_backend.config import GitBackendConfig
from synthorg.engine.workspace.project_workspace_service import ProjectWorkspaceService
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.knowledge.service import KnowledgeService
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.tools.structure_map.query_structure_map import QueryStructureMapTool
from synthorg.workers.runtime_builder import build_runtime_services
from tests._shared import FakeClock, as_uuid, make_app_state, mock_of, sid
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_PROJECT = "acquired-co"


class _StopStrategy:
    """Scripted provider strategy: every agent turn STOPs immediately."""

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        del messages, tools, config
        return CompletionResponse(
            content="Analysis complete.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=8, output_tokens=4, cost=0.0001),
            model=model,
        )


class _TaskCreatingIntake:
    """Persist a real task from the work item so the spine can read it back."""

    def __init__(self, task_engine: TaskEngine) -> None:
        self._task_engine = task_engine

    async def process(self, request: Any) -> IntakeResult:
        meta = request.metadata
        created = await self._task_engine.create_task(
            CreateTaskData(
                title=request.requirement.title,
                description=request.requirement.description,
                type=TaskType.ANALYSIS,
                project=str(meta["project"]),
                created_by=str(meta["requested_by"]),
            ),
            requested_by=str(meta["requested_by"]),
        )
        return IntakeResult.accepted_result(
            request_id=request.request_id,
            task_id=str(created.id),
        )


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _write_source_files(path: Path) -> None:
    """Create the source tree on disk (sync; called via a worker thread)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(
        '[project]\nname = "acquired"\ndependencies = ["httpx"]\n'
        '[project.scripts]\nacquired = "acquired.cli:main"\n',
        encoding="utf-8",
    )
    pkg = path / "acquired"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli.py").write_text("def main():\n    pass\n", encoding="utf-8")


async def _make_source_repo(path: Path) -> None:
    """Build a small Python source git repo to import."""
    await asyncio.to_thread(_write_source_files, path)

    async def _git(*args: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(path),
            env=_clean_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        rc = await proc.wait()
        assert rc == 0, f"git {args} failed ({rc})"

    await _git("init", "--initial-branch", "main")
    await _git("config", "user.email", "src@example.com")
    await _git("config", "user.name", "Source")
    await _git("add", "-A")
    await _git("commit", "-m", "initial")


def _agent() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="analyst",
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        skills=SkillSet(primary=(Skill(id="analysis", name="analysis"),)),
        authority=Authority(budget_limit=10.0),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


@pytest.fixture
async def persistence() -> AsyncGenerator[FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def task_engine(
    persistence: FakePersistenceBackend,
) -> AsyncGenerator[TaskEngine]:
    engine = TaskEngine(persistence=persistence)
    await engine.start()
    yield engine
    await engine.stop()


async def _build_pipeline(
    *,
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> DefaultWorkPipeline:
    provider = ScriptedDriver("test-provider", strategy=_StopStrategy())
    registry = ProviderRegistry({"test-provider": provider})
    agent_registry = AgentRegistryService()
    await agent_registry.register(_agent())
    root_config = RootConfig(company_name="brownfield-e2e")
    settings_service = SettingsService(
        repository=persistence.settings, registry=get_registry()
    )
    await settings_service.set("coordination", "routing_policy", "leaf-threshold")
    config_resolver = ConfigResolver(
        settings_service=settings_service, config=root_config
    )
    intake = IntakeEngine(strategy=_TaskCreatingIntake(task_engine))
    app_state = make_app_state(
        provider_registry=registry,
        config=root_config,
        config_resolver=config_resolver,
        task_engine=task_engine,
        agent_registry=agent_registry,
        approval_store=ApprovalStore(),
        clock=FakeClock(),
        agent_workspace_root=tmp_path,
        persistence=persistence,
        client_simulation_state=mock_of[ClientSimulationState](intake_engine=intake),
        cost_tracker=CostTracker(),
    )
    runtime = await build_runtime_services(app_state, workspace_root=tmp_path)
    pipeline = runtime.work_pipeline
    assert isinstance(pipeline, DefaultWorkPipeline)
    return pipeline


def _import_service(
    *,
    persistence: FakePersistenceBackend,
    tmp_path: Path,
    knowledge: KnowledgeService,
) -> BrownfieldImportService:
    workspace_service = ProjectWorkspaceService(
        base_root=tmp_path / "workspaces",
        repo=persistence.project_workspaces,
        git_backend=EmbeddedGitBackend(
            base_root=tmp_path / "workspaces",
            embedded_subdir="git-repos",
            cmd_timeout=30.0,
            clock=FakeClock(),
        ),
        config=GitBackendConfig(),
        clock=FakeClock(),
    )
    return BrownfieldImportService(
        workspace_service=workspace_service,
        source_resolver=BrownfieldSourceResolver(),
        scanners=build_structure_map_scanners(),
        structure_map_repo=persistence.codebase_structure_maps,
        knowledge_service=knowledge,
        clock=FakeClock(),
    )


async def test_brownfield_intake_acceptance(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    await persistence.projects.create(
        Project(id=as_uuid(_PROJECT), name=NotBlankStr("Acquired Co"))
    )
    source = tmp_path / "source"
    await _make_source_repo(source)

    pipeline = await _build_pipeline(
        persistence=persistence, task_engine=task_engine, tmp_path=tmp_path
    )
    knowledge = mock_of[KnowledgeService]()
    knowledge.ingest.return_value = SimpleNamespace(source_id="ks-1")
    import_service = _import_service(
        persistence=persistence, tmp_path=tmp_path, knowledge=knowledge
    )
    adapter = BrownfieldEntryAdapter(
        work_pipeline=pipeline, import_service=import_service
    )

    # ── Stage 1: import + structure map + analysis pass ──────────────
    result = await adapter.submit(
        CodebaseImportSubmission(
            project_id=NotBlankStr(sid(_PROJECT)),
            source_ref=NotBlankStr(str(source)),
            title=NotBlankStr("Acquired codebase"),
            requested_by=NotBlankStr("operator"),
        )
    )

    assert result.is_success is True
    # An agent actually ran the analysis pass (status advanced past CREATED).
    assert result.final_task_status is not TaskStatus.CREATED
    assert result.work_item.source is WorkSource.BROWNFIELD
    assert result.work_item.task_type is TaskType.ANALYSIS

    stored = await persistence.codebase_structure_maps.get(NotBlankStr(sid(_PROJECT)))
    assert stored is not None
    assert any(m.path == "acquired" for m in stored.modules)
    assert any(d.name == "httpx" for d in stored.dependencies)
    knowledge.ingest.assert_awaited_once()

    # ── Stage 2a: an agent can retrieve its own understanding ────────
    query_tool = QueryStructureMapTool(
        repository=persistence.codebase_structure_maps,
        project_id=NotBlankStr(sid(_PROJECT)),
    )
    rendered = await query_tool.execute(arguments={"facet": "modules"})
    assert rendered.is_error is False
    assert "acquired" in rendered.content

    # ── Stage 2b: a follow-up directive builds on the ingested base ──
    task_board = TaskBoardEntryAdapter(work_pipeline=pipeline)
    follow_up = await task_board.submit(
        TaskBoardFiling(
            title=NotBlankStr("Add a health endpoint to the acquired service"),
            description=NotBlankStr(
                "Using the imported codebase, add a /health endpoint."
            ),
            task_type=TaskType.DEVELOPMENT,
            project=NotBlankStr(sid(_PROJECT)),
            requested_by=NotBlankStr("operator"),
        )
    )
    assert follow_up.is_success is True
    assert follow_up.final_task_status is not TaskStatus.CREATED
