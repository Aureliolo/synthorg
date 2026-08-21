# module-kind: tests
"""The sweep writes every coordination setting a planning run depends on."""

from typing import cast

import pytest

from evals.recursion_depth.tree import arm_recursion
from synthorg.engine.decomposition.llm import LlmDecompositionConfig
from synthorg.engine.decomposition.service import (
    _DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS,
)
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit

#: Read off the product's own config so the comparison cannot drift from it.
_PRODUCT_DEFAULT_RETRIES = LlmDecompositionConfig().max_retries


class _RecordingSettings:
    """Captures the writes ``arm_recursion`` makes, in order."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    async def set(self, namespace: str, key: str, value: str, **_: object) -> None:
        """Record one write.

        Args:
            namespace: The setting namespace.
            key: The setting key.
            value: The written value.
        """
        self.writes.append((namespace, key, value))


async def _armed(*, enabled: bool = True) -> dict[str, str]:
    """Run ``arm_recursion`` against a recorder.

    Returns:
        The coordination settings it wrote, keyed by setting key.
    """
    recorder = _RecordingSettings()
    await arm_recursion(cast(SettingsService, recorder), enabled=enabled)
    return {
        key: value
        for namespace, key, value in recorder.writes
        if namespace == "coordination"
    }


async def test_planning_timeout_is_written() -> None:
    """An unwritten ceiling costs a whole arm, not a slow one.

    The product default is sized for a model that answers directly. Every model
    worth sweeping reasons first, and a reasoning planner ran the same brief in
    310s once and past 600s the next time: at the default one arm completed and
    the other was killed mid-plan and recorded as an unavailable cell, which
    destroys the very comparison the sweep exists to make.
    """
    written = await _armed()

    assert "decomposition_timeout_seconds" in written


async def test_planning_timeout_exceeds_the_product_default() -> None:
    """Compared against the product's own constant so it cannot drift."""
    written = await _armed()

    assert float(written["decomposition_timeout_seconds"]) > (
        _DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS
    )


async def test_planning_retries_exceed_the_product_default() -> None:
    """A failed plan costs the sweep its comparison, not a data point.

    Arms are compared pairwise, so one cell that never produces a tree
    destroys the pairing rather than weakening it. A measured run refused a
    plan three times for three DIFFERENT faults while its sibling arm planned
    cleanly: each attempt corrected the previous one, so it was converging and
    ran out of budget. The sweep therefore buys more attempts than a
    production initiative would.
    """
    written = await _armed()

    assert int(written["decomposition_max_retries"]) > _PRODUCT_DEFAULT_RETRIES


@pytest.mark.parametrize("enabled", [True, False])
async def test_ceiling_is_written_in_both_arms(enabled: bool) -> None:
    """Both arms plan, so both need the ceiling; only recursion differs."""
    written = await _armed(enabled=enabled)

    assert "decomposition_timeout_seconds" in written
    expected = "true" if enabled else "false"
    assert written["recursive_decomposition_enabled"] == expected
