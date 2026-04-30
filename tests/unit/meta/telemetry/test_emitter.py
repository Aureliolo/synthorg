"""Unit tests for the cross-deployment analytics emitter."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from synthorg.meta.chief_of_staff.models import ProposalOutcome
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import ImprovementProposal, RolloutResult
from synthorg.meta.telemetry.config import CrossDeploymentAnalyticsConfig
from synthorg.meta.telemetry.emitter import HttpAnalyticsEmitter

from .conftest import BUILTIN_RULE_NAMES

pytestmark = pytest.mark.unit


@pytest.fixture
def emitter(
    analytics_config: CrossDeploymentAnalyticsConfig,
    self_improvement_config: SelfImprovementConfig,
) -> HttpAnalyticsEmitter:
    """Create an emitter with test config."""
    return HttpAnalyticsEmitter(
        analytics_config=analytics_config,
        self_improvement_config=self_improvement_config,
        builtin_rule_names=BUILTIN_RULE_NAMES,
    )


class TestEmitterBuffering:
    """Tests for event buffering behavior."""

    async def test_emit_decision_buffers_event(
        self,
        emitter: HttpAnalyticsEmitter,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
    ) -> None:
        with patch.object(emitter, "_send_batch", new_callable=AsyncMock):
            await emitter.emit_decision(
                sample_outcome,
                proposal=sample_proposal,
            )
            assert emitter.pending_count == 1

    async def test_emit_rollout_buffers_event(
        self,
        emitter: HttpAnalyticsEmitter,
        sample_rollout_result: RolloutResult,
        sample_proposal: ImprovementProposal,
    ) -> None:
        with patch.object(emitter, "_send_batch", new_callable=AsyncMock):
            await emitter.emit_rollout(
                sample_rollout_result,
                proposal=sample_proposal,
            )
            assert emitter.pending_count == 1

    async def test_batch_threshold_triggers_flush(
        self,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        small_batch = analytics_config.model_copy(
            update={"batch_size": 3},
        )
        si = self_improvement_config.model_copy(
            update={"cross_deployment_analytics": small_batch},
        )
        em = HttpAnalyticsEmitter(
            analytics_config=small_batch,
            self_improvement_config=si,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        with patch.object(em, "_send_batch", new_callable=AsyncMock) as mock_send:
            for _ in range(3):
                await em.emit_decision(
                    sample_outcome,
                    proposal=sample_proposal,
                )
            assert mock_send.await_count >= 1
            # Buffer should be cleared after flush.
            assert em.pending_count == 0

    async def test_below_threshold_no_flush(
        self,
        emitter: HttpAnalyticsEmitter,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
    ) -> None:
        with patch.object(emitter, "_send_batch", new_callable=AsyncMock) as mock_send:
            await emitter.emit_decision(
                sample_outcome,
                proposal=sample_proposal,
            )
            # batch_size=10, only 1 event, no flush.
            mock_send.assert_not_awaited()

    async def test_periodic_flush_task_created(
        self,
        emitter: HttpAnalyticsEmitter,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
    ) -> None:
        with patch.object(emitter, "_send_batch", new_callable=AsyncMock):
            assert emitter._flush_task is None
            await emitter.emit_decision(
                sample_outcome,
                proposal=sample_proposal,
            )
            # Background flush task should be created on first enqueue.
            assert emitter._flush_task is not None


class TestEmitterFlush:
    """Tests for explicit flush and close."""

    async def test_flush_sends_buffered_events(
        self,
        emitter: HttpAnalyticsEmitter,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
    ) -> None:
        with patch.object(emitter, "_send_batch", new_callable=AsyncMock) as mock_send:
            await emitter.emit_decision(
                sample_outcome,
                proposal=sample_proposal,
            )
            await emitter.flush()
            mock_send.assert_awaited_once()
            assert emitter.pending_count == 0

    async def test_flush_noop_when_empty(
        self,
        emitter: HttpAnalyticsEmitter,
    ) -> None:
        with patch.object(emitter, "_send_batch", new_callable=AsyncMock) as mock_send:
            await emitter.flush()
            mock_send.assert_not_awaited()

    async def test_close_flushes_and_closes_client(
        self,
        emitter: HttpAnalyticsEmitter,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
    ) -> None:
        with patch.object(emitter, "_send_batch", new_callable=AsyncMock) as mock_send:
            await emitter.emit_decision(
                sample_outcome,
                proposal=sample_proposal,
            )
            await emitter.aclose()
            mock_send.assert_awaited_once()

    async def test_async_context_manager_calls_aclose(
        self,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
    ) -> None:
        """``async with`` invokes ``aclose()`` on exit."""
        em = HttpAnalyticsEmitter(
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        with patch.object(em, "_send_batch", new_callable=AsyncMock) as mock_send:
            async with em as emitter:
                await emitter.emit_decision(
                    sample_outcome,
                    proposal=sample_proposal,
                )
            # After the ``async with`` exits, ``aclose()`` ran which
            # flushes the buffered event via ``_send_batch``.
        mock_send.assert_awaited_once()
        assert em._closed is True


class TestEmitterHttpBehavior:
    """Tests for HTTP POST behavior."""

    async def test_successful_post(
        self,
        emitter: HttpAnalyticsEmitter,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
    ) -> None:
        mock_response = httpx.Response(200, json={"ingested": 1})
        with patch.object(
            emitter._client,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await emitter.emit_decision(
                sample_outcome,
                proposal=sample_proposal,
            )
            await emitter.flush()
            assert emitter.pending_count == 0

    async def test_retry_on_5xx(
        self,
        emitter: HttpAnalyticsEmitter,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
    ) -> None:
        responses = [
            httpx.Response(503),
            httpx.Response(200, json={"ingested": 1}),
        ]
        call_count = 0

        async def mock_post(*args: object, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        with (
            patch.object(emitter._client, "post", side_effect=mock_post),
            patch(
                "synthorg.meta.telemetry.emitter.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            await emitter.emit_decision(
                sample_outcome,
                proposal=sample_proposal,
            )
            await emitter.flush()
            assert call_count == 2

    async def test_drop_on_4xx(
        self,
        emitter: HttpAnalyticsEmitter,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
    ) -> None:
        mock_response = httpx.Response(400, json={"error": "bad request"})
        with patch.object(
            emitter._client,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_post:
            await emitter.emit_decision(
                sample_outcome,
                proposal=sample_proposal,
            )
            await emitter.flush()
            # Only one attempt -- no retry on 4xx.
            mock_post.assert_awaited_once()
            assert emitter.pending_count == 0

    async def test_emit_failure_does_not_raise(
        self,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        """Emission errors are logged, not raised."""
        # Use batch_size=1 to trigger immediate flush on emit.
        small = analytics_config.model_copy(update={"batch_size": 1})
        si = self_improvement_config.model_copy(
            update={"cross_deployment_analytics": small},
        )
        em = HttpAnalyticsEmitter(
            analytics_config=small,
            self_improvement_config=si,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        with (
            patch.object(
                em._client,
                "post",
                side_effect=httpx.ConnectError("connection refused"),
            ),
            patch(
                "synthorg.meta.telemetry.emitter.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            # Must not raise despite HTTP failure.
            await em.emit_decision(
                sample_outcome,
                proposal=sample_proposal,
            )
            # Buffer cleared by flush attempt (events sent to _send_batch).
            assert em.pending_count == 0
