# module-kind: tests
"""Shared fixtures for the recording-host and per-cell-binding suites.

The ``host`` fixture is per-test, not shared. ``_ACTIVE_HOSTS`` allows one host
per process, and several tests boot one of their own to exercise the lifecycle,
so a wider scope would hold the single slot and refuse them. Booting the real
application lifespan per test is the price of that; it is why every module using
this fixture carries the ``slow`` marker and a raised timeout.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from evals.loop_ab.host import LoopAbGatewayHost, LoopAbHostConfig
from evals.runner.execution import seed_eval_project
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

#: The capability every binding test binds to, present in the company config below.
RECORDING_PROVIDER = "test-provider"
RECORDING_MODEL = "example-expert-001"

#: Image references the fixture host is started with. Deliberately unlike the
#: registered defaults, so a test asserting one of them cannot pass against a
#: value that arrived from the code default or from an import-time singleton.
#: Unresolvable on purpose: nothing in this suite launches a container.
RECORDING_SANDBOX_IMAGE = "example.invalid/sandbox:under-test"
RECORDING_SIDECAR_IMAGE = "example.invalid/sidecar:under-test"
RECORDING_OPENHANDS_IMAGE = "example.invalid/openhands:under-test"


def recording_company_config() -> RootConfig:
    """Build the recording company config the host boots against.

    The driver is the deterministic scripted one, so a full round trip through
    the gateway contacts no provider and costs nothing. What the recorder's own
    legs dial is the gateway itself, which they reach over HTTP regardless.

    Returns:
        The recording company config.
    """
    return RootConfig(
        company_name="Loop A/B Host",
        providers={
            RECORDING_PROVIDER: ProviderConfig(
                driver=NotBlankStr("scripted"),
                connection_name=NotBlankStr("conn-scripted"),
                models=(ProviderModelConfig(id=NotBlankStr(RECORDING_MODEL)),),
            )
        },
    )


@pytest.fixture
async def project_repo(tmp_path: Path) -> AsyncIterator[ProjectRepository]:
    """A connected backend carrying the benchmark project.

    Every brief expects artifacts, which makes every cell a work task, and the
    engine refuses to run one against a project it cannot look up. Real
    persistence rather than a double, because the lookup the engine performs is
    the thing under test: a stand-in that answered would prove nothing about
    whether the seeded row is reachable.

    Yields:
        The seeded project repository.
    """
    backend = SQLitePersistenceBackend(
        SQLiteConfig(path=str(tmp_path / "eval-projects.db"))
    )
    await backend.connect()
    try:
        await backend.migrate()
        await seed_eval_project(backend.projects)
        yield backend.projects
    finally:
        await backend.disconnect()


@pytest.fixture
async def host(tmp_path: Path) -> AsyncIterator[LoopAbGatewayHost]:
    """Boot and serve the recording host on an ephemeral loopback port.

    Yields:
        The started host.
    """
    # Loopback only: nothing here drives a container, so the wider bind a real
    # recording needs would expose the surface for no gain.
    config = LoopAbHostConfig(
        company_config=recording_company_config(),
        scratch_dir=tmp_path / "host",
        bind_host="127.0.0.1",
        sandbox_image=RECORDING_SANDBOX_IMAGE,
        sidecar_image=RECORDING_SIDECAR_IMAGE,
        openhands_image=RECORDING_OPENHANDS_IMAGE,
    )
    async with LoopAbGatewayHost(config) as started:
        yield started
