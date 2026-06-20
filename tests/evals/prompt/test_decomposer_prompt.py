"""Prompt eval: decomposer sampling contract + probe-parse behaviour."""

import inspect

import pytest

from synthorg.core.task import AcceptanceCriterion
from synthorg.engine.quality.decomposers.llm import _probe_rejection_reason
from tests.evals.prompt._harness import (
    LabelledExample,
    assert_accuracy_at_least,
    run_grader,
)


@pytest.mark.unit
class TestDecomposerPromptContract:
    """Guard rails for the LLM criteria decomposer prompt surface."""

    def test_temperature_is_zero(self) -> None:
        """Decomposer must run at temperature=0 for deterministic splits."""
        import re

        from synthorg.engine.quality.decomposers.llm import (
            LLMCriteriaDecomposer,
        )

        source = inspect.getsource(LLMCriteriaDecomposer)
        # Require ``temperature`` bound to exactly ``0`` / ``0.0`` via
        # ``=`` or ``:`` (dataclass / Field / keyword argument forms).
        # Raw substring matching would succeed on a comment that
        # merely mentions ``temperature=0.0`` without binding it.
        pattern = r"temperature\s*(?::\s*[\w\[\], ]*)?\s*=\s*0(?:\.0+)?\b"
        assert re.search(pattern, source), (
            "LLMCriteriaDecomposer must pin temperature=0.0 "
            "(checked via a binding pattern, not substring)"
        )

    def test_top_p_is_pinned_to_one(self) -> None:
        """Decomposer must pin ``top_p=1.0`` (no nucleus truncation).

        ``temperature=0`` removes sampling randomness, but a drifted
        ``top_p`` would silently re-truncate the distribution; pin both
        so determinism is fully specified.
        """
        import re

        from synthorg.engine.quality.decomposers.llm import (
            LLMCriteriaDecomposer,
        )

        source = inspect.getsource(LLMCriteriaDecomposer)
        pattern = r"top_p\s*(?::\s*[\w\[\], ]*)?\s*=\s*1(?:\.0+)?\b"
        assert re.search(pattern, source), (
            "LLMCriteriaDecomposer must pin top_p=1.0 "
            "(checked via a binding pattern, not substring)"
        )


@pytest.mark.unit
class TestDecomposerProbeParse:
    """Labelled eval for the deterministic probe-validation contract.

    The prompt asks the LLM to emit probes shaped
    ``{source_criterion_index, probe_text}``; this grades that
    :func:`_probe_rejection_reason` accepts a well-formed probe and
    rejects (with a reason the caller drops) every malformed shape --
    the parse contract the prompt's tool schema is written against.
    """

    _CRITERIA = (AcceptanceCriterion(description="output is correct"),)

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(
            name="valid_probe_accepted",
            inp={"source_criterion_index": 0, "probe_text": "Is the output correct?"},
            expected=None,
        ),
        LabelledExample(
            name="non_mapping_rejected",
            inp="not a probe",
            expected=str,
        ),
        LabelledExample(
            name="index_out_of_range_rejected",
            inp={"source_criterion_index": 9, "probe_text": "ok?"},
            expected=str,
        ),
        LabelledExample(
            name="blank_probe_text_rejected",
            inp={"source_criterion_index": 0, "probe_text": "   "},
            expected=str,
        ),
        LabelledExample(
            name="missing_index_rejected",
            inp={"probe_text": "ok?"},
            expected=str,
        ),
    )

    def test_probe_validation_matches_labelled_examples(self) -> None:
        """Every labelled (raw probe, expected outcome) pair grades."""

        def _grade(actual_input: object, expected: object) -> bool:
            reason = _probe_rejection_reason(
                actual_input,
                criteria=self._CRITERIA,
                per_criterion_counts={0: 0},
                cap=5,
            )
            if expected is None:
                return reason is None
            return isinstance(reason, str)

        outcome = run_grader(self.EXAMPLES, _grade)
        assert_accuracy_at_least(outcome, 1.0)

    def test_per_criterion_cap_rejects(self) -> None:
        """A probe past the per-criterion cap is rejected with a reason."""
        reason = _probe_rejection_reason(
            {"source_criterion_index": 0, "probe_text": "Is the output correct?"},
            criteria=self._CRITERIA,
            per_criterion_counts={0: 5},
            cap=5,
        )
        assert reason == "per-criterion cap reached"
