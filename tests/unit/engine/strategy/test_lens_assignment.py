"""Unit tests for lens assignment strategies."""

import pytest

from synthorg.engine.strategy.lens_assignment import (
    DiversityMaximizingAssigner,
)


class TestDiversityMaximizingAssigner:
    """Tests for DiversityMaximizingAssigner."""

    @pytest.mark.unit
    def test_empty_participants_returns_empty_dict(self) -> None:
        """Empty participant list should return empty dict."""
        assigner = DiversityMaximizingAssigner()
        result = assigner.assign((), ("lens_a", "lens_b"))
        assert result == {}

    @pytest.mark.unit
    def test_empty_lenses_returns_empty_dict(self) -> None:
        """Empty lens list should return empty dict."""
        assigner = DiversityMaximizingAssigner()
        result = assigner.assign(("agent_1", "agent_2"), ())
        assert result == {}

    @pytest.mark.unit
    def test_both_empty_returns_empty_dict(self) -> None:
        """Both empty lists should return empty dict."""
        assigner = DiversityMaximizingAssigner()
        result = assigner.assign((), ())
        assert result == {}

    @pytest.mark.unit
    def test_single_participant_single_lens(self) -> None:
        """Single participant gets the single lens."""
        assigner = DiversityMaximizingAssigner()
        result = assigner.assign(("agent_1",), ("contrarian",))
        assert result == {"agent_1": "contrarian"}

    @pytest.mark.unit
    def test_equal_participants_and_lenses(self) -> None:
        """Participants and lenses of equal length map one-to-one."""
        assigner = DiversityMaximizingAssigner()
        result = assigner.assign(
            ("agent_1", "agent_2", "agent_3"),
            ("contrarian", "risk_focused", "cost_focused"),
        )
        assert result == {
            "agent_1": "contrarian",
            "agent_2": "risk_focused",
            "agent_3": "cost_focused",
        }

    @pytest.mark.unit
    def test_more_participants_than_lenses(self) -> None:
        """More participants than lenses wraps around."""
        assigner = DiversityMaximizingAssigner()
        result = assigner.assign(
            ("agent_1", "agent_2", "agent_3", "agent_4"),
            ("lens_a", "lens_b"),
        )
        assert result == {
            "agent_1": "lens_a",
            "agent_2": "lens_b",
            "agent_3": "lens_a",
            "agent_4": "lens_b",
        }

    @pytest.mark.unit
    def test_more_lenses_than_participants(self) -> None:
        """More lenses than participants uses only first N lenses."""
        assigner = DiversityMaximizingAssigner()
        result = assigner.assign(
            ("agent_1", "agent_2"),
            ("lens_a", "lens_b", "lens_c", "lens_d"),
        )
        assert result == {
            "agent_1": "lens_a",
            "agent_2": "lens_b",
        }

    @pytest.mark.unit
    def test_all_participants_get_a_lens(self) -> None:
        """Every participant receives exactly one lens."""
        assigner = DiversityMaximizingAssigner()
        participants = (
            "alice",
            "bob",
            "charlie",
            "diana",
            "eve",
        )
        lenses = ("lens_1", "lens_2", "lens_3")
        result = assigner.assign(participants, lenses)

        # Check all participants are in the result
        assert set(result.keys()) == set(participants)
        # Check each has exactly one lens
        for pid in participants:
            assert pid in result
            assert isinstance(result[pid], str)

    @pytest.mark.unit
    def test_round_robin_cycling(self) -> None:
        """Verify round-robin cycling pattern."""
        assigner = DiversityMaximizingAssigner()
        participants = ("p1", "p2", "p3", "p4", "p5")
        lenses = ("a", "b")
        result = assigner.assign(participants, lenses)

        # Expected: p1->a, p2->b, p3->a, p4->b, p5->a
        assert result["p1"] == "a"
        assert result["p2"] == "b"
        assert result["p3"] == "a"
        assert result["p4"] == "b"
        assert result["p5"] == "a"

    @pytest.mark.unit
    def test_preserves_participant_id_strings(self) -> None:
        """Verify participant IDs are preserved exactly."""
        assigner = DiversityMaximizingAssigner()
        participants = ("agent_alpha", "agent_beta")
        lenses = ("lens_x", "lens_y")
        result = assigner.assign(participants, lenses)

        assert "agent_alpha" in result
        assert "agent_beta" in result

    @pytest.mark.unit
    def test_preserves_lens_name_strings(self) -> None:
        """Verify lens names are preserved exactly."""
        assigner = DiversityMaximizingAssigner()
        participants = ("p1", "p2")
        lenses = ("custom_lens_1", "custom_lens_2")
        result = assigner.assign(participants, lenses)

        assert result["p1"] == "custom_lens_1"
        assert result["p2"] == "custom_lens_2"

    @pytest.mark.unit
    def test_large_participant_set(self) -> None:
        """Works with large numbers of participants."""
        assigner = DiversityMaximizingAssigner()
        participants = tuple(f"agent_{i}" for i in range(100))
        lenses = ("lens_1", "lens_2", "lens_3")
        result = assigner.assign(participants, lenses)

        assert len(result) == 100
        # Verify cycling: every third should be lens_1
        assert result["agent_0"] == "lens_1"
        assert result["agent_3"] == "lens_1"
        assert result["agent_6"] == "lens_1"
