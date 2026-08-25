"""Where the depth and width backstops come from, and how they stop.

Both are runaway guards rather than targets, so what matters is that the
operator's setting is what binds when a caller declared nothing, that a caller
that declared one still wins, and that spending the tree's session budget
stops gracefully rather than discarding what it paid for.
"""

from unittest.mock import MagicMock

import pytest

from synthorg.engine.decomposition._recursion import (
    TreeSessionLedger,
    resolve_decomposition_bounds,
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
