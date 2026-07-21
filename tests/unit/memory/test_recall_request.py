"""Tests for the structured memory recall request.

The composed query is the whole point of this model: a bare task title
under-retrieves, so the request carries the surrounding work context and
composes a query from it.
"""

import pytest
from pydantic import ValidationError

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.recall_request import MemoryRecallRequest

pytestmark = pytest.mark.unit


def _request(**overrides: object) -> MemoryRecallRequest:
    """Build a recall request with sensible defaults."""
    fields: dict[str, object] = {
        "agent_id": NotBlankStr("agent-1"),
        "task_title": NotBlankStr("Roll back the failed deploy"),
        "token_budget": 2000,
    }
    fields.update(overrides)
    return MemoryRecallRequest(**fields)  # type: ignore[arg-type]


class TestQueryComposition:
    def test_title_alone_composes_to_the_title(self) -> None:
        assert _request().query_text == "Roll back the failed deploy"

    def test_objective_and_role_widen_the_query(self) -> None:
        request = _request(
            objective="Restore checkout availability",
            role="Site Reliability Engineer",
            department="Platform",
        )

        query = request.query_text

        assert "Roll back the failed deploy" in query
        assert "Restore checkout availability" in query
        assert "Site Reliability Engineer" in query
        assert "Platform" in query

    def test_task_leads_the_query(self) -> None:
        """The task is the strongest retrieval anchor, so it goes first."""
        request = _request(objective="Restore checkout availability")

        assert request.query_text.startswith("Roll back the failed deploy")

    def test_blank_context_fields_are_omitted_not_padded(self) -> None:
        """Empty fields must not leave separator noise in the embedding."""
        request = _request(objective="", role="   ", department="")

        assert request.query_text == "Roll back the failed deploy"

    def test_project_id_is_not_embedded_in_the_query(self) -> None:
        # An opaque id is noise in an embedding, not vocabulary.
        request = _request(project_id="checkout-revamp")

        assert "checkout-revamp" not in request.query_text

    def test_project_scopes_recall_by_namespace(self) -> None:
        request = _request(project_id="checkout-revamp")

        assert request.namespaces == frozenset({"default", "project:checkout-revamp"})

    def test_unscoped_work_has_no_namespace_filter(self) -> None:
        assert _request().namespaces is None

    def test_composition_is_stable(self) -> None:
        """A drifting query silently invalidates every cached embedding."""
        first = _request(objective="Restore checkout", role="SRE")
        second = _request(objective="Restore checkout", role="SRE")

        assert first.query_text == second.query_text


class TestValidation:
    def test_is_frozen(self) -> None:
        request = _request()

        with pytest.raises(ValidationError):
            request.task_title = NotBlankStr("other")  # type: ignore[misc]

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            _request(unexpected="value")

    def test_rejects_negative_budget(self) -> None:
        with pytest.raises(ValidationError):
            _request(token_budget=-1)

    def test_zero_budget_is_allowed(self) -> None:
        """Zero is a real state: the caller asks for nothing to be injected."""
        assert _request(token_budget=0).token_budget == 0

    def test_categories_default_to_unrestricted(self) -> None:
        assert _request().categories == frozenset()

    def test_categories_round_trip(self) -> None:
        request = _request(categories=frozenset({MemoryCategory.PROCEDURAL}))

        assert request.categories == frozenset({MemoryCategory.PROCEDURAL})
