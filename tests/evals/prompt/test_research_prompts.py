"""Prompt eval: research planner / synthesiser / triage temperature + drift.

All three research surfaces complete through ``research/_llm.py``, which pins
``_DETERMINISTIC_TEMPERATURE = 0.0``. Each carries a distinct system prompt;
all three are fingerprinted against silent drift.
"""

import json

import pytest

from synthorg.core.boundary import parse_typed
from synthorg.providers.protocol import CompletionProvider
from synthorg.research._args import (
    PlannerOutput,
    SynthesisOutput,
    TriageOutput,
)
from synthorg.research.errors import ResearchRunError, ResearchSynthesisError
from synthorg.research.planning.llm_planner import LlmQueryPlanner
from synthorg.research.synthesis.llm_synthesizer import LlmSynthesizer
from synthorg.research.triage.llm import _TRIAGE_BOUNDARY
from tests._shared import mock_of
from tests.evals.prompt._harness import (
    LabelledExample,
    assert_accuracy_at_least,
    fingerprint_prompt,
    run_grader,
)

_VALID_PLAN = json.dumps(
    {
        "research_angle": "comparative cost and capability lens",
        "sub_queries": [
            {
                "source_type": "web",
                "query_text": "current pricing for the candidate options",
                "intent": "establish a baseline cost comparison",
            }
        ],
    }
)

_VALID_SYNTHESIS = json.dumps(
    {
        "title": "Cost and capability comparison",
        "summary": "A concise executive summary of the compared options.",
        "claims": [
            {
                "text": "Option A is materially cheaper than option B",
                "claim_type": "comparison",
                "confidence": 0.8,
                "ref_ids": ["src-1"],
            }
        ],
    }
)

_VALID_TRIAGE = json.dumps(
    {
        "verdicts": [
            {
                "ref_id": "src-1",
                "authority": "expert",
                "domain_alignment": 0.9,
                "score": 0.85,
            }
        ]
    }
)


@pytest.mark.unit
class TestResearchTemperatureContract:
    """The shared research completion path must stay deterministic."""

    def test_deterministic_temperature_is_zero(self) -> None:
        from synthorg.research._llm import _DETERMINISTIC_TEMPERATURE

        assert _DETERMINISTIC_TEMPERATURE == 0.0, (
            "research/_llm.py must keep _DETERMINISTIC_TEMPERATURE pinned at 0.0."
        )


@pytest.mark.unit
class TestResearchPlannerPrompt:
    """Guard rails for the research planner prompt."""

    PINNED_FP = "a5af4dbbbba753a2"

    def test_prompt_fingerprint_is_pinned(self) -> None:
        from synthorg.research.planning.llm_planner import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"research planner prompt drifted: {fp!r} != {self.PINNED_FP!r}."
        )


@pytest.mark.unit
class TestResearchSynthesizerPrompt:
    """Guard rails for the research synthesiser prompt."""

    PINNED_FP = "fa2ad9a20a4988e9"

    def test_prompt_fingerprint_is_pinned(self) -> None:
        from synthorg.research.synthesis.llm_synthesizer import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"research synthesiser prompt drifted: {fp!r} != {self.PINNED_FP!r}."
        )


@pytest.mark.unit
class TestResearchTriagePrompt:
    """Guard rails for the research triage prompt."""

    PINNED_FP = "0eb4ceeabd787cda"

    def test_prompt_fingerprint_is_pinned(self) -> None:
        from synthorg.research.triage.llm import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"research triage prompt drifted: {fp!r} != {self.PINNED_FP!r}."
        )


@pytest.mark.unit
class TestResearchPlannerParse:
    """Labelled eval for the planner's JSON structure-parse contract."""

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(name="valid_plan", inp=_VALID_PLAN, expected=True),
        LabelledExample(name="not_json", inp="not a plan", expected=False),
        # Empty sub_queries violates min_length=1.
        LabelledExample(
            name="empty_sub_queries",
            inp=json.dumps({"research_angle": "lens", "sub_queries": []}),
            expected=False,
        ),
        # Missing required research_angle.
        LabelledExample(
            name="missing_angle",
            inp=json.dumps({"sub_queries": []}),
            expected=False,
        ),
    )

    def test_planner_parse_matches_labelled_examples(self) -> None:
        planner = LlmQueryPlanner(
            provider=mock_of[CompletionProvider](),
            model="test-small-001",
        )

        def _grade(actual_input: object, expected: object) -> bool:
            assert isinstance(actual_input, str)
            try:
                result = planner._parse(actual_input)
            except ResearchRunError:
                return expected is False
            return expected is True and isinstance(result, PlannerOutput)

        assert_accuracy_at_least(run_grader(self.EXAMPLES, _grade), 1.0)


@pytest.mark.unit
class TestResearchSynthesizerParse:
    """Labelled eval for the synthesiser's JSON output-parse contract."""

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(name="valid_synthesis", inp=_VALID_SYNTHESIS, expected=True),
        LabelledExample(name="not_json", inp="not a report", expected=False),
        # Empty claims violates min_length=1.
        LabelledExample(
            name="empty_claims",
            inp=json.dumps({"title": "t", "summary": "s", "claims": []}),
            expected=False,
        ),
    )

    def test_synthesizer_parse_matches_labelled_examples(self) -> None:
        from synthorg.research.synthesis.citation_binder import CitationBinder

        synthesizer = LlmSynthesizer(
            provider=mock_of[CompletionProvider](),
            model="test-small-001",
            binder=mock_of[CitationBinder](),
        )

        def _grade(actual_input: object, expected: object) -> bool:
            assert isinstance(actual_input, str)
            try:
                result = synthesizer._parse(actual_input)
            except ResearchSynthesisError:
                return expected is False
            return expected is True and isinstance(result, SynthesisOutput)

        assert_accuracy_at_least(run_grader(self.EXAMPLES, _grade), 1.0)


@pytest.mark.unit
class TestResearchTriageParse:
    """Labelled eval for the triage verdict-parse contract.

    The triage stage parses via ``parse_typed(_TRIAGE_BOUNDARY, obj,
    TriageOutput)``; this grades that contract directly with valid and
    malformed verdict payloads.
    """

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(name="valid_triage", inp=_VALID_TRIAGE, expected=True),
        # Empty verdicts is valid (default=()).
        LabelledExample(
            name="empty_verdicts_valid",
            inp=json.dumps({"verdicts": []}),
            expected=True,
        ),
        # Out-of-range score violates le=1.0.
        LabelledExample(
            name="score_out_of_range",
            inp=json.dumps(
                {
                    "verdicts": [
                        {
                            "ref_id": "s1",
                            "authority": "expert",
                            "domain_alignment": 0.5,
                            "score": 1.5,
                        }
                    ]
                }
            ),
            expected=False,
        ),
        # Unknown authority bucket.
        LabelledExample(
            name="bad_authority",
            inp=json.dumps(
                {
                    "verdicts": [
                        {
                            "ref_id": "s1",
                            "authority": "supreme",
                            "domain_alignment": 0.5,
                            "score": 0.5,
                        }
                    ]
                }
            ),
            expected=False,
        ),
    )

    def test_triage_parse_matches_labelled_examples(self) -> None:
        def _grade(actual_input: object, expected: object) -> bool:
            assert isinstance(actual_input, str)
            try:
                obj = json.loads(actual_input)
                result = parse_typed(_TRIAGE_BOUNDARY, obj, TriageOutput)
            except ValueError, json.JSONDecodeError:
                return expected is False
            return expected is True and isinstance(result, TriageOutput)

        assert_accuracy_at_least(run_grader(self.EXAMPLES, _grade), 1.0)
