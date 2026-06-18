"""Tests for the shared decision-repo SQL helpers."""

from types import MappingProxyType

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.persistence._shared.decision_sql import (
    resolve_role_column,
    unfreeze_for_json,
)


@pytest.mark.unit
class TestResolveRoleColumn:
    """The closed-set role-to-column guard that bounds dynamic SQL."""

    def test_executor_maps_to_executing_agent_id(self) -> None:
        assert resolve_role_column("executor", agent_id="a-1") == "executing_agent_id"

    def test_reviewer_maps_to_reviewer_agent_id(self) -> None:
        assert resolve_role_column("reviewer", agent_id="a-1") == "reviewer_agent_id"

    def test_unknown_role_raises_query_error(self) -> None:
        with pytest.raises(QueryError):
            resolve_role_column("admin", agent_id="a-1")

    def test_non_string_role_raises_query_error(self) -> None:
        # An untyped caller that defeats the Literal must not reach the SQL.
        with pytest.raises(QueryError):
            resolve_role_column(123, agent_id="a-1")


@pytest.mark.unit
class TestUnfreezeForJson:
    """Recursive unwrapping of frozen views into JSON primitives."""

    def test_mapping_proxy_becomes_plain_dict(self) -> None:
        result = unfreeze_for_json(MappingProxyType({"a": 1}))
        assert result == {"a": 1}
        assert type(result) is dict

    def test_nested_tuple_becomes_list(self) -> None:
        result = unfreeze_for_json(MappingProxyType({"a": (1, 2)}))
        assert result == {"a": [1, 2]}

    def test_frozenset_becomes_list(self) -> None:
        result = unfreeze_for_json(frozenset({"x"}))
        assert result == ["x"]

    def test_scalar_passes_through(self) -> None:
        assert unfreeze_for_json("plain") == "plain"
        assert unfreeze_for_json(7) == 7
