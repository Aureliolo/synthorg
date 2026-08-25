# module-kind: tests
"""The sweep writes every coordination setting a planning run depends on."""

from unittest.mock import AsyncMock

import pytest
import structlog

from evals.errors import RecursionDepthCeilingUndeclaredError
from evals.recursion_depth.tree import _declared_maximum, arm_recursion
from synthorg.engine.decomposition._ceilings import (
    DEFAULT_SESSION_CEILING_SECONDS,
    DEFAULT_TREE_CEILING_SECONDS,
)
from synthorg.engine.decomposition.llm import LlmDecompositionConfig
from synthorg.observability.events.evals import EVALS_RECURSION_SETTINGS_ARMED
from synthorg.settings.registry import SettingsRegistry, get_registry
from synthorg.settings.service import SettingsService
from tests._shared import mock_of

pytestmark = pytest.mark.unit

#: Read off the product's own config so the comparison cannot drift from it.
_PRODUCT_DEFAULT_RETRIES = LlmDecompositionConfig().max_retries

#: Every setting the sweep opens all the way, paired with the key whose
#: declared maximum it must equal. Read from the registry rather than written
#: down, so a product bound that moves is caught here rather than by a write
#: the settings service refuses partway through a paid sweep.
_OPENED_TO_CEILING: tuple[str, ...] = (
    "subtask_max_artifacts",
    "subtask_max_criteria",
    "decomposition_tree_timeout_seconds",
)


async def _writes(*, enabled: bool = True) -> list[tuple[str, str]]:
    """Run ``arm_recursion`` against a recording double.

    ``mock_of`` rather than a hand-written class behind a ``cast``: the cast
    tells the type checker a one-method object is the whole service, so a
    changed ``set`` signature would break every production caller and nothing
    here. The typed double carries the real signature.

    Returns:
        The coordination writes it made, in order, as ``(key, value)`` pairs.
        A list rather than a dict because a dict keeps only the last write per
        key, which is exactly how a second write to one key would hide.
    """
    settings = mock_of[SettingsService](
        set=AsyncMock(return_value=None), registry=get_registry()
    )
    await arm_recursion(settings, enabled=enabled)
    return [
        (call.args[1], call.args[2])
        for call in settings.set.await_args_list
        if call.args[0] == "coordination"
    ]


async def _provider_writes() -> list[tuple[str, str]]:
    """The ``providers`` writes ``arm_recursion`` made, in order.

    Returns:
        ``(key, value)`` pairs.
    """
    settings = mock_of[SettingsService](
        set=AsyncMock(return_value=None), registry=get_registry()
    )
    await arm_recursion(settings, enabled=True)
    return [
        (call.args[1], call.args[2])
        for call in settings.set.await_args_list
        if call.args[0] == "providers"
    ]


class TestTheRetryLadderIsOpenedAllTheWay:
    """A provider blip that outlasts the ladder discards the whole session.

    ``call_provider`` turns an exhausted retry into a terminal ERROR result
    and the loop returns it unchanged, so a leaf thirty turns into building a
    subsystem loses all thirty. Nothing persisted that conversation, so
    nothing can re-enter it. The ladder is the only thing standing in front of
    that, and the sweep buys every attempt the setting allows.
    """

    async def test_the_sweep_arms_the_declared_maximum(self) -> None:
        ceiling = _declared_maximum(
            mock_of[SettingsService](
                set=AsyncMock(return_value=None), registry=get_registry()
            ),
            "retry_max_attempts",
            namespace="providers",
        )

        assert await _provider_writes() == [("retry_max_attempts", str(int(ceiling)))]

    async def test_the_armed_ladder_is_longer_than_the_product_default(
        self,
    ) -> None:
        # Read off the product rather than asserting a literal: the point is
        # that the sweep buys MORE than a request handler gets, which stays
        # true only while the two are compared rather than pinned.
        definition = get_registry().get("providers", "retry_max_attempts")
        assert definition is not None
        assert definition.default is not None
        armed = dict(await _provider_writes())["retry_max_attempts"]

        assert int(armed) > int(definition.default)


async def _armed(*, enabled: bool = True) -> dict[str, str]:
    """The coordination settings ``arm_recursion`` wrote, keyed by setting.

    Returns:
        One entry per key.

    Raises:
        AssertionError: A key was written twice, which a keyed reading would
            otherwise silently collapse to whichever write happened to be last.
    """
    writes = await _writes(enabled=enabled)
    written = dict(writes)
    assert len(written) == len(writes), (
        f"a coordination key was written twice: {writes}"
    )
    return written


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
        DEFAULT_SESSION_CEILING_SECONDS
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
    """Arming one decomposition ceiling and not the other is the worst option.

    A tree is many sessions by construction, so raising what one session may
    spend while the whole-tree ceiling keeps a default sized for request
    handlers leaves an outer bound that cannot admit even two of the sessions
    the inner one allows. Every tree killed that way has already paid for the
    levels it planned.
    """
    written = await _armed()

    assert "decomposition_tree_timeout_seconds" in written


