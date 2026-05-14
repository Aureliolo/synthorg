"""Tests for quota degradation resolution (FALLBACK, QUEUE, ALERT)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from synthorg.budget.degradation import (
    DegradationResult,
    PreFlightResult,
    resolve_degradation,
)
from synthorg.budget.errors import QuotaExhaustedError
from synthorg.budget.quota import (
    DegradationAction,
    DegradationConfig,
    QuotaCheckResult,
    QuotaLimit,
    QuotaSnapshot,
    QuotaWindow,
    SubscriptionConfig,
)
from synthorg.budget.quota_tracker import QuotaTracker

# Frozen reference time for deterministic delay computation in QUEUE tests.
_FROZEN_NOW = datetime(2026, 3, 20, 12, 0, 0, tzinfo=UTC)


def _frozen_datetime_mock() -> MagicMock:
    """Build a mock datetime that returns _FROZEN_NOW from now()."""
    mock_dt = MagicMock(wraps=datetime)
    mock_dt.now = MagicMock(return_value=_FROZEN_NOW)
    return mock_dt


# ── Helpers ────────────────────────────────────────────────────────


def _denied_result(
    provider: str = "primary",
    *,
    windows: tuple[QuotaWindow, ...] = (QuotaWindow.PER_HOUR,),
) -> QuotaCheckResult:
    return QuotaCheckResult(
        allowed=False,
        provider_name=provider,
        reason=f"{provider} per_hour: requests 60/60",
        exhausted_windows=windows,
    )


def _make_tracker(
    providers: dict[str, int],
) -> QuotaTracker:
    """Build a QuotaTracker with per-hour request quotas.

    Args:
        providers: Mapping of provider name to max_requests.
    """
    subs: dict[str, SubscriptionConfig] = {}
    for name, max_req in providers.items():
        subs[name] = SubscriptionConfig(
            quotas=(
                QuotaLimit(
                    window=QuotaWindow.PER_HOUR,
                    max_requests=max_req,
                ),
            ),
        )
    return QuotaTracker(subscriptions=subs)


async def _exhaust_provider(
    tracker: QuotaTracker,
    provider: str,
    count: int,
) -> None:
    """Record enough usage to exhaust a provider's quota."""
    for _ in range(count):
        await tracker.record_usage(provider)


# ── Result model tests ─────────────────────────────────────────────


@pytest.mark.unit
class TestDegradationResult:
    """Tests for the DegradationResult model."""

    def test_frozen(self) -> None:
        result = DegradationResult(
            original_provider="a",
            effective_provider="b",
            action_taken=DegradationAction.FALLBACK,
        )
        with pytest.raises(ValidationError):
            result.original_provider = "c"  # type: ignore[misc]

    def test_defaults(self) -> None:
        result = DegradationResult(
            original_provider="a",
            effective_provider="a",
            action_taken=DegradationAction.QUEUE,
        )
        assert result.wait_seconds == 0.0

    def test_all_fields(self) -> None:
        result = DegradationResult(
            original_provider="primary",
            effective_provider="fallback",
            action_taken=DegradationAction.FALLBACK,
            wait_seconds=0.0,
        )
        assert result.original_provider == "primary"
        assert result.effective_provider == "fallback"
        assert result.action_taken == DegradationAction.FALLBACK


@pytest.mark.unit
class TestPreFlightResult:
    """Tests for the PreFlightResult model."""

    def test_defaults(self) -> None:
        result = PreFlightResult()
        assert result.effective_provider is None
        assert result.degradation is None

    def test_with_degradation(self) -> None:
        deg = DegradationResult(
            original_provider="a",
            effective_provider="b",
            action_taken=DegradationAction.FALLBACK,
        )
        result = PreFlightResult(degradation=deg)
        assert result.effective_provider == "b"
        assert result.degradation is deg

    def test_frozen(self) -> None:
        result = PreFlightResult()
        with pytest.raises(ValidationError):
            result.effective_provider = "x"  # type: ignore[misc]


# ── FALLBACK strategy tests ───────────────────────────────────────


