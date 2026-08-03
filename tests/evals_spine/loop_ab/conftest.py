# module-kind: tests
"""Shared fixtures for the recording-host and per-cell-binding suites.

Booting the host runs the real application lifespan, so the two suites share one
started instance rather than each paying for its own.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from evals.loop_ab.host import LoopAbGatewayHost, LoopAbHostConfig
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr

#: The tier every binding test binds to, present in the company config below.
RECORDING_PROVIDER = "test-provider"
RECORDING_MODEL = "example-large-001"


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
    )
    async with LoopAbGatewayHost(config) as started:
        yield started
