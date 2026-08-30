"""Boot-wiring coverage for background-job registry threading.

Guards the dormancy class of defect (a collaborator built but never
actually reachable from a booted tool registry) plus the construction-order
cycle ``_engine_assembly.py`` resolves between the lifecycle strategy and
the Docker sandbox whose bound method becomes its ``pin_check``. Neither
gap is visible to a test that only exercises ``DockerSandboxBackgroundMixin``
directly (as ``test_docker_sandbox_background.py`` does): both live entirely
in how ``_build_tool_registry`` wires its collaborators together.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import structlog.contextvars

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
from synthorg.tools.sandbox.lifecycle.per_agent import PerAgentStrategy
from synthorg.tools.sandbox.sandboxing_config import SandboxingConfig
from synthorg.workers._background_job_wiring import (
    _MAX_CONCURRENT_JOBS_KEY,
    _OUTPUT_BYTE_CAP_KEY,
    _TOOLS_NS,
)
from synthorg.workers._engine_assembly import _build_tool_registry
from tests._shared import FakeClock, make_app_state, mock_of
from tests._shared.fake_background_job_exec import (
    make_mock_docker as _make_mock_docker,
)
from tests._shared.fake_background_job_exec import (
    patch_aiodocker as _patch_aiodocker,
)
from tests._shared.fake_background_job_exec import (
    responder_for as _responder_for,
)
from tests._shared.fake_background_job_repo import (
    InMemoryBackgroundJobRepository as _InMemoryBackgroundJobRepository,
)

#: Real setting defaults (``settings/definitions/tools.py``), so a
#: concurrency-ceiling read during boot does not artificially constrain
#: a test starting more than one job -- the blanket-``1`` stub this
#: replaced set the ceiling to one job for every test built on this
#: fixture, regardless of the setting key actually being read.
_BACKGROUND_JOB_INT_DEFAULTS: dict[str, int] = {
    _MAX_CONCURRENT_JOBS_KEY: 5,
    _OUTPUT_BYTE_CAP_KEY: 1_000_000,
}

pytestmark = pytest.mark.unit

_BACKGROUND_TOOL_NAMES: tuple[str, ...] = (
    "check_background_job",
    "read_background_job_output",
    "cancel_background_job",
    "list_background_jobs",
)


def _per_agent_docker_config() -> RootConfig:
    """A boot config wired for a reusable, background-job-capable sandbox.

    Returns:
        A ``RootConfig`` whose Docker backend uses ``per-agent``
        (the strategy ``pin_check`` binding actually applies to).
    """
    return RootConfig(
        company_name="test",
        sandboxing=SandboxingConfig(
            default_backend="docker",
            docker=DockerSandboxConfig(
                lifecycle=SandboxLifecycleConfig(strategy="per-agent"),
            ),
        ),
    )


def _boot_app_state(
    *, repo: _InMemoryBackgroundJobRepository, config: RootConfig
) -> AppState:
    """Build a minimal ``AppState`` sufficient for ``_build_tool_registry``.

    Returns:
        The composed ``AppState``.
    """

    async def _get_int(namespace: str, key: str) -> int:
        if namespace == _TOOLS_NS and key in _BACKGROUND_JOB_INT_DEFAULTS:
            return _BACKGROUND_JOB_INT_DEFAULTS[key]
        return 1

    resolver = mock_of[ConfigResolver](
        get_float=AsyncMock(return_value=30.0),
        get_int=AsyncMock(side_effect=_get_int),
        get_str=AsyncMock(return_value=""),
        get_bool=AsyncMock(return_value=False),
    )
    persistence = mock_of[PersistenceBackend](
        is_connected=True,
        background_jobs=repo,
    )
    return make_app_state(
        config=config,
        clock=FakeClock(),
        persistence=persistence,
        slices={SettingsStateSlice: {"config_resolver": resolver}},
    )


class TestBackgroundJobBootWiring:
    """The registry/pin_check wiring the tools and the pin_check cycle require."""

    async def test_tools_present_and_reach_a_wired_sandbox(
        self, tmp_path: Path
    ) -> None:
        repo = _InMemoryBackgroundJobRepository()
        app_state = _boot_app_state(repo=repo, config=_per_agent_docker_config())

        registry, _count, sandbox_backends = await _build_tool_registry(
            app_state, tmp_path
        )

        for name in _BACKGROUND_TOOL_NAMES:
            assert name in registry

        docker_backend = sandbox_backends["docker"]
        assert isinstance(docker_backend, DockerSandbox)
        # The half that matters: the tools resolve to THIS backend
        # (verified above) and this backend actually carries a registry --
        # not `None`, which is what every `*_background` method reads as
        # "feature off" and refuses against.
        assert docker_backend._background_jobs is not None

        strategy = docker_backend._lifecycle_strategy
        assert isinstance(strategy, PerAgentStrategy)
        # The construction-order cycle: `create_lifecycle_strategy` builds
        # the strategy before `docker_backend` exists, so `pin_check` can
        # only be bound in a second step, after both exist. Unbound means
        # every live job is invisible to grace/idle expiry.
        assert strategy._pin_check is not None

    async def test_started_job_is_visible_through_list_background_jobs(
        self, tmp_path: Path
    ) -> None:
        """A job started through the wired sandbox round-trips through listing.

        The sharper regression target: a mismatch between the key
        ``start_background`` persists under and the key
        ``list_background_jobs`` queries by (both resolved independently,
        both reachable only through this boot path) reads as "no jobs",
        not as an error -- silent data loss a narrower test cannot see.
        """
        repo = _InMemoryBackgroundJobRepository()
        app_state = _boot_app_state(repo=repo, config=_per_agent_docker_config())
        _registry, _count, sandbox_backends = await _build_tool_registry(
            app_state, tmp_path
        )
        docker_backend = sandbox_backends["docker"]
        assert isinstance(docker_backend, DockerSandbox)

        docker = _make_mock_docker(_responder_for(pid="123"))
        # `owner_id=None` derives from the structlog correlation context
        # (see `start_background`'s own docstring for why `None` is
        # correct rather than a convenience default): under `per-agent`
        # that reads `agent_id`, which a real turn binds around the
        # whole tool call and this test binds explicitly instead.
        with (
            structlog.contextvars.bound_contextvars(agent_id="agent-1"),
            _patch_aiodocker(docker),
        ):
            job_id = await docker_backend.start_background(
                command="sleep",
                args=("30",),
                category="terminal",
            )
            jobs = await docker_backend.list_background_jobs(category="terminal")

        assert [j.job_id for j in jobs] == [job_id]
