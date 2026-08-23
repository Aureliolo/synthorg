# module-kind: tests
"""The sweep writes every coordination setting a planning run depends on."""

from unittest.mock import AsyncMock

import pytest

from evals.recursion_depth.tree import arm_recursion
from synthorg.engine.decomposition.llm import LlmDecompositionConfig
from synthorg.engine.decomposition.service import (
    _DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS,
    _DEFAULT_TREE_TIMEOUT_SECONDS,
)
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import mock_of

pytestmark = pytest.mark.unit

#: Read off the product's own config so the comparison cannot drift from it.
_PRODUCT_DEFAULT_RETRIES = LlmDecompositionConfig().max_retries

#: A sweep small enough that the derived tree ceiling stays under the setting's
#: own maximum, so the clamp is not what these assertions are reading.
_SMALL_SWEEP_SESSIONS = 30


async def _armed(
    *, enabled: bool = True, max_sessions: int = _SMALL_SWEEP_SESSIONS
) -> dict[str, str]:
    """Run ``arm_recursion`` against a recording double.

    ``mock_of`` rather than a hand-written class behind a ``cast``: the cast
    tells the type checker a one-method object is the whole service, so a
    changed ``set`` signature would break every production caller and nothing
    here. The typed double carries the real signature.

    Returns:
        The coordination settings it wrote, keyed by setting key.
    """
    settings = mock_of[SettingsService](set=AsyncMock(return_value=None))
    await arm_recursion(settings, enabled=enabled, max_sessions=max_sessions)
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


async def test_tree_ceiling_is_written() -> None:
    """Arming one ceiling and not the other is worse than arming neither.

    A live run raised the per-session ceiling to four times the product default
    and left this one at its default, which is sized for the two callers that
    are request handlers. Three of five planning attempts were then killed at
    exactly the default, each having already spent between 0.6M and 1.3M
    tokens, and every one was filed as an unavailable cell rather than as a
    harness that could not finish a tree it was paying for.
    """
    written = await _armed()

    assert "decomposition_tree_timeout_seconds" in written


async def test_tree_ceiling_exceeds_the_product_default() -> None:
    """Compared against the product's own constant so it cannot drift."""
    written = await _armed()

    assert float(written["decomposition_tree_timeout_seconds"]) > (
        _DEFAULT_TREE_TIMEOUT_SECONDS
    )


async def test_tree_ceiling_admits_more_than_one_session() -> None:
    """The structural absurdity the live run hit, stated as an invariant.

    A tree is many sessions by construction, so a tree ceiling that cannot
    admit even two of the sessions the sweep itself allows can only ever kill
    a tree partway through. That is what the product default did once the
    per-session ceiling was raised past half of it.
    """
    written = await _armed()

    tree = float(written["decomposition_tree_timeout_seconds"])
    session = float(written["decomposition_timeout_seconds"])
    assert tree > session * 2


async def test_tree_ceiling_is_clamped_to_what_the_setting_accepts() -> None:
    """A derived value the service would refuse is a write that fails mid-run.

    The derivation scales with the sweep's session ceiling, so a large enough
    sweep computes a number above what the definition allows. Clamping is what
    keeps that from surfacing as a refused write partway through a paid run.
    """
    definition = get_registry().get(
        "coordination", "decomposition_tree_timeout_seconds"
    )
    assert definition is not None
    assert definition.max_value is not None

    written = await _armed(max_sessions=1_000_000)

    assert float(written["decomposition_tree_timeout_seconds"]) == definition.max_value
