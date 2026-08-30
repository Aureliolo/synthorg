"""Boot-wiring coverage for the background-job stall-nudge watcher.

``_construct_agent_engine`` resolves its own ``BackgroundJobRegistry``
(mirroring ``_build_tool_registry``'s construction, since the two are
separate functions and neither's local variable reaches the other) and
passes ``create_background_job_watcher(...)`` into the boot
``AgentEngine``. This guards the same class of dormancy defect
``test_engine_assembly_background_jobs.py`` guards for the tool
registry: a collaborator built but never actually reaching what it was
built for.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.background_job_watch import (
    BackgroundJobStalenessConfig,
    BackgroundJobWatcher,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from synthorg.tools.registry import ToolRegistry
from synthorg.workers._engine_assembly import _construct_agent_engine
from tests._shared import FakeClock, make_app_state, mock_of
from tests._shared.fake_background_job_repo import (
    InMemoryBackgroundJobRepository as _InMemoryBackgroundJobRepository,
)
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.unit


def _app_state(
    *,
    background_job_staleness: BackgroundJobStalenessConfig,
    persistence_connected: bool,
) -> AppState:
    """Build the minimal ``AppState`` ``_construct_agent_engine`` needs.

    Returns:
        The composed ``AppState``.
    """
    config = RootConfig(
        company_name="test",
        background_job_staleness=background_job_staleness,
    )
    resolver = mock_of[ConfigResolver](
        get_float=AsyncMock(return_value=0.5),
        get_int=AsyncMock(return_value=1),
        get_str=AsyncMock(return_value=""),
        get_bool=AsyncMock(return_value=False),
        get_provider_configs=AsyncMock(return_value={}),
    )
    persistence = mock_of[PersistenceBackend](
        is_connected=persistence_connected,
        background_jobs=_InMemoryBackgroundJobRepository(),
        projects=mock_of[ProjectRepository](),
    )
    return make_app_state(
        config=config,
        clock=FakeClock(),
        persistence=persistence,
        approval_store=ApprovalStore(),
        agent_registry=AgentRegistryService(),
        task_engine=mock_of[TaskEngine](),
        slices={SettingsStateSlice: {"config_resolver": resolver}},
    )


async def _engine_for(app_state: AppState) -> AgentEngine:
    return await _construct_agent_engine(
        app_state,
        ScriptedProvider([]),
        registry=ProviderRegistry(drivers={}),
        tool_registry=ToolRegistry([]),
        coordination_metrics_collector=None,
    )


class TestBackgroundJobWatcherBootWiring:
    async def test_disabled_by_default(self) -> None:
        app_state = _app_state(
            background_job_staleness=BackgroundJobStalenessConfig(),
            persistence_connected=True,
        )
        engine = await _engine_for(app_state)
        assert engine._background_job_watcher is None

    async def test_enabled_with_persistence_connected(self) -> None:
        app_state = _app_state(
            background_job_staleness=BackgroundJobStalenessConfig(enabled=True),
            persistence_connected=True,
        )
        engine = await _engine_for(app_state)
        assert isinstance(engine._background_job_watcher, BackgroundJobWatcher)

    async def test_enabled_without_persistence_fails_closed(self) -> None:
        app_state = _app_state(
            background_job_staleness=BackgroundJobStalenessConfig(enabled=True),
            persistence_connected=False,
        )
        engine = await _engine_for(app_state)
        assert engine._background_job_watcher is None
