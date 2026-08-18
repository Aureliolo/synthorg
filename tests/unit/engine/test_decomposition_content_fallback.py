"""A plan the model wrote is read, whatever it wrapped the plan in.

The invariant: the content fallback exists for a model that cannot call the
tool, and the prompt asks such a model to "respond with a JSON object". A
model answering that instruction writes a sentence and then the object, so a
parser that accepts only a bare object or a fenced one refuses the plan it
asked for and reports that the model produced nothing.

Retrying cannot fix it: the same prompt to the same model returns the same
shape, so three attempts buy latency and nothing else. A live run spent 68
seconds proving that before failing.
"""

import json
from typing import Final

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.llm_parse import parse_content_response
from synthorg.engine.errors import DecompositionError
from synthorg.providers.models import CompletionResponse, TokenUsage

pytestmark = pytest.mark.unit

_ROSTER: Final[tuple[NotBlankStr, ...]] = (NotBlankStr("Backend Developer"),)


def _plan_args() -> dict[str, object]:
    return {
        "subtasks": [
            {
                "id": "sub-0",
                "title": "Build the core loop",
                "description": "Blocks fall, move, rotate, and lines clear",
                "dependencies": [],
                "estimated_complexity": "medium",
                "stakes": "normal",
                "required_skills": ["javascript"],
                "required_role": "Backend Developer",
                "expected_artifacts": ["src/engine.js"],
                "acceptance_criteria": ["the core loop is playable"],
                "kind": "work",
            }
        ],
        "task_structure": "sequential",
        "coordination_topology": "auto",
    }


def _content(text: str) -> CompletionResponse:
    return CompletionResponse(
        content=text,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=100, output_tokens=50, cost=0.01),
        model="test-model-001",
    )


def _parse(text: str) -> object:
    return parse_content_response(_content(text), "task-1", _ROSTER)


class TestContentFallbackReadsWhatTheModelWrote:
    def test_a_bare_object(self) -> None:
        plan = _parse(json.dumps(_plan_args()))
        assert len(plan.subtasks) == 1  # type: ignore[attr-defined]

    def test_an_object_inside_a_fence(self) -> None:
        plan = _parse(f"```json\n{json.dumps(_plan_args())}\n```")
        assert len(plan.subtasks) == 1  # type: ignore[attr-defined]

    def test_an_object_after_a_sentence(self) -> None:
        # The shape a live run produced: the instruction says "respond with a
        # JSON object", and a model that cannot call the tool obliges by
        # writing one, in a sentence.
        plan = _parse(f"Here is the plan:\n\n{json.dumps(_plan_args())}")
        assert len(plan.subtasks) == 1  # type: ignore[attr-defined]

    def test_an_object_between_two_sentences(self) -> None:
        plan = _parse(
            f"Sure. {json.dumps(_plan_args())}\n\nLet me know if you want changes."
        )
        assert len(plan.subtasks) == 1  # type: ignore[attr-defined]

    def test_a_brace_inside_a_description_does_not_end_the_object(self) -> None:
        # Naive scanning stops at the first closing brace, which for a plan is
        # somewhere inside its first subtask.
        args = _plan_args()
        subtasks = args["subtasks"]
        assert isinstance(subtasks, list)
        first = subtasks[0]
        assert isinstance(first, dict)
        first["description"] = "Clear a line {sic} and score it"
        plan = _parse(f"Plan below.\n{json.dumps(args)}")
        assert len(plan.subtasks) == 1  # type: ignore[attr-defined]

    def test_an_escaped_quote_inside_a_string_does_not_end_it(self) -> None:
        args = _plan_args()
        subtasks = args["subtasks"]
        assert isinstance(subtasks, list)
        first = subtasks[0]
        assert isinstance(first, dict)
        first["title"] = 'The "core" loop \\ and a brace }'
        plan = _parse(f"Plan below.\n{json.dumps(args)}")
        assert len(plan.subtasks) == 1  # type: ignore[attr-defined]


class TestContentFallbackStillRefusesWhatIsNotAPlan:
    def test_prose_with_no_object_at_all(self) -> None:
        with pytest.raises(DecompositionError, match="Failed to parse JSON"):
            _parse("I cannot decompose this task.")

    def test_an_object_that_never_closes(self) -> None:
        with pytest.raises(DecompositionError, match="Failed to parse JSON"):
            _parse('Here you go: {"subtasks": [')

    def test_a_bare_array_is_not_a_plan(self) -> None:
        # A plan is an object carrying ``subtasks``; a list of subtasks with
        # no envelope is missing the structure and topology the plan needs.
        with pytest.raises(DecompositionError):
            _parse('[{"id": "sub-0"}]')

    def test_empty_content(self) -> None:
        with pytest.raises(DecompositionError):
            _parse("   ")
