"""Tests for PerformanceTracker collaboration enhancements.

Tests override precedence and LLM sampler integration in the tracker.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.collaboration_override_store import (
    CollaborationOverrideStore,
)
from synthorg.hr.performance.collaboration_protocol import CollaborationScoringStrategy
from synthorg.hr.performance.llm_calibration_sampler import LlmCalibrationSampler
from synthorg.hr.performance.models import CollaborationOverride
from synthorg.hr.performance.tracker import PerformanceTracker

from .conftest import make_collab_metric

NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


async def _drain_background(tracker: PerformanceTracker) -> None:
    """Await all background sampling tasks on the tracker."""
    if tracker._background_tasks:
        await asyncio.gather(*tracker._background_tasks)


@pytest.mark.unit
class TestOverridePrecedence:
    """Override takes precedence in get_collaboration_score."""

    async def test_active_override_returned(self) -> None:
        """Active override is returned instead of computed score."""
        override_store = CollaborationOverrideStore()
        override_store.set_override(
            CollaborationOverride(
                agent_id=NotBlankStr("agent-001"),
                score=9.5,
                reason=NotBlankStr("Exceptional work"),
                applied_by=NotBlankStr("manager"),
                applied_at=NOW,
            ),
        )
        tracker = PerformanceTracker(override_store=override_store)

        result = await tracker.get_collaboration_score(
            NotBlankStr("agent-001"),
        )

        assert result.score == 9.5
        assert result.strategy_name == "human_override"
        assert result.confidence == 1.0
        assert result.override_active is True

    async def test_expired_override_falls_through(self) -> None:
        """Expired override falls through to behavioral strategy."""
        override_store = CollaborationOverrideStore()
        override_store.set_override(
            CollaborationOverride(
                agent_id=NotBlankStr("agent-001"),
                score=9.5,
                reason=NotBlankStr("Old override"),
                applied_by=NotBlankStr("manager"),
                applied_at=NOW - timedelta(days=10),
                expires_at=NOW - timedelta(days=5),
            ),
        )
        tracker = PerformanceTracker(override_store=override_store)

        result = await tracker.get_collaboration_score(
            NotBlankStr("agent-001"),
            now=NOW,
        )

        # Falls through to behavioral strategy, returns neutral score
        # since there are no collaboration records.
        assert result.score == 5.0
        assert result.strategy_name == "behavioral_telemetry"
        assert result.override_active is False

    async def test_no_override_uses_strategy(self) -> None:
        """Without an override, the behavioral strategy is used."""
        override_store = CollaborationOverrideStore()
        tracker = PerformanceTracker(override_store=override_store)

        # Record some collaboration data so strategy computes something.
        await tracker.record_collaboration_event(
            make_collab_metric(
                agent_id="agent-001",
                recorded_at=NOW,
                delegation_success=True,
            ),
        )

        result = await tracker.get_collaboration_score(
            NotBlankStr("agent-001"),
        )

        assert result.strategy_name == "behavioral_telemetry"
        assert result.override_active is False

    async def test_no_override_store_uses_strategy(self) -> None:
        """Tracker without override store uses strategy normally."""
        tracker = PerformanceTracker()

        result = await tracker.get_collaboration_score(
            NotBlankStr("agent-001"),
        )

        assert result.strategy_name == "behavioral_telemetry"
        assert result.override_active is False

    async def test_override_reflected_in_snapshot(self) -> None:
        """Override is reflected in get_snapshot."""
        override_store = CollaborationOverrideStore()
        override_store.set_override(
            CollaborationOverride(
                agent_id=NotBlankStr("agent-001"),
                score=8.0,
                reason=NotBlankStr("Good teamwork"),
                applied_by=NotBlankStr("manager"),
                applied_at=NOW,
            ),
        )
        tracker = PerformanceTracker(override_store=override_store)

        snapshot = await tracker.get_snapshot(
            NotBlankStr("agent-001"),
            now=NOW,
        )

        assert snapshot.overall_collaboration_score == 8.0


@pytest.mark.unit
class TestSamplerIntegration:
    """LLM sampler invocation during record_collaboration_event."""

    async def test_sampler_invoked_when_conditions_met(self) -> None:
        """Sampler is invoked for records with interaction_summary."""
        mock_sampler = MagicMock(spec=LlmCalibrationSampler)
        mock_sampler.should_sample.return_value = True
        mock_sampler.sample = AsyncMock(return_value=None)
        tracker = PerformanceTracker(sampler=mock_sampler)

        record = make_collab_metric(
            recorded_at=NOW,
            delegation_success=True,
            interaction_summary="Agent delegated task",
        )
        await tracker.record_collaboration_event(record)
        await _drain_background(tracker)

        mock_sampler.should_sample.assert_called_once()
        mock_sampler.sample.assert_called_once()

    async def test_sampler_skipped_without_summary(self) -> None:
        """Sampler is not invoked for records without summary."""
        mock_sampler = MagicMock(spec=LlmCalibrationSampler)
        mock_sampler.should_sample.return_value = True
        mock_sampler.sample = AsyncMock()
        tracker = PerformanceTracker(sampler=mock_sampler)

        record = make_collab_metric(
            recorded_at=NOW,
            delegation_success=True,
        )
        await tracker.record_collaboration_event(record)
        await _drain_background(tracker)

        mock_sampler.should_sample.assert_not_called()
        mock_sampler.sample.assert_not_called()

    async def test_sampler_skipped_when_should_sample_false(self) -> None:
        """Sampler.sample() not called when should_sample() is False."""
        mock_sampler = MagicMock(spec=LlmCalibrationSampler)
        mock_sampler.should_sample.return_value = False
        mock_sampler.sample = AsyncMock()
        tracker = PerformanceTracker(sampler=mock_sampler)

        record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Some interaction",
        )
        await tracker.record_collaboration_event(record)
        await _drain_background(tracker)

        mock_sampler.should_sample.assert_called_once()
        mock_sampler.sample.assert_not_called()

    async def test_no_sampler_does_not_error(self) -> None:
        """Tracker without sampler records events normally."""
        tracker = PerformanceTracker()

        record = make_collab_metric(
            recorded_at=NOW,
            delegation_success=True,
            interaction_summary="Some interaction",
        )
        await tracker.record_collaboration_event(record)

        # No error, record stored.
        records = tracker.get_collaboration_metrics(
            agent_id=NotBlankStr("agent-001"),
        )
        assert len(records) == 1

    async def test_sampler_failure_does_not_block_recording(self) -> None:
        """If sampler.sample() raises, the record is still stored."""
        mock_sampler = MagicMock(spec=LlmCalibrationSampler)
        mock_sampler.should_sample.return_value = True
        mock_sampler.sample = AsyncMock(side_effect=RuntimeError("LLM down"))
        tracker = PerformanceTracker(sampler=mock_sampler)

        record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Some interaction",
        )
        await tracker.record_collaboration_event(record)
        await _drain_background(tracker)

        # Record should still be stored.
        records = tracker.get_collaboration_metrics(
            agent_id=NotBlankStr("agent-001"),
        )
        assert len(records) == 1

    async def test_behavioral_strategy_failure_does_not_block(self) -> None:
        """If behavioral strategy.score() raises, the record is stored."""
        mock_strategy = AsyncMock(spec=CollaborationScoringStrategy)
        mock_strategy.score = AsyncMock(
            side_effect=RuntimeError("Strategy error"),
        )
        mock_strategy.name = "broken_strategy"
        mock_sampler = MagicMock(spec=LlmCalibrationSampler)
        mock_sampler.should_sample.return_value = True
        mock_sampler.sample = AsyncMock()
        tracker = PerformanceTracker(
            collaboration_strategy=mock_strategy,
            sampler=mock_sampler,
        )

        record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Some interaction",
        )
        await tracker.record_collaboration_event(record)
        await _drain_background(tracker)

        # Record should still be stored despite strategy failure.
        records = tracker.get_collaboration_metrics(
            agent_id=NotBlankStr("agent-001"),
        )
        assert len(records) == 1
        # Sampler.sample() should NOT have been called.
        mock_sampler.sample.assert_not_called()
