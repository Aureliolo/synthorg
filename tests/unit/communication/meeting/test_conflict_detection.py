"""Tests for conflict detection strategies.

Tests all detector implementations with various inputs including:
- Simple keyword detection
- Structured JSON comparison
- LLM judgment parsing
- Auto-detection with format selection
- Hybrid strategies with fallback
"""

import json

import pytest

from synthorg.communication.meeting.conflict_detection import (
    AutoDetector,
    EmbeddingSimilarityDetector,
    HybridDetector,
    KeywordConflictDetector,
    LlmJudgeDetector,
    StructuredComparisonDetector,
)
from synthorg.communication.meeting.embedder import HashingTextEmbedder

_DIVERGENT = json.dumps(
    {
        "positions": [
            {"recommendation": "approve the budget increase for cloud scaling"},
            {"recommendation": "reject all spending freeze hiring postpone forever"},
        ]
    }
)
_AGREEING = json.dumps(
    {
        "positions": [
            {"recommendation": "approve the budget increase for cloud scaling"},
            {"recommendation": "approve the budget increase for cloud scaling"},
        ]
    }
)


@pytest.mark.unit
class TestKeywordConflictDetector:
    """Tests for KeywordConflictDetector."""

    def test_detects_conflicts_uppercase_marker(self) -> None:
        """Detect conflicts with exact uppercase marker."""
        detector = KeywordConflictDetector()
        response = "Analysis: CONFLICTS: YES - agents disagree on approach"
        assert detector.detect(response) is True

    def test_detects_conflicts_lowercase_marker(self) -> None:
        """Detect conflicts with lowercase marker (case-insensitive)."""
        detector = KeywordConflictDetector()
        response = "conflicts: yes - there are disagreements"
        assert detector.detect(response) is True

    def test_detects_conflicts_mixed_case_marker(self) -> None:
        """Detect conflicts with mixed-case marker."""
        detector = KeywordConflictDetector()
        response = "Conflicts: Yes - engineering and product disagree"
        assert detector.detect(response) is True

    def test_no_conflicts_with_no_marker(self) -> None:
        """No conflicts when marker is absent."""
        detector = KeywordConflictDetector()
        response = "All agents agree on the approach"
        assert detector.detect(response) is False

    def test_no_conflicts_with_conflicts_no(self) -> None:
        """No conflicts when explicitly saying CONFLICTS: NO."""
        detector = KeywordConflictDetector()
        response = "CONFLICTS: NO - consensus reached"
        assert detector.detect(response) is False

    def test_detects_marker_in_middle_of_text(self) -> None:
        """Marker can appear anywhere in response."""
        detector = KeywordConflictDetector()
        response = "Starting analysis... CONFLICTS: YES ...ending analysis"
        assert detector.detect(response) is True

    def test_empty_response(self) -> None:
        """Empty response means no conflicts."""
        detector = KeywordConflictDetector()
        assert detector.detect("") is False