@pytest.mark.unit
class TestFallbackStrategy:
    """Tests for FALLBACK degradation strategy."""

    async def test_returns_first_available_provider(self) -> None:
        tracker = _make_tracker({"primary": 5, "fallback-a": 100})
        await _exhaust_provider(tracker, "primary", 5)

        result = await resolve_degradation(
            provider_name="primary",
            quota_result=_denied_result("primary"),
            degradation_config=DegradationConfig(
                strategy=DegradationAction.FALLBACK,
                fallback_providers=("fallback-a",),
            ),
            quota_tracker=tracker,
        )

        assert result.effective_provider == "fallback-a"
        assert result.original_provider == "primary"
        assert result.action_taken == DegradationAction.FALLBACK

    async def test_skips_exhausted_providers(self) -> None:
        tracker = _make_tracker(
            {
                "primary": 5,
                "fallback-a": 5,
                "fallback-b": 100,
            }
        )
        await _exhaust_provider(tracker, "primary", 5)
        await _exhaust_provider(tracker, "fallback-a", 5)

        result = await resolve_degradation(
            provider_name="primary",
            quota_result=_denied_result("primary"),
            degradation_config=DegradationConfig(
                strategy=DegradationAction.FALLBACK,
                fallback_providers=("fallback-a", "fallback-b"),
            ),
            quota_tracker=tracker,
        )

        assert result.effective_provider == "fallback-b"

    async def test_raises_when_all_exhausted(self) -> None:
        tracker = _make_tracker(
            {
                "primary": 5,
                "fallback-a": 5,
            }
        )
        await _exhaust_provider(tracker, "primary", 5)
        await _exhaust_provider(tracker, "fallback-a", 5)

        with pytest.raises(
            QuotaExhaustedError,
            match="All fallback providers exhausted",
        ) as exc_info:
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.FALLBACK,
                    fallback_providers=("fallback-a",),
                ),
                quota_tracker=tracker,
            )

        assert exc_info.value.provider_name == "primary"
        assert exc_info.value.degradation_action == DegradationAction.FALLBACK

    async def test_raises_when_no_providers_configured(self) -> None:
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        with pytest.raises(
            QuotaExhaustedError,
            match="No fallback providers configured",
        ):
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.FALLBACK,
                    fallback_providers=(),
                ),
                quota_tracker=tracker,
            )

    async def test_checks_providers_in_order(self) -> None:
        """First available in the list wins, even if later ones also work."""
        tracker = _make_tracker(
            {
                "primary": 5,
                "fallback-a": 100,
                "fallback-b": 100,
            }
        )
        await _exhaust_provider(tracker, "primary", 5)

        result = await resolve_degradation(
            provider_name="primary",
            quota_result=_denied_result("primary"),
            degradation_config=DegradationConfig(
                strategy=DegradationAction.FALLBACK,
                fallback_providers=("fallback-a", "fallback-b"),
            ),
            quota_tracker=tracker,
        )

        assert result.effective_provider == "fallback-a"

    async def test_passes_estimated_tokens(self) -> None:
        """Estimated tokens are forwarded to each fallback quota check."""
        tracker = _make_tracker({"primary": 5, "fallback-a": 100})
        await _exhaust_provider(tracker, "primary", 5)

        calls: list[int] = []
        original_check = tracker.check_quota

        async def _spy(
            name: str,
            *,
            estimated_tokens: int = 0,
        ) -> QuotaCheckResult:
            calls.append(estimated_tokens)
            return await original_check(
                name,
                estimated_tokens=estimated_tokens,
            )

        with patch.object(tracker, "check_quota", side_effect=_spy):
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.FALLBACK,
                    fallback_providers=("fallback-a",),
                ),
                quota_tracker=tracker,
                estimated_tokens=5000,
            )

        # The fallback check should have received 5000
        assert 5000 in calls

    async def test_unknown_provider_treated_as_allowed(self) -> None:
        """Unknown fallback provider (no quotas) is treated as available."""
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        result = await resolve_degradation(
            provider_name="primary",
            quota_result=_denied_result("primary"),
            degradation_config=DegradationConfig(
                strategy=DegradationAction.FALLBACK,
                fallback_providers=("unknown-provider",),
            ),
            quota_tracker=tracker,
        )

        assert result.effective_provider == "unknown-provider"


# ── QUEUE strategy tests ──────────────────────────────────────────


