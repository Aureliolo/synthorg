"""Where the recursion switch and its backstops come from, and how they stop.

Depth and width are runaway guards rather than targets, so what matters is
that the operator's setting is what binds when a caller declared nothing, that
a caller that declared one still wins, and that spending the tree's session
budget stops gracefully rather than discarding what it paid for.

The switch beside them is read the same way and fails apart the same way: a
setting that cannot answer for itself leaves recursion off, and a settings
store that is momentarily down surfaces rather than quietly planning every
objective at one level.
"""

from unittest.mock import MagicMock

import pytest

from synthorg.engine.decomposition._recursion import (
    DEFAULT_SUBTASK_MAX_ARTIFACTS,
    DEFAULT_SUBTASK_MAX_CRITERIA,
    TreeSessionLedger,
    resolve_decomposition_bounds,
    resolve_recursion_budget,
)
from synthorg.engine.decomposition.context import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_SUBTASKS,
    DecompositionContext,
    depth_budget,
    width_budget,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_OPERATOR_DEPTH = 7
_OPERATOR_WIDTH = 4


def _settings() -> MagicMock:
    """Build a resolver answering both bound keys with the operator's values.

    Returns:
        The scripted resolver.
    """
    answers = {
        "decomposition_max_depth": _OPERATOR_DEPTH,
        "decomposition_max_subtasks": _OPERATOR_WIDTH,
    }
    resolver: MagicMock = mock_of[ConfigResolverProtocol]()
    resolver.get_int.side_effect = lambda _namespace, key: answers[key]
    return resolver


class TestBudgetReaders:
    def test_an_undeclared_unresolved_context_reads_the_definition_default(
        self,
    ) -> None:
        context = DecompositionContext()
        assert depth_budget(context) == DEFAULT_MAX_DEPTH
        assert width_budget(context) == DEFAULT_MAX_SUBTASKS

    def test_a_declared_value_is_what_is_read(self) -> None:
        context = DecompositionContext(max_depth=2, max_subtasks=3)
        assert depth_budget(context) == 2
        assert width_budget(context) == 3


class TestResolveBounds:
    async def test_an_undeclared_context_takes_the_operator_setting(self) -> None:
        resolved = await resolve_decomposition_bounds(
            DecompositionContext(), _settings()
        )
        assert resolved.max_depth == _OPERATOR_DEPTH
        assert resolved.max_subtasks == _OPERATOR_WIDTH

    async def test_a_declared_bound_wins_over_the_setting(self) -> None:
        resolved = await resolve_decomposition_bounds(
            DecompositionContext(max_depth=2, max_subtasks=3), _settings()
        )
        assert resolved.max_depth == 2
        assert resolved.max_subtasks == 3

    async def test_one_declared_bound_leaves_the_other_to_the_setting(self) -> None:
        resolved = await resolve_decomposition_bounds(
            DecompositionContext(max_depth=2), _settings()
        )
        assert resolved.max_depth == 2
        assert resolved.max_subtasks == _OPERATOR_WIDTH

    async def test_no_resolver_falls_back_to_the_definitions(self) -> None:
        resolved = await resolve_decomposition_bounds(DecompositionContext(), None)
        assert resolved.max_depth == DEFAULT_MAX_DEPTH
        assert resolved.max_subtasks == DEFAULT_MAX_SUBTASKS

    async def test_an_unreadable_setting_still_leaves_a_bound_standing(self) -> None:
        # A backstop nobody can read is not a licence to spend: the runaway
        # this exists to catch is caught either way.
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_int.side_effect = SettingNotFoundError(
            "coordination/decomposition_max_depth"
        )
        resolved = await resolve_decomposition_bounds(DecompositionContext(), resolver)
        assert resolved.max_depth == DEFAULT_MAX_DEPTH
        assert resolved.max_subtasks == DEFAULT_MAX_SUBTASKS

    async def test_a_fully_declared_context_asks_the_settings_nothing(self) -> None:
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        await resolve_decomposition_bounds(
            DecompositionContext(max_depth=2, max_subtasks=3), resolver
        )
        assert resolver.get_int.await_count == 0


class TestResolveRecursionBudget:
    """Where the switch and its thresholds come from, and what stops a read."""

    async def test_the_operator_switch_and_thresholds_are_what_bind(self) -> None:
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_bool.return_value = True
        resolver.get_int.side_effect = lambda _namespace, key: {
            "subtask_max_artifacts": 3,
            "subtask_max_criteria": 4,
        }[key]

        budget = await resolve_recursion_budget(resolver)

        assert budget.enabled
        assert budget.policy.max_expected_artifacts == 3
        assert budget.policy.max_acceptance_criteria == 4

    async def test_a_switch_that_is_off_asks_for_no_thresholds(self) -> None:
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_bool.return_value = False

        budget = await resolve_recursion_budget(resolver)

        assert not budget.enabled
        assert resolver.get_int.await_count == 0

    async def test_no_resolver_at_all_stays_flat(self) -> None:
        budget = await resolve_recursion_budget(None)

        assert not budget.enabled
        assert budget.policy.max_expected_artifacts == DEFAULT_SUBTASK_MAX_ARTIFACTS
        assert budget.policy.max_acceptance_criteria == DEFAULT_SUBTASK_MAX_CRITERIA

    @pytest.mark.parametrize(
        "unanswerable",
        [
            SettingNotFoundError("coordination/recursive_decomposition_enabled"),
            ValueError("not a boolean"),
        ],
    )
    async def test_a_setting_that_cannot_answer_for_itself_stays_flat(
        self, unanswerable: Exception
    ) -> None:
        # Unreadable for as long as it stays that way, so there is nothing a
        # later decomposition would learn by asking again.
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_bool.side_effect = unanswerable

        budget = await resolve_recursion_budget(resolver)

        assert not budget.enabled

    async def test_a_settings_store_that_is_down_is_not_a_silent_downgrade(
        self,
    ) -> None:
        # Recursion ships ON, so swallowing this plans every objective at one
        # level for as long as the store stays down: the shape the sweep
        # measured delivering nothing, with one WARNING per decomposition and
        # no other sign. A store that is momentarily unreachable is a fact
        # about the moment, not about the setting.
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_bool.side_effect = RuntimeError("settings store unreachable")

        with pytest.raises(RuntimeError, match="settings store unreachable"):
            await resolve_recursion_budget(resolver)

    async def test_a_store_that_drops_between_the_two_threshold_reads_surfaces(
        self,
    ) -> None:
        # The switch answered, so this decomposition is going to recurse; a
        # threshold read that then fails would otherwise flip it back to flat
        # after the operator's own answer was already in hand.
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_bool.return_value = True
        resolver.get_int.side_effect = RuntimeError("settings store unreachable")

        with pytest.raises(RuntimeError, match="settings store unreachable"):
            await resolve_recursion_budget(resolver)


class TestTreeSessionLedger:
    def test_spends_down_to_zero(self) -> None:
        ledger = TreeSessionLedger(remaining=2)
        assert ledger.take()
        assert ledger.take()
        assert not ledger.take()

    def test_records_that_it_bound(self) -> None:
        ledger = TreeSessionLedger(remaining=0)
        assert not ledger.take()
        assert ledger.exhausted

    def test_says_nothing_while_budget_remains(self) -> None:
        ledger = TreeSessionLedger(remaining=1)
        assert ledger.take()
        assert not ledger.exhausted