@pytest.mark.unit
class TestStructuredComparisonDetector:
    """Tests for StructuredComparisonDetector."""

    def test_detects_conflicts_with_differing_fields(self) -> None:
        """Detect conflicts when position fields differ."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"approach": "A", "timeline": "1 week"},
                    {"approach": "B", "timeline": "1 week"},
                ]
            }
        )
        assert detector.detect(response) is True

    def test_no_conflicts_with_identical_fields(self) -> None:
        """No conflicts when all positions agree."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"approach": "A", "timeline": "1 week"},
                    {"approach": "A", "timeline": "1 week"},
                ]
            }
        )
        assert detector.detect(response) is False

    def test_handles_singular_position_field(self) -> None:
        """Handles "position" (singular) field."""
        detector = StructuredComparisonDetector()
        # Single position doesn't create conflict
        response = json.dumps({"position": {"approach": "A"}})
        assert detector.detect(response) is False

    def test_detects_conflicts_with_multiple_field_differences(self) -> None:
        """Detect conflicts with multiple differing fields."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"approach": "A", "priority": "high", "resource": "team-x"},
                    {"approach": "B", "priority": "low", "resource": "team-y"},
                ]
            }
        )
        assert detector.detect(response) is True

    def test_no_conflicts_with_three_positions_all_matching(self) -> None:
        """No conflicts with multiple positions that all agree."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"stance": "yes", "confidence": 0.9},
                    {"stance": "yes", "confidence": 0.9},
                    {"stance": "yes", "confidence": 0.9},
                ]
            }
        )
        assert detector.detect(response) is False

    def test_detects_conflicts_with_three_positions_some_differing(self) -> None:
        """Detect conflicts when any pair differs."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"stance": "yes"},
                    {"stance": "yes"},
                    {"stance": "no"},
                ]
            }
        )
        assert detector.detect(response) is True

    def test_ignores_identity_keys_only_differences(self) -> None:
        """No conflict when positions differ only by identity/metadata keys."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {
                        "agent_id": "agent-1",
                        "role": "engineer",
                        "id": "pos-1",
                        "metadata": {"ts": 1},
                        "recommendation": "approve",
                    },
                    {
                        "agent_id": "agent-2",
                        "role": "designer",
                        "id": "pos-2",
                        "metadata": {"ts": 2},
                        "recommendation": "approve",
                    },
                ]
            }
        )
        assert detector.detect(response) is False

    def test_handles_missing_positions_field(self) -> None:
        """Gracefully handle missing positions field."""
        detector = StructuredComparisonDetector()
        response = json.dumps({"analysis": "some analysis"})
        assert detector.detect(response) is False

    def test_handles_empty_positions_list(self) -> None:
        """Gracefully handle empty positions list."""
        detector = StructuredComparisonDetector()
        response = json.dumps({"positions": []})
        assert detector.detect(response) is False

    def test_handles_single_position_in_list(self) -> None:
        """Gracefully handle single position (no comparison needed)."""
        detector = StructuredComparisonDetector()
        response = json.dumps({"positions": [{"approach": "A"}]})
        assert detector.detect(response) is False

    def test_handles_invalid_json(self) -> None:
        """Gracefully handle invalid JSON."""
        detector = StructuredComparisonDetector()
        response = "This is not JSON at all"
        assert detector.detect(response) is False

    def test_handles_json_with_non_dict_positions(self) -> None:
        """Handle positions field that's not a list of dicts."""
        detector = StructuredComparisonDetector()
        response = json.dumps({"positions": ["string1", "string2"]})
        assert detector.detect(response) is False

    def test_detects_conflict_from_different_field_keys(self) -> None:
        """Detect conflict when positions have different field keys."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"approach": "A"},
                    {"resource": "team-x"},
                ]
            }
        )
        # Different keys means different values (None vs actual value)
        assert detector.detect(response) is True

    def test_handles_json_embedded_in_text(self) -> None:
        """Extract and parse JSON embedded in text."""
        detector = StructuredComparisonDetector()
        response = (
            "Here is my analysis:\n"
            '{"positions": [{"stance": "yes"}, {"stance": "no"}]}\n'
            "End of response"
        )
        assert detector.detect(response) is True

    def test_handles_nested_json_structures(self) -> None:
        """Handle nested JSON objects in positions."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"details": {"approach": "A"}},
                    {"details": {"approach": "B"}},
                ]
            }
        )
        # "details" fields with different dicts => conflict detected
        assert detector.detect(response) is True

    def test_handles_null_values_in_positions(self) -> None:
        """Handle null values in position fields."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"stance": "yes", "comment": None},
                    {"stance": "yes", "comment": "My thoughts"},
                ]
            }
        )
        assert detector.detect(response) is True


@pytest.mark.unit
class TestLlmJudgeDetector:
    """Tests for LlmJudgeDetector."""

    def test_detects_conflicts_from_json_boolean(self) -> None:
        """Parse conflicts from JSON boolean field."""
        detector = LlmJudgeDetector()
        response = json.dumps({"conflicts": True, "areas": ["scope", "timeline"]})
        assert detector.detect(response) is True

    def test_no_conflicts_from_json_boolean_false(self) -> None:
        """Parse no-conflicts from JSON boolean field."""
        detector = LlmJudgeDetector()
        response = json.dumps({"conflicts": False, "areas": []})
        assert detector.detect(response) is False

    def test_detects_conflicts_from_judgment_field(self) -> None:
        """Parse conflicts from "judgment" field."""
        detector = LlmJudgeDetector()
        response = json.dumps(
            {
                "judgment": "conflict detected between teams",
                "resolution": "escalate",
            }
        )
        assert detector.detect(response) is True

    def test_no_conflicts_from_judgment_field_without_conflict(self) -> None:
        """Parse no-conflicts from judgment field."""
        detector = LlmJudgeDetector()
        response = json.dumps({"judgment": "no issues found"})
        assert detector.detect(response) is False

    def test_detects_judge_conflict_marker(self) -> None:
        """Detect from JUDGE: CONFLICT marker."""
        detector = LlmJudgeDetector()
        response = "JUDGE: CONFLICT\nReasons: timing and scope disagree"
        assert detector.detect(response) is True

    def test_detects_judge_no_conflict_marker(self) -> None:
        """Detect no-conflict from JUDGE: NO_CONFLICT marker."""
        detector = LlmJudgeDetector()
        response = "JUDGE: NO_CONFLICT\nAll positions are compatible"
        assert detector.detect(response) is False

    def test_fallback_to_conflicts_yes_keyword(self) -> None:
        """Fallback to CONFLICTS: YES keyword."""
        detector = LlmJudgeDetector()
        response = "CONFLICTS: YES - disagreement on priorities"
        assert detector.detect(response) is True

    def test_handles_invalid_json_with_keyword(self) -> None:
        """Fallback to keyword when JSON parsing fails."""
        detector = LlmJudgeDetector()
        response = "Invalid json { broken CONFLICTS: YES"
        assert detector.detect(response) is True

    def test_handles_invalid_json_without_keyword(self) -> None:
        """Fallback to False when no keywords found."""
        detector = LlmJudgeDetector()
        response = "No structured format or keywords here"
        assert detector.detect(response) is False

    def test_handles_case_insensitive_judgment(self) -> None:
        """Handle case variations in judgment keywords."""
        detector = LlmJudgeDetector()
        response = json.dumps({"judgment": "Conflict between proposals"})
        assert detector.detect(response) is True

    def test_handles_empty_response(self) -> None:
        """Handle empty response gracefully."""
        detector = LlmJudgeDetector()
        assert detector.detect("") is False

    @pytest.mark.parametrize(
        "judgment",
        [
            "no conflict detected",
            "no conflicts",
            "no conflicts.",
            "no_conflict",
            "No Conflict Found",
        ],
    )
    def test_negative_judgment_returns_false(self, judgment: str) -> None:
        """Negated judgment phrases must not trigger conflict."""
        detector = LlmJudgeDetector()
        response = json.dumps({"judgment": judgment})
        assert detector.detect(response) is False

    def test_prefers_json_boolean_over_keyword(self) -> None:
        """Prefer JSON parsing when both formats present."""
        detector = LlmJudgeDetector()
        # JSON says no conflict, but text has "CONFLICTS: YES"
        response = json.dumps({"conflicts": False}) + "\nCONFLICTS: YES"
        # JSON parsing succeeds, so keyword is not checked
        assert detector.detect(response) is False


@pytest.mark.unit
class TestEmbeddingSimilarityDetector:
    """Tests for EmbeddingSimilarityDetector."""

    def test_detects_divergent_positions(self) -> None:
        """Dissimilar positions (low cosine) are flagged as conflicting."""
        detector = EmbeddingSimilarityDetector(embedder=HashingTextEmbedder())
        assert detector.detect(_DIVERGENT) is True

    def test_agreeing_positions_not_conflicting(self) -> None:
        """Identical positions (cosine 1.0) are not flagged."""
        detector = EmbeddingSimilarityDetector(embedder=HashingTextEmbedder())
        assert detector.detect(_AGREEING) is False

    def test_single_position_not_conflicting(self) -> None:
        """Fewer than two positions cannot conflict."""
        detector = EmbeddingSimilarityDetector(embedder=HashingTextEmbedder())
        response = json.dumps({"position": {"recommendation": "ship it"}})
        assert detector.detect(response) is False

    def test_two_empty_positions_not_conflicting(self) -> None:
        """Two identity-only positions both embed to zero; not a conflict."""
        detector = EmbeddingSimilarityDetector(embedder=HashingTextEmbedder())
        response = json.dumps(
            {
                "positions": [
                    {"agent_id": "agent-1", "role": "engineer"},
                    {"agent_id": "agent-2", "role": "designer"},
                ]
            }
        )
        assert detector.detect(response) is False

    def test_non_json_not_conflicting(self) -> None:
        """A response with no positions cannot conflict."""
        detector = EmbeddingSimilarityDetector(embedder=HashingTextEmbedder())
        assert detector.detect("free-form prose, no positions") is False

    def test_accepts_similarity_threshold(self) -> None:
        """Accepts custom similarity threshold."""
        detector = EmbeddingSimilarityDetector(
            embedder=HashingTextEmbedder(), similarity_threshold=0.5
        )
        assert detector.similarity_threshold == 0.5

    def test_default_threshold_is_0_7(self) -> None:
        """Default similarity threshold is 0.7."""
        detector = EmbeddingSimilarityDetector(embedder=HashingTextEmbedder())
        assert detector.similarity_threshold == 0.7


@pytest.mark.unit
class TestHybridDetector:
    """Tests for HybridDetector."""

    def test_detects_conflicts_via_keyword_fallback(self) -> None:
        """Detect conflicts through keyword fallback when embedding agrees."""
        detector = HybridDetector(embedder=HashingTextEmbedder())
        response = "CONFLICTS: YES - scope disagreement"
        assert detector.detect(response) is True

    def test_detects_conflicts_via_embedding(self) -> None:
        """Detect conflicts through the embedding leg (no keyword marker)."""
        detector = HybridDetector(embedder=HashingTextEmbedder())
        assert detector.detect(_DIVERGENT) is True

    def test_no_conflicts_via_keyword_fallback(self) -> None:
        """No conflicts when keyword absent and positions agree."""
        detector = HybridDetector(embedder=HashingTextEmbedder())
        response = "All positions align on timing"
        assert detector.detect(response) is False

    def test_accepts_custom_threshold(self) -> None:
        """Accepts custom similarity threshold."""
        detector = HybridDetector(
            embedder=HashingTextEmbedder(), similarity_threshold=0.5
        )
        assert detector.embedding_detector.similarity_threshold == 0.5

    def test_has_both_detectors(self) -> None:
        """Initializes both embedding and keyword detectors."""
        detector = HybridDetector(embedder=HashingTextEmbedder())
        assert isinstance(detector.embedding_detector, EmbeddingSimilarityDetector)
        assert isinstance(detector.keyword_detector, KeywordConflictDetector)

    def test_handles_json_response_via_keyword(self) -> None:
        """Handle JSON responses via keyword fallback."""
        detector = HybridDetector(embedder=HashingTextEmbedder())
        response = json.dumps(
            {
                "positions": [{"stance": "yes"}],
                "note": "CONFLICTS: YES",
            }
        )
        assert detector.detect(response) is True


@pytest.mark.unit
class TestAutoDetector:
    """Tests for AutoDetector."""

    def test_selects_structured_detector_for_json_positions(self) -> None:
        """Select StructuredComparisonDetector for JSON with positions."""
        detector = AutoDetector()
        response = json.dumps(
            {
                "positions": [
                    {"approach": "A"},
                    {"approach": "B"},
                ]
            }
        )
        assert detector.detect(response) is True

    def test_selects_structured_detector_for_singular_position(self) -> None:
        """Select StructuredComparisonDetector for 'position' field."""
        detector = AutoDetector()
        response = json.dumps({"position": {"approach": "A"}})
        assert detector.detect(response) is False

    def test_falls_back_to_keyword_for_non_json(self) -> None:
        """Fall back to keyword detector for non-JSON responses."""
        detector = AutoDetector()
        response = "CONFLICTS: YES - timeline mismatch"
        assert detector.detect(response) is True

    def test_falls_back_to_keyword_when_no_position_field(self) -> None:
        """Fall back to keyword when JSON lacks position field."""
        detector = AutoDetector()
        response = json.dumps({"analysis": "something", "note": "CONFLICTS: YES"})
        assert detector.detect(response) is True

    def test_detects_no_conflict_structured(self) -> None:
        """AutoDetector handles no-conflict structured responses."""
        detector = AutoDetector()
        response = json.dumps(
            {
                "positions": [
                    {"consensus": True},
                    {"consensus": True},
                ]
            }
        )
        assert detector.detect(response) is False

    def test_detects_no_conflict_keyword(self) -> None:
        """AutoDetector handles no-conflict keyword responses."""
        detector = AutoDetector()
        response = "Analysis complete. No disagreements found."
        assert detector.detect(response) is False

    def test_handles_invalid_json_gracefully(self) -> None:
        """Handle invalid JSON gracefully."""
        detector = AutoDetector()
        response = "{ broken json CONFLICTS: YES"
        # Falls back to keyword detector
        assert detector.detect(response) is True

    def test_has_both_detectors(self) -> None:
        """Initializes both detector strategies."""
        detector = AutoDetector()
        assert isinstance(detector.structured_detector, StructuredComparisonDetector)
        assert isinstance(detector.keyword_detector, KeywordConflictDetector)

    def test_complex_nested_json_with_positions(self) -> None:
        """Handle complex nested JSON with positions."""
        detector = AutoDetector()
        response = json.dumps(
            {
                "metadata": {"timestamp": "2026-04-09"},
                "positions": [
                    {"proposed_solution": "Option A"},
                    {"proposed_solution": "Option B"},
                ],
                "summary": "Two viable paths forward",
            }
        )
        assert detector.detect(response) is True

    def test_json_without_positions_falls_back(self) -> None:
        """JSON without positions field falls back to keyword."""
        detector = AutoDetector()
        response = json.dumps(
            {
                "summary": "Analysis complete",
                "status": "CONFLICTS: YES",
            }
        )
        assert detector.detect(response) is True


@pytest.mark.unit
class TestDetectorProtocolCompliance:
    """Tests to verify all detectors implement ConflictDetector protocol."""

    def test_keyword_detector_has_detect_method(self) -> None:
        """KeywordConflictDetector has detect(response_content: str) -> bool."""
        detector = KeywordConflictDetector()
        assert callable(detector.detect)
        result = detector.detect("test")
        assert isinstance(result, bool)

    def test_structured_detector_has_detect_method(self) -> None:
        """StructuredComparisonDetector has detect(response_content: str) -> bool."""
        detector = StructuredComparisonDetector()
        assert callable(detector.detect)
        result = detector.detect("test")
        assert isinstance(result, bool)

    def test_llm_judge_detector_has_detect_method(self) -> None:
        """LlmJudgeDetector has detect(response_content: str) -> bool."""
        detector = LlmJudgeDetector()
        assert callable(detector.detect)
        result = detector.detect("test")
        assert isinstance(result, bool)

    def test_embedding_detector_has_detect_method(self) -> None:
        """EmbeddingSimilarityDetector has detect(response_content: str) -> bool."""
        detector = EmbeddingSimilarityDetector(embedder=HashingTextEmbedder())
        assert callable(detector.detect)
        result = detector.detect("test")
        assert isinstance(result, bool)

    def test_hybrid_detector_has_detect_method(self) -> None:
        """HybridDetector has detect(response_content: str) -> bool."""
        detector = HybridDetector(embedder=HashingTextEmbedder())
        assert callable(detector.detect)
        result = detector.detect("test")
        assert isinstance(result, bool)

    def test_auto_detector_has_detect_method(self) -> None:
        """AutoDetector has detect(response_content: str) -> bool."""
        detector = AutoDetector()
        assert callable(detector.detect)
        result = detector.detect("test")
        assert isinstance(result, bool)


@pytest.mark.unit
class TestEdgeCases:
    """Tests for edge cases and robustness."""

    def test_keyword_detector_with_whitespace_variations(self) -> None:
        """Handle whitespace variations around marker."""
        detector = KeywordConflictDetector()
        assert detector.detect("CONFLICTS: YES") is True
        assert detector.detect("CONFLICTS:YES") is True
        assert detector.detect("CONFLICTS : YES") is True
        assert detector.detect("  CONFLICTS: YES  ") is True

    def test_structured_detector_with_numeric_values(self) -> None:
        """Handle numeric field values in positions."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"priority": 1, "cost": 100},
                    {"priority": 2, "cost": 100},
                ]
            }
        )
        assert detector.detect(response) is True

    def test_structured_detector_with_boolean_values(self) -> None:
        """Handle boolean field values in positions."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"proceed": True},
                    {"proceed": False},
                ]
            }
        )
        assert detector.detect(response) is True

    def test_llm_judge_with_mixed_case_judgment(self) -> None:
        """Handle mixed-case judgment keywords."""
        detector = LlmJudgeDetector()
        response = json.dumps({"judgment": "CONFLICT detected"})
        assert detector.detect(response) is True

    def test_auto_detector_with_json_array_not_object(self) -> None:
        """Handle JSON array (not object) gracefully."""
        detector = AutoDetector()
        response = json.dumps(
            [
                {"stance": "yes"},
                {"stance": "no"},
            ]
        )
        # JSON array doesn't have "positions" field, falls back to keyword
        assert detector.detect(response) is False

    def test_structured_detector_with_very_large_json(self) -> None:
        """Handle large JSON with many positions."""
        detector = StructuredComparisonDetector()
        positions = [{"id": i, "stance": "yes" if i < 5 else "no"} for i in range(100)]
        response = json.dumps({"positions": positions})
        assert detector.detect(response) is True

    def test_keyword_detector_with_unicode_content(self) -> None:
        """Handle unicode content in response."""
        detector = KeywordConflictDetector()
        response = "Analysis: 分析結果 CONFLICTS: YES チーム"
        assert detector.detect(response) is True

    def test_structured_detector_with_special_characters(self) -> None:
        """Handle special characters in JSON values."""
        detector = StructuredComparisonDetector()
        response = json.dumps(
            {
                "positions": [
                    {"approach": "Use $100 allocation"},
                    {"approach": "Use @200 allocation"},
                ]
            }
        )
        assert detector.detect(response) is True