async def test_tree_ceiling_exceeds_the_product_default() -> None:
    """Compared against the product's own constant so it cannot drift."""
    written = await _armed()

    assert float(written["decomposition_tree_timeout_seconds"]) > (
        DEFAULT_TREE_CEILING_SECONDS
    )


async def test_tree_ceiling_admits_many_sessions() -> None:
    """The structural absurdity, stated as an invariant rather than a value.

    A tree recurses across many sessions, so the outer ceiling has to admit
    many of the inner one. Asserting the relationship rather than a number is
    what keeps this true if either ceiling is re-tuned.
    """
    written = await _armed()

    tree = float(written["decomposition_tree_timeout_seconds"])
    session = float(written["decomposition_timeout_seconds"])
    assert tree > session * 2


@pytest.mark.parametrize("key", _OPENED_TO_CEILING)
async def test_a_setting_opened_all_the_way_equals_its_declared_maximum(
    key: str,
) -> None:
    """Opened to the ceiling means THE ceiling, not a copy of today's value.

    Each of these is armed at whatever its definition currently declares, so a
    product bound that moves moves the sweep with it. A literal here instead
    would keep arming yesterday's number, silently arming a different
    manipulation than the harness documents.
    """
    written = await _armed()

    definition = get_registry().get("coordination", key)
    assert definition is not None
    assert definition.max_value is not None
    assert float(written[key]) == definition.max_value


async def test_the_bound_is_read_off_the_service_that_will_accept_the_write() -> None:
    """Not off the module-level singleton, which nothing here populates.

    That singleton fills when the ``definitions`` sub-package is imported, and
    nothing this module imports does: it is non-empty in a sweep only through
    an incidental chain out of the oracle. Reading the SERVICE's registry ties
    the bound to the authority that will accept or refuse the write, which is
    the only reason the bound is wanted.
    """
    registry = mock_of[SettingsRegistry](get=lambda _ns, _key: None)
    settings = mock_of[SettingsService](
        set=AsyncMock(return_value=None), registry=registry
    )

    with pytest.raises(RecursionDepthCeilingUndeclaredError, match="not registered"):
        await arm_recursion(settings, enabled=True)


async def test_the_armed_event_reports_every_setting_that_was_written() -> None:
    """The log is the only record of which ceilings a killed cell ran under.

    A cell killed by a ceiling reports only that it produced no tree, so this
    event is what an operator reads to find out which bound fired. A hand-kept
    kwarg list is one added setting away from silently omitting it, which is
    exactly when the log is being read.
    """
    settings = mock_of[SettingsService](
        set=AsyncMock(return_value=None), registry=get_registry()
    )

    with structlog.testing.capture_logs() as logs:
        await arm_recursion(settings, enabled=True)

    # Keyed by namespace as well, because the sweep arms two of them and a
    # bare key does not say which setting moved.
    written = {
        f"{call.args[0]}.{call.args[1]}": call.args[2]
        for call in settings.set.await_args_list
    }
    armed = [
        entry for entry in logs if entry["event"] == EVALS_RECURSION_SETTINGS_ARMED
    ]
    assert len(armed) == 1
    assert {key: armed[0][key] for key in written} == written


async def test_an_absent_setting_is_refused_rather_than_guessed() -> None:
    """Nothing to read is a refusal, because the alternative is a guess.

    A guessed ceiling would be discovered as a write the settings service
    rejects, which happens after the sweep has booted and begun spending.
    """
    settings = mock_of[SettingsService](registry=get_registry())

    with pytest.raises(RecursionDepthCeilingUndeclaredError, match="not registered"):
        _declared_maximum(settings, "a_setting_that_does_not_exist")


async def test_an_unbounded_setting_is_refused_rather_than_guessed() -> None:
    """The second way there is nothing to read: present, but with no maximum.

    A different fault from absence and reported differently, because absence
    across the board means the definitions never loaded while this one means a
    definition that did load declares no ceiling.
    """
    real = get_registry().get("coordination", "decomposition_tree_timeout_seconds")
    assert real is not None
    unbounded = real.model_copy(update={"max_value": None})
    registry = mock_of[SettingsRegistry](get=lambda _ns, _key: unbounded)
    settings = mock_of[SettingsService](registry=registry)

    with pytest.raises(RecursionDepthCeilingUndeclaredError, match="no maximum"):
        _declared_maximum(settings, "decomposition_tree_timeout_seconds")
