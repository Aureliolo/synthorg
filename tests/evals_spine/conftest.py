"""Shared fixtures for the eval-spine test suite.

The ``host`` fixture is per-test, not shared. ``_ACTIVE_HOSTS`` allows one host
per process, and several tests boot one of their own to exercise the lifecycle,
so a wider scope would hold the single slot and refuse them. Booting the real
application lifespan per test is the price of that; it is why every module using
this fixture carries the ``slow`` marker and a raised timeout.

It lives at the spine's root rather than in one harness's suite because every
harness on the recording spine needs it, and pytest does not share fixtures
between sibling packages.
"""

import asyncio
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path

import pytest
import yaml

from tests.evals_spine._recording import (
    RECORDING_SANDBOX_IMAGE,
    RECORDING_SIDECAR_IMAGE,
    RecordingGatewayHost,
    RecordingHostConfig,
    recording_company_config,
    recording_pair,
)

BriefYamlWriter = Callable[..., Path]  # type: ignore[explicit-any]  # arbitrary-arg brief-writer test factory


# This tier binds real sockets and serves a real application (the recording
# host), which the Windows ``SelectorEventLoop`` cannot drive. It sits
# outside ``tests/unit``, so it does not inherit that tier's override and would
# otherwise take whatever the interpreter defaults to: correct today, but by
# coincidence rather than by choice, and a coincidence that breaks as a hang.
# Declared in a conftest so pytest's plugin manager discovers it; a hook defined
# in a test module is never registered.
if sys.platform == "win32":  # pragma: no cover -- Windows-only branch

    def pytest_asyncio_loop_factories(
        config: pytest.Config,
        item: pytest.Item,
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        """Use ``ProactorEventLoop`` for the eval spine on Windows.

        Returns:
            The loop factory mapping pytest-asyncio selects from.
        """
        del config, item
        return {"proactor": asyncio.ProactorEventLoop}


def _brief_yaml(kind: str, **overrides: object) -> str:
    """Produce a minimal valid brief YAML string for tests.

    Either branch (executable / judged) ships the matching block. Tests
    pass field overrides via *overrides* to construct invalid payloads.
    """
    base: dict[str, object] = {
        "brief_id": overrides.pop("brief_id", "BRIEF_TEST_001"),
        "schema_version": 1,
        "kind": kind,
        "title": "Test brief",
        "description": "A brief used in unit tests.",
        "priority": "medium",
        "estimated_complexity": 3,
        "acceptance_criteria": ["criterion one"],
        "limits": {
            "max_total_cost": 1.0,
            "max_wall_clock_seconds": 60,
            "max_turns": 8,
        },
    }
    if kind == "executable":
        base["checks"] = {
            "hidden_tests": [{"cmd": ["echo", "ok"], "timeout_seconds": 5}],
        }
    else:
        base["rubric"] = {
            "rubric_id": "summarise",
            "dimensions": [
                {"name": "faithfulness", "weight": 0.5, "grade_type": "ternary"},
                {"name": "clarity", "weight": 0.5, "grade_type": "ternary"},
            ],
            "reference_answer_path": "anchors/summarise_reference.md",
        }
    base.update(overrides)
    return yaml.safe_dump(base, sort_keys=False)


@pytest.fixture
def write_brief_yaml(tmp_path: Path) -> BriefYamlWriter:
    """Return a helper that writes a brief YAML into *tmp_path*."""

    def _write(filename: str, kind: str, **overrides: object) -> Path:
        path = tmp_path / filename
        path.write_text(_brief_yaml(kind, **overrides), encoding="utf-8")
        return path

    return _write


@pytest.fixture
async def host(tmp_path: Path) -> AsyncIterator[RecordingGatewayHost]:
    """Boot and serve the recording host on an ephemeral loopback port.

    Yields:
        The started host.
    """
    # Loopback only: nothing here drives a container, so the wider bind a real
    # recording needs would expose the surface for no gain.
    config = RecordingHostConfig(
        company_config=recording_company_config(),
        scratch_dir=tmp_path / "host",
        coordination_pair=recording_pair(),
        bind_host="127.0.0.1",
        sandbox_image=RECORDING_SANDBOX_IMAGE,
        sidecar_image=RECORDING_SIDECAR_IMAGE,
    )
    async with RecordingGatewayHost(config) as started:
        yield started