@pytest.mark.unit
class TestQueueStrategy:
    """Tests for QUEUE degradation strategy."""

    @staticmethod
    def _near_future_snapshot(
        seconds: float = 30,
    ) -> tuple[QuotaSnapshot, ...]:
        """Build a snapshot with a reset time in the near future."""
        return (
            QuotaSnapshot(
                provider_name="primary",
                window=QuotaWindow.PER_HOUR,
                requests_used=5,
                requests_limit=5,
                window_resets_at=_FROZEN_NOW + timedelta(seconds=seconds),
                captured_at=_FROZEN_NOW,
            ),
        )

    async def test_waits_for_window_reset(self) -> None:
        """Queue waits for the shortest window reset time."""
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        allowed_result = QuotaCheckResult(
            allowed=True,
            provider_name="primary",
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=self._near_future_snapshot(30),
            ),
            patch("synthorg.budget.degradation.asyncio_sleep") as mock_sleep,
            patch.object(
                tracker,
                "check_quota",
                new_callable=AsyncMock,
                return_value=allowed_result,
            ),
            patch(
                "synthorg.budget.degradation.datetime",
                _frozen_datetime_mock(),
            ),
        ):
            mock_sleep.return_value = None

            result = await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=300,
                ),
                quota_tracker=tracker,
            )

        assert result.action_taken == DegradationAction.QUEUE
        assert result.effective_provider == "primary"
        mock_sleep.assert_awaited_once()
        delay = mock_sleep.call_args[0][0]
        assert delay == 30.0

    async def test_rechecks_after_wake_and_succeeds(self) -> None:
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        allowed_result = QuotaCheckResult(
            allowed=True,
            provider_name="primary",
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=self._near_future_snapshot(30),
            ),
            patch("synthorg.budget.degradation.asyncio_sleep") as mock_sleep,
            patch.object(
                tracker,
                "check_quota",
                new_callable=AsyncMock,
                return_value=allowed_result,
            ),
            patch(
                "synthorg.budget.degradation.datetime",
                _frozen_datetime_mock(),
            ),
        ):
            mock_sleep.return_value = None

            result = await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=300,
                ),
                quota_tracker=tracker,
            )

        assert result.effective_provider == "primary"
        assert result.wait_seconds == 30.0

    async def test_rechecks_after_wake_and_fails(self) -> None:
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        still_denied = QuotaCheckResult(
            allowed=False,
            provider_name="primary",
            reason="still exhausted",
            exhausted_windows=(QuotaWindow.PER_HOUR,),
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=self._near_future_snapshot(30),
            ),
            patch("synthorg.budget.degradation.asyncio_sleep") as mock_sleep,
            patch.object(
                tracker,
                "check_quota",
                new_callable=AsyncMock,
                return_value=still_denied,
            ),
            patch(
                "synthorg.budget.degradation.datetime",
                _frozen_datetime_mock(),
            ),
        ):
            mock_sleep.return_value = None

            with pytest.raises(
                QuotaExhaustedError,
                match="still exhausted after waiting",
            ) as exc_info:
                await resolve_degradation(
                    provider_name="primary",
                    quota_result=_denied_result("primary"),
                    degradation_config=DegradationConfig(
                        strategy=DegradationAction.QUEUE,
                        queue_max_wait_seconds=300,
                    ),
                    quota_tracker=tracker,
                )

        assert exc_info.value.degradation_action == DegradationAction.QUEUE

    async def test_respects_max_wait_seconds(self) -> None:
        """When reset time exceeds max_wait, raises immediately."""
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        # Build a snapshot where reset is far in the future
        now = _FROZEN_NOW
        far_future = now + timedelta(hours=2)
        snapshot = QuotaSnapshot(
            provider_name="primary",
            window=QuotaWindow.PER_HOUR,
            requests_used=5,
            requests_limit=5,
            window_resets_at=far_future,
            captured_at=now,
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=(snapshot,),
            ),
            patch(
                "synthorg.budget.degradation.datetime",
                _frozen_datetime_mock(),
            ),
            pytest.raises(
                QuotaExhaustedError,
                match="exceeds max wait",
            ),
        ):
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=10,
                ),
                quota_tracker=tracker,
            )

    async def test_immediate_recheck_when_window_rotated(self) -> None:
        """When delay <= 0 (window already rotated), recheck immediately."""
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        # Snapshot with reset in the past
        now = _FROZEN_NOW
        past = now - timedelta(seconds=10)
        snapshot = QuotaSnapshot(
            provider_name="primary",
            window=QuotaWindow.PER_HOUR,
            requests_used=5,
            requests_limit=5,
            window_resets_at=past,
            captured_at=now,
        )
        allowed_result = QuotaCheckResult(
            allowed=True,
            provider_name="primary",
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=(snapshot,),
            ),
            patch("synthorg.budget.degradation.asyncio_sleep") as mock_sleep,
            patch.object(
                tracker,
                "check_quota",
                new_callable=AsyncMock,
                return_value=allowed_result,
            ),
            patch(
                "synthorg.budget.degradation.datetime",
                _frozen_datetime_mock(),
            ),
        ):
            mock_sleep.return_value = None

            result = await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=300,
                ),
                quota_tracker=tracker,
            )

        assert result.wait_seconds == 0.0
        mock_sleep.assert_not_awaited()

    async def test_returns_original_provider(self) -> None:
        """QUEUE doesn't change the provider -- it waits for it."""
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        allowed_result = QuotaCheckResult(
            allowed=True,
            provider_name="primary",
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=self._near_future_snapshot(30),
            ),
            patch("synthorg.budget.degradation.asyncio_sleep") as mock_sleep,
            patch.object(
                tracker,
                "check_quota",
                new_callable=AsyncMock,
                return_value=allowed_result,
            ),
            patch(
                "synthorg.budget.degradation.datetime",
                _frozen_datetime_mock(),
            ),
        ):
            mock_sleep.return_value = None

            result = await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=300,
                ),
                quota_tracker=tracker,
            )

        assert result.original_provider == "primary"
        assert result.effective_provider == "primary"

    async def test_picks_soonest_from_multiple_windows(self) -> None:
        """When multiple windows are exhausted, uses the soonest reset."""
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        now = _FROZEN_NOW
        snapshots = (
            QuotaSnapshot(
                provider_name="primary",
                window=QuotaWindow.PER_HOUR,
                requests_used=5,
                requests_limit=5,
                window_resets_at=now + timedelta(seconds=120),
                captured_at=now,
            ),
            QuotaSnapshot(
                provider_name="primary",
                window=QuotaWindow.PER_DAY,
                requests_used=100,
                requests_limit=100,
                window_resets_at=now + timedelta(hours=5),
                captured_at=now,
            ),
        )
        allowed_result = QuotaCheckResult(
            allowed=True,
            provider_name="primary",
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=snapshots,
            ),
            patch("synthorg.budget.degradation.asyncio_sleep") as mock_sleep,
            patch.object(
                tracker,
                "check_quota",
                new_callable=AsyncMock,
                return_value=allowed_result,
            ),
            patch(
                "synthorg.budget.degradation.datetime",
                _frozen_datetime_mock(),
            ),
        ):
            mock_sleep.return_value = None

            result = await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result(
                    "primary",
                    windows=(
                        QuotaWindow.PER_HOUR,
                        QuotaWindow.PER_DAY,
                    ),
                ),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=300,
                ),
                quota_tracker=tracker,
            )

        assert result.action_taken == DegradationAction.QUEUE
        delay = mock_sleep.call_args[0][0]
        assert delay == 120.0

    async def test_zero_max_wait_raises_immediately(self) -> None:
        """queue_max_wait_seconds=0 means no waiting allowed."""
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=self._near_future_snapshot(30),
            ),
            patch(
                "synthorg.budget.degradation.datetime",
                _frozen_datetime_mock(),
            ),
            pytest.raises(
                QuotaExhaustedError,
                match="exceeds max wait",
            ),
        ):
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=0,
                ),
                quota_tracker=tracker,
            )

    async def test_no_snapshots_raises(self) -> None:
        """When no snapshots available, raises immediately."""
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=(),
            ),
            pytest.raises(
                QuotaExhaustedError,
                match="no reset time available",
            ),
        ):
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=300,
                ),
                quota_tracker=tracker,
            )


