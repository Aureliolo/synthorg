# module-kind: tests
"""The sweep writes every coordination setting a planning run depends on."""

from unittest.mock import AsyncMock

import pytest

from evals.recursion_depth.tree import arm_recursion
from synthorg.engine.decomposition.llm import LlmDecompositionConfig
from synthorg.engine.decomposition.service import (
    _DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS,
)
from synthorg.settings.service import SettingsService
from tests._shared import mock_of

pytestmark = pytest.mark.unit

#: Read off the product's own config so the comparison cannot drift from it.
_PRODUCT_DEFAULT_RETRIES = LlmDecompositionConfig().max_retries


async def _armed(*, enabled: bool = True) -> dict[str, str]:
    """Run ``arm_recursion`` against a recording double.

    ``mock_of`` rather than a hand-written class behind a ``cast``: the cast
    tells the type checker a one-method object is the whole service, so a
    changed ``set`` signature would break every production caller and nothing
    here. The typed double carries the real signature.

    Returns:
        The coordination settings it wrote, keyed by setting key.
    """
    settings = mock_of[SettingsService](set=AsyncMock(return_value=None))
    await arm_recursion(settings, enabled=enabled)
    return {
        call.args[1]: call.args[2]
        for call in settings.set.await_args_list
        if call.args[0] == "coordination"
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
