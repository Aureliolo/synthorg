"""Tests for plan parsing utilities."""

import json

import pytest

from synthorg.engine.plan_parsing import parse_plan
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage

pytestmark = pytest.mark.timeout(30)


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, cost_usd=0.001)


def _response(content: str) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=_usage(),
        model="test-model-001",
    )


@pytest.mark.unit
class TestParsePlanJson:
    """JSON plan parsing."""

    def test_valid_json(self) -> None:
        content = json.dumps(
            {
                "steps": [
                    {
                        "step_number": 1,
                        "description": "Do A",
                        "expected_outcome": "A done",
                    },
                ],
            }
        )
        plan = parse_plan(_response(content), "exec-1", "task")
        assert plan is not None
        assert len(plan.steps) == 1
        assert plan.steps[0].description == "Do A"

    def test_markdown_code_fence(self) -> None:
        inner = json.dumps(
            {
                "steps": [
                    {
                        "step_number": 1,
                        "description": "Fenced step",
                        "expected_outcome": "Done",
                    },
                ],
            }
        )
        content = f"```json\n{inner}\n```"
        plan = parse_plan(_response(content), "exec-1", "task")
        assert plan is not None
        assert plan.steps[0].description == "Fenced step"

    def test_non_dict_top_level_returns_none(self) -> None:
        plan = parse_plan(_response("[1, 2, 3]"), "exec-1", "task")
        assert plan is None

    def test_missing_steps_key_returns_none(self) -> None:
        plan = parse_plan(
            _response(json.dumps({"plan": "something"})),
            "exec-1",
            "task",
        )
        assert plan is None

    def test_empty_steps_list_returns_none(self) -> None:
        plan = parse_plan(
            _response(json.dumps({"steps": []})),
            "exec-1",
            "task",
        )
        assert plan is None

    def test_step_without_description_returns_none(self) -> None:
        plan = parse_plan(
            _response(
                json.dumps({"steps": [{"step_number": 1, "expected_outcome": "x"}]})
            ),
            "exec-1",
            "task",
        )
        assert plan is None

    def test_step_not_dict_returns_none(self) -> None:
        plan = parse_plan(
            _response(json.dumps({"steps": ["step 1"]})),
            "exec-1",
            "task",
        )
        assert plan is None


@pytest.mark.unit
class TestParsePlanText:
    """Text fallback plan parsing."""

    def test_numbered_list(self) -> None:
        content = "1. Research the problem\n2. Implement solution\n3. Test it"
        plan = parse_plan(_response(content), "exec-1", "task")
        assert plan is not None
        assert len(plan.steps) == 3
        assert plan.steps[0].description == "Research the problem"

    def test_no_numbered_lines_returns_none(self) -> None:
        plan = parse_plan(
            _response("Just some random text with no steps"),
            "exec-1",
            "task",
        )
        assert plan is None


@pytest.mark.unit
class TestParsePlanEdgeCases:
    """Edge cases."""

    def test_empty_content_returns_none(self) -> None:
        plan = parse_plan(_response(""), "exec-1", "task")
        assert plan is None

    def test_whitespace_only_returns_none(self) -> None:
        plan = parse_plan(_response("   \n  "), "exec-1", "task")
        assert plan is None

    def test_revision_number_passed_through(self) -> None:
        content = json.dumps(
            {
                "steps": [
                    {
                        "step_number": 1,
                        "description": "Step",
                        "expected_outcome": "Done",
                    },
                ],
            }
        )
        plan = parse_plan(
            _response(content),
            "exec-1",
            "task",
            revision_number=3,
        )
        assert plan is not None
        assert plan.revision_number == 3

    def test_multi_step_renumbering(self) -> None:
        content = json.dumps(
            {
                "steps": [
                    {
                        "step_number": 5,
                        "description": "A",
                        "expected_outcome": "x",
                    },
                    {
                        "step_number": 10,
                        "description": "B",
                        "expected_outcome": "y",
                    },
                ],
            }
        )
        plan = parse_plan(_response(content), "exec-1", "task")
        assert plan is not None
        # Steps are renumbered sequentially regardless of input
        assert plan.steps[0].step_number == 1
        assert plan.steps[1].step_number == 2
