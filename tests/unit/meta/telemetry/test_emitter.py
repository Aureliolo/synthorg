"""Unit tests for the cross-deployment analytics emitter."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from synthorg.meta.chief_of_staff.models import ProposalOutcome
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import ImprovementProposal, RolloutResult
from synthorg.meta.telemetry.config import CrossDeploymentAnalyticsConfig
from synthorg.meta.telemetry.emitter import HttpAnalyticsEmitter
from tests._shared.fake_clock import FakeClock

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


class TestEmitterCloseEnqueueRace:
    """Regression tests for the close / enqueue race.

    Without this guarding, ``_enqueue`` only checks ``_closed``
    outside the lock. A producer that passes the outer guard still
    ``await`` s ``_ensure_flush_task`` and then takes the lock; if
    ``aclose()`` has
    set ``_closed`` and drained the buffer in the meantime, the event
    would be appended to a buffer that nothing was watching anymore --
    stranded forever.

    The fix re-checks ``_closed`` *inside* the lock and drops the
    event if ``aclose()`` already shut things down.
    """

    async def test_enqueue_after_close_inside_lock_drops_event(
        self,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        from unittest.mock import MagicMock

        from synthorg.meta.telemetry.models import AnonymizedOutcomeEvent

        em = HttpAnalyticsEmitter(
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        try:
            # Replace ``_ensure_flush_task`` with a no-op coroutine that
            # flips ``_closed`` AFTER the outer guard but BEFORE the
            # buffer-mutation lock is acquired. Delegating to
            # ``original_ensure`` would actually spawn the real
            # ``_periodic_flush`` background task and leak it past
            # test teardown -- the test only needs the closed-flag
            # flip; spawning the periodic task is irrelevant to the
            # assertion.
            async def race(*args: object, **kwargs: object) -> None:
                em._closed = True

            em._ensure_flush_task = race  # type: ignore[method-assign]
            # Use a spec'd Mock for the event so we don't depend on
            # ``AnonymizedOutcomeEvent``'s exact field set; we only
            # need something the buffer-append branch *would* enqueue
            # if the inner guard were missing.
            event = MagicMock(spec=AnonymizedOutcomeEvent)
            event.event_type = "proposal_decision"
            await em._enqueue(event)
            # Inner guard MUST drop the event; before the fix this
            # appended to a buffer that aclose() would never drain.
            assert em.pending_count == 0
        finally:
            # Close the httpx client so the test does not leave a live
            # ``httpx.AsyncClient`` past teardown. ``aclose`` is safe
            # to call even though we already flipped ``_closed``;
            # the client close is the only side-effect we care about.
            await em._client.aclose()

    async def test_enqueue_outer_guard_drops_event_when_already_closed(
        self,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        """Outer guard short-circuits before taking the lock."""
        from unittest.mock import MagicMock

        from synthorg.meta.telemetry.models import AnonymizedOutcomeEvent

        em = HttpAnalyticsEmitter(
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
        )
        try:
            em._closed = True
            event = MagicMock(spec=AnonymizedOutcomeEvent)
            event.event_type = "proposal_decision"
            await em._enqueue(event)
            assert em.pending_count == 0
        finally:
            await em._client.aclose()


class TestEmitterClockSeam:
    """FakeClock drives flush-throttle bookkeeping deterministically."""

    async def test_init_records_clock_monotonic(
        self,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
    ) -> None:
        """``__init__`` reads ``_last_flush_at`` from the injected clock."""
        fake = FakeClock()
        fake.advance(42.5)
        em = HttpAnalyticsEmitter(
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
            clock=fake,
        )
        try:
            assert em._last_flush_at == 42.5
        finally:
            await em._client.aclose()

    async def test_flush_updates_last_flush_at_via_clock(
        self,
        analytics_config: CrossDeploymentAnalyticsConfig,
        self_improvement_config: SelfImprovementConfig,
        sample_outcome: ProposalOutcome,
        sample_proposal: ImprovementProposal,
    ) -> None:
        """``flush()`` advances ``_last_flush_at`` to the new clock reading."""
        fake = FakeClock()
        em = HttpAnalyticsEmitter(
            analytics_config=analytics_config,
            self_improvement_config=self_improvement_config,
            builtin_rule_names=BUILTIN_RULE_NAMES,
            clock=fake,
        )
        try:
            with patch.object(em, "_send_batch", new_callable=AsyncMock):
                start = em._last_flush_at
                fake.advance(7.5)
                await em.emit_decision(
                    sample_outcome,
                    proposal=sample_proposal,
                )
                await em.flush()
                assert em._last_flush_at == start + 7.5
        finally:
            await em._client.aclose()
