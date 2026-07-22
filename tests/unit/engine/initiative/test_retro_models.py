# module-kind: tests
"""Unit tests for the SHIP-time retrospective models and parser."""

import pytest

from synthorg.engine.errors import RetrospectiveParseError
from synthorg.engine.initiative.retro_models import (
    RetrospectiveDraft,
    args_to_retrospective,
    build_retrospective_tool,
    org_category_for,
    retro_object_tag,
)
from synthorg.memory.enums import OrgFactCategory

pytestmark = pytest.mark.unit


class TestRetroObjectTag:
    def test_is_deterministic_in_the_project_id(self) -> None:
        assert retro_object_tag("proj-1") == retro_object_tag("proj-1")

    def test_differs_across_projects(self) -> None:
        assert retro_object_tag("proj-1") != retro_object_tag("proj-2")

    def test_is_namespaced(self) -> None:
        assert retro_object_tag("proj-1").startswith("objective:")


class TestOrgCategoryFor:
    def test_maps_procedure_and_convention(self) -> None:
        assert org_category_for("procedure") is OrgFactCategory.PROCEDURE
        assert org_category_for("convention") is OrgFactCategory.CONVENTION


class TestArgsToRetrospective:
    def test_parses_a_full_submission(self) -> None:
        draft = args_to_retrospective(
            {
                "summary": "Shipped the checkout hardening.",
                "org_learnings": [
                    {"content": "Prefer idempotent retries.", "kind": "convention"},
                    {"content": "Run the load test first.", "kind": "procedure"},
                ],
                "agent_learnings": [
                    {"agent_id": "agent-1", "content": "Ask for the spec earlier."},
                ],
            }
        )
        assert isinstance(draft, RetrospectiveDraft)
        assert draft.summary == "Shipped the checkout hardening."
        assert len(draft.org_learnings) == 2
        assert draft.org_learnings[0].kind == "convention"
        assert draft.agent_learnings[0].agent_id == "agent-1"

    def test_summary_only_is_valid(self) -> None:
        draft = args_to_retrospective({"summary": "It went fine."})
        assert draft.org_learnings == ()
        assert draft.agent_learnings == ()

    def test_missing_summary_raises(self) -> None:
        with pytest.raises(RetrospectiveParseError):
            args_to_retrospective({"org_learnings": []})

    def test_blank_summary_raises(self) -> None:
        with pytest.raises(RetrospectiveParseError):
            args_to_retrospective({"summary": "   "})

    def test_non_array_org_learnings_raises(self) -> None:
        with pytest.raises(RetrospectiveParseError):
            args_to_retrospective({"summary": "ok", "org_learnings": "nope"})

    def test_org_learning_missing_kind_raises(self) -> None:
        with pytest.raises(RetrospectiveParseError):
            args_to_retrospective(
                {"summary": "ok", "org_learnings": [{"content": "x"}]}
            )

    def test_agent_learning_missing_content_raises(self) -> None:
        with pytest.raises(RetrospectiveParseError):
            args_to_retrospective(
                {"summary": "ok", "agent_learnings": [{"agent_id": "agent-1"}]}
            )

    def test_too_many_org_learnings_is_a_retryable_parse_error(self) -> None:
        """The per-collection cap surfaces as a retryable parse error, not a crash."""
        with pytest.raises(RetrospectiveParseError):
            args_to_retrospective(
                {
                    "summary": "ok",
                    "org_learnings": [
                        {"content": f"lesson {i}", "kind": "procedure"}
                        for i in range(51)
                    ],
                }
            )


class TestBuildRetrospectiveTool:
    def test_names_the_terminal_tool(self) -> None:
        tool = build_retrospective_tool()
        assert tool.name == "submit_retrospective"

    def test_schema_requires_summary(self) -> None:
        tool = build_retrospective_tool()
        assert tool.parameters_schema["required"] == ["summary"]
