"""Tests for RequestProjectDecisionTool."""

import json
from typing import cast

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource
from synthorg.tools import decision_tool
from synthorg.tools.decision_tool import RequestProjectDecisionTool
from tests._shared import JsonDict

pytestmark = pytest.mark.unit


@pytest.fixture
def approval_store() -> ApprovalStore:
    return ApprovalStore()


@pytest.fixture
def tool(approval_store: ApprovalStore) -> RequestProjectDecisionTool:
    return RequestProjectDecisionTool(
        approval_store=approval_store,
        agent_id="agent-1",
        task_id="task-1",
    )


class TestToolCreation:
    """Tool creation and schema."""

    def test_name(self, tool: RequestProjectDecisionTool) -> None:
        assert tool.name == "request_project_decision"

    def test_has_question_and_options(self, tool: RequestProjectDecisionTool) -> None:
        schema = tool.parameters_schema
        assert schema is not None
        props = cast("JsonDict", schema)["properties"]
        assert "question" in props
        assert "options" in props
        assert "reversibility" in props
        assert schema["required"] == ["question", "options", "reversibility"]


def _rich_options() -> list[dict[str, object]]:
    """Two rich options (one recommended) for a decision fork."""
    return [
        {
            "id": "react",
            "title": "React",
            "summary": "Mature ecosystem, larger bundle, familiar to the team.",
            "recommended": True,
        },
        {
            "id": "svelte",
            "title": "Svelte",
            "summary": "Small bundle, compiler-based, smaller ecosystem.",
        },
    ]


class TestExecute:
    """Execution parks with the clarification + decision markers set."""

    async def test_creates_decision_item_with_options(
        self,
        tool: RequestProjectDecisionTool,
        approval_store: ApprovalStore,
    ) -> None:
        result = await tool.execute(
            arguments={
                "question": "Which web framework should we target?",
                "options": _rich_options(),
                "reversibility": "hard_to_reverse",
            },
        )
        assert not result.is_error
        assert result.metadata["requires_parking"] is True
        # Parks like a clarification (task -> AWAITING_INPUT) AND is a decision
        # (answer -> project-brain DECISION entry on resume).
        assert result.metadata["clarification"] is True
        assert result.metadata["decision"] is True
        assert result.metadata["action_type"] == "decision:project"

        item = await approval_store.get(
            cast("JsonDict", result.metadata)["approval_id"]
        )
        assert item is not None
        assert item.source is ApprovalSource.PARKED_CONTEXT
        assert item.risk_level is ApprovalRiskLevel.LOW
        assert item.action_type == "decision:project"
        assert item.description == "Which web framework should we target?"
        assert item.metadata["decision"] == "true"
        assert item.metadata["reversibility"] == "hard_to_reverse"
        assert result.metadata["reversibility"] == "hard_to_reverse"
        # The brain-record alternatives are the option titles.
        assert json.loads(item.metadata["options"]) == ["React", "Svelte"]
        # The rich per-option writeups ride on the evidence package the
        # operator picks from.
        assert item.evidence_package is not None
        opt_ids = [o.id for o in item.evidence_package.options]
        assert opt_ids == ["react", "svelte"]
        assert sum(o.recommended for o in item.evidence_package.options) == 1

    async def test_missing_options_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        # Every project decision must offer structured options; a bare question
        # with no options is rejected, never parked as an answerless item.
        result = await tool.execute(
            arguments={
                "question": "What should the release cadence be?",
                "reversibility": "reversible",
            },
        )
        assert result.is_error
        assert "Invalid decision arguments" in result.content

    async def test_reversibility_is_required(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        # Judging materiality is part of deciding to ask, so the agent states
        # it rather than the tool guessing a default on its behalf.
        result = await tool.execute(
            arguments={"question": "Framework?", "options": _rich_options()},
        )
        assert result.is_error
        assert "reversibility" in result.content

    async def test_option_titles_in_content(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        result = await tool.execute(
            arguments={
                "question": "Framework?",
                "options": _rich_options(),
                "reversibility": "reversible",
            },
        )
        assert "React, Svelte" in result.content

    async def test_long_question_yields_short_title(
        self,
        tool: RequestProjectDecisionTool,
        approval_store: ApprovalStore,
    ) -> None:
        long_question = "Which framework? " + "x" * 400
        result = await tool.execute(
            arguments={
                "question": long_question,
                "options": _rich_options(),
                "reversibility": "reversible",
            },
        )
        assert not result.is_error
        item = await approval_store.get(
            cast("JsonDict", result.metadata)["approval_id"]
        )
        assert item is not None
        assert item.evidence_package is not None
        # The title is a compact label; the full question rides on narrative.
        assert len(item.evidence_package.title) <= 120
        assert item.evidence_package.narrative == long_question
        assert item.description == long_question

    async def test_invalid_options_no_recommended_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": "a", "title": "A", "summary": "first"},
            {"id": "b", "title": "B", "summary": "second"},
        ]
        result = await tool.execute(
            arguments={
                "question": "Framework?",
                "options": options,
                "reversibility": "reversible",
            },
        )
        # The args model validator rejects at parse time.
        assert result.is_error
        assert "Invalid decision arguments" in result.content
        assert "recommended" in result.content

    async def test_two_recommended_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": "a", "title": "A", "summary": "first", "recommended": True},
            {"id": "b", "title": "B", "summary": "second", "recommended": True},
        ]
        result = await tool.execute(
            arguments={
                "question": "Framework?",
                "options": options,
                "reversibility": "reversible",
            },
        )
        assert result.is_error
        assert "recommended" in result.content

    async def test_duplicate_option_ids_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": "a", "title": "A", "summary": "first", "recommended": True},
            {"id": "a", "title": "B", "summary": "second"},
        ]
        result = await tool.execute(
            arguments={
                "question": "Framework?",
                "options": options,
                "reversibility": "reversible",
            },
        )
        assert result.is_error
        assert "duplicate option ids" in result.content

    async def test_too_many_options_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": f"o{i}", "title": f"O{i}", "summary": "x", "recommended": i == 0}
            for i in range(13)
        ]
        result = await tool.execute(
            arguments={
                "question": "Framework?",
                "options": options,
                "reversibility": "reversible",
            },
        )
        assert result.is_error
        assert "Invalid decision arguments" in result.content

    async def test_malformed_option_schema_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": "a", "title": "A", "recommended": True},  # missing summary
            {"id": "b", "title": "B", "summary": "second"},
        ]
        result = await tool.execute(
            arguments={
                "question": "Framework?",
                "options": options,
                "reversibility": "reversible",
            },
        )
        assert result.is_error
        assert "Invalid decision arguments" in result.content
        assert "summary" in result.content

    async def test_single_option_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": "a", "title": "A", "summary": "only one", "recommended": True}
        ]
        result = await tool.execute(
            arguments={
                "question": "Framework?",
                "options": options,
                "reversibility": "reversible",
            },
        )
        assert result.is_error
        assert "Invalid decision arguments" in result.content

    async def test_blank_question_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        # Valid options, so the parser cannot fail on a missing field and
        # mask a broken blank-question check.
        result = await tool.execute(
            arguments={
                "question": "   ",
                "options": _rich_options(),
                "reversibility": "reversible",
            }
        )
        assert result.is_error
        assert "question" in result.content

    async def test_store_error_returns_error_result(
        self,
        approval_store: ApprovalStore,
        tool: RequestProjectDecisionTool,
    ) -> None:
        async def _failing_add(item: object) -> None:
            msg = "Store unavailable"
            raise RuntimeError(msg)

        approval_store.add = _failing_add  # type: ignore[method-assign]

        result = await tool.execute(
            arguments={
                "question": "Framework?",
                "options": _rich_options(),
                "reversibility": "reversible",
            },
        )
        assert result.is_error
        assert "Failed to create decision request" in result.content