# ── ALERT strategy tests ──────────────────────────────────────────


@pytest.mark.unit
class TestAlertStrategy:
    """Tests for ALERT degradation strategy (default -- raises)."""

    async def test_raises_immediately(self) -> None:
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        with pytest.raises(
            QuotaExhaustedError,
            match="quota exhausted",
        ) as exc_info:
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.ALERT,
                ),
                quota_tracker=tracker,
            )

        assert exc_info.value.provider_name == "primary"
        assert exc_info.value.degradation_action == DegradationAction.ALERT


# ── Additional edge case tests ────────────────────────────────────


@pytest.mark.unit
class TestDegradationResultValidation:
    """Tests for DegradationResult validation constraints."""

    def test_negative_wait_seconds_raises(self) -> None:
        with pytest.raises(ValidationError, match="wait_seconds"):
            DegradationResult(
                original_provider="a",
                effective_provider="a",
                action_taken=DegradationAction.QUEUE,
                wait_seconds=-1.0,
            )

    def test_extra_field_rejected(self) -> None:
        """Unknown fields are rejected (extra='forbid')."""
        with pytest.raises(ValidationError, match="extra"):
            DegradationResult(
                original_provider="a",
                effective_provider="a",
                action_taken=DegradationAction.FALLBACK,
                unknown_field="surprise",  # type: ignore[call-arg]
            )


@pytest.mark.unit
class TestExtractResetTimesEdgeCases:
    """Edge case tests for _extract_reset_times filtering."""

    async def test_non_matching_windows_filtered(self) -> None:
        """Snapshots for non-exhausted windows are filtered out."""
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        now = datetime.now(UTC)
        # Snapshot exists for PER_DAY but exhausted window is PER_HOUR
        snapshot = QuotaSnapshot(
            provider_name="primary",
            window=QuotaWindow.PER_DAY,
            requests_used=100,
            requests_limit=100,
            window_resets_at=now + timedelta(seconds=30),
            captured_at=now,
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=(snapshot,),
            ),
            pytest.raises(
                QuotaExhaustedError,
                match="no reset time available",
            ),
        ):
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result(
                    "primary",
                    windows=(QuotaWindow.PER_HOUR,),
                ),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=300,
                ),
                quota_tracker=tracker,
            )

    async def test_none_reset_time_filtered(self) -> None:
        """Snapshots with window_resets_at=None are filtered out."""
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        now = datetime.now(UTC)
        snapshot = QuotaSnapshot(
            provider_name="primary",
            window=QuotaWindow.PER_HOUR,
            requests_used=5,
            requests_limit=5,
            window_resets_at=None,
            captured_at=now,
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=(snapshot,),
            ),
            pytest.raises(
                QuotaExhaustedError,
                match="no reset time available",
            ),
        ):
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=300,
                ),
                quota_tracker=tracker,
            )


@pytest.mark.unit
class TestQueueErrorAttributes:
    """Verify QUEUE error paths carry structured context."""

    async def test_max_wait_exceeded_error_attributes(self) -> None:
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        now = _FROZEN_NOW
        snapshot = QuotaSnapshot(
            provider_name="primary",
            window=QuotaWindow.PER_HOUR,
            requests_used=5,
            requests_limit=5,
            window_resets_at=now + timedelta(hours=2),
            captured_at=now,
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=(snapshot,),
            ),
            patch(
                "synthorg.budget.degradation.datetime",
                _frozen_datetime_mock(),
            ),
            pytest.raises(QuotaExhaustedError) as exc_info,
        ):
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=10,
                ),
                quota_tracker=tracker,
            )

        assert exc_info.value.provider_name == "primary"
        assert exc_info.value.degradation_action == DegradationAction.QUEUE

    async def test_no_snapshots_error_attributes(self) -> None:
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=(),
            ),
            pytest.raises(QuotaExhaustedError) as exc_info,
        ):
            await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=300,
                ),
                quota_tracker=tracker,
            )

        assert exc_info.value.provider_name == "primary"
        assert exc_info.value.degradation_action == DegradationAction.QUEUE


@pytest.mark.unit
class TestQueueWaitSecondsAccuracy:
    """Verify wait_seconds matches the actual sleep delay."""

    async def test_wait_seconds_matches_sleep_delay(self) -> None:
        tracker = _make_tracker({"primary": 5})
        await _exhaust_provider(tracker, "primary", 5)

        now = _FROZEN_NOW
        snapshots = (
            QuotaSnapshot(
                provider_name="primary",
                window=QuotaWindow.PER_HOUR,
                requests_used=5,
                requests_limit=5,
                window_resets_at=now + timedelta(seconds=45),
                captured_at=now,
            ),
        )
        allowed_result = QuotaCheckResult(
            allowed=True,
            provider_name="primary",
        )
        with (
            patch.object(
                tracker,
                "get_snapshot",
                new_callable=AsyncMock,
                return_value=snapshots,
            ),
            patch("synthorg.budget.degradation.asyncio_sleep") as mock_sleep,
            patch.object(
                tracker,
                "check_quota",
                new_callable=AsyncMock,
                return_value=allowed_result,
            ),
            patch(
                "synthorg.budget.degradation.datetime",
                _frozen_datetime_mock(),
            ),
        ):
            mock_sleep.return_value = None

            result = await resolve_degradation(
                provider_name="primary",
                quota_result=_denied_result("primary"),
                degradation_config=DegradationConfig(
                    strategy=DegradationAction.QUEUE,
                    queue_max_wait_seconds=300,
                ),
                quota_tracker=tracker,
            )

        sleep_delay = mock_sleep.call_args[0][0]
        assert result.wait_seconds == sleep_delay