class TestOutputStyleRewritesReachPersistence:
    """What the boundary rewrote is what gets stored, resumed and shown.

    A rewrite applied only to the agent-facing confirmation would leave the
    operator reading, and the resumed run quoting, the text the output-style
    boundary had already ruled against.
    """

    @staticmethod
    def _rewrite_everything(monkeypatch: pytest.MonkeyPatch) -> None:
        """Stand in for an AUTO_REWRITE rule that changes every string."""

        def _guard(*texts: str) -> tuple[None, list[str]]:
            return None, [f"{text} [ok]" for text in texts]

        monkeypatch.setattr(decision_tool, "guard_question_text", _guard)

    async def test_question_and_options_persist_rewritten(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tool: RequestProjectDecisionTool,
        approval_store: ApprovalStore,
    ) -> None:
        self._rewrite_everything(monkeypatch)

        result = await tool.execute(
            arguments={
                "question": "Framework?",
                "options": _rich_options(),
                "reversibility": "reversible",
            },
        )
        assert not result.is_error

        item = await approval_store.get(str(result.metadata["approval_id"]))
        assert item is not None
        assert item.description == "Framework? [ok]"
        evidence = item.evidence_package
        assert evidence is not None
        assert evidence.narrative == "Framework? [ok]"
        assert [opt.title for opt in evidence.options] == ["React [ok]", "Svelte [ok]"]
        assert all(opt.summary.endswith(" [ok]") for opt in evidence.options)
        # The brain DECISION record reads its alternatives from this list, so
        # it has to carry the rewrite too.
        assert json.loads(str(item.metadata["options"])) == [
            "React [ok]",
            "Svelte [ok]",
        ]

    async def test_option_ids_and_recommendation_survive_the_rewrite(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tool: RequestProjectDecisionTool,
        approval_store: ApprovalStore,
    ) -> None:
        # The operator picks structurally by id, so a prose rewrite must not
        # disturb the fields the pick resolves against.
        self._rewrite_everything(monkeypatch)

        result = await tool.execute(
            arguments={
                "question": "Framework?",
                "options": _rich_options(),
                "reversibility": "reversible",
            },
        )
        item = await approval_store.get(str(result.metadata["approval_id"]))
        assert item is not None
        evidence = item.evidence_package
        assert evidence is not None
        assert [opt.id for opt in evidence.options] == ["react", "svelte"]
        assert [opt.recommended for opt in evidence.options] == [True, False]
