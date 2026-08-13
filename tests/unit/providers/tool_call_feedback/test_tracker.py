"""Unit tests for the runtime tool-call feedback tracker."""

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.model_tool_call_signal_protocol import (
    ModelToolCallSignal,
    ModelToolCallSignalKey,
)
from synthorg.providers.tool_call_feedback.sink import ToolCallOutcome
from synthorg.providers.tool_call_feedback.tracker import ToolCallFeedbackTracker
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_PROVIDER = "example-provider"
_MODEL = "example-expert-001"
_KEY = (NotBlankStr(_PROVIDER), NotBlankStr(_MODEL))


class _FakeSignalRepo:
    """In-memory ``ModelToolCallSignalRepository`` double."""

    def __init__(self) -> None:
        self.rows: dict[ModelToolCallSignalKey, ModelToolCallSignal] = {}
        self.raise_on_save = False

    async def save(self, entity: ModelToolCallSignal) -> None:
        if self.raise_on_save:
            msg = "boom"
            raise QueryError(msg)
        self.rows[(entity.provider_name, entity.model_id)] = entity

    async def get(
        self, entity_id: ModelToolCallSignalKey
    ) -> ModelToolCallSignal | None:
        return self.rows.get(entity_id)

    async def delete(self, entity_id: ModelToolCallSignalKey) -> bool:
        return self.rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ModelToolCallSignal, ...]:
        return tuple(self.rows.values())


class _FakeWriter:
    """Records capability-flag transitions; honours idempotent no-op semantics.

    Mirrors the real ``ProviderToolCallCapabilityMixin``: each call records
    the invocation, applies the tristate transition, and returns ``True``
    only when the persisted flag actually changed -- including the
    ``None`` -> ``True`` no-op (a success on a never-downgraded model).
    """

    def __init__(self) -> None:
        # Current persisted flag per key: None (untested) / True / False.
        self.flags: dict[ModelToolCallSignalKey, bool | None] = {}
        self.unverified_calls: list[ModelToolCallSignalKey] = []
        self.verified_calls: list[ModelToolCallSignalKey] = []
        self.cleared_calls: list[ModelToolCallSignalKey] = []

    def _apply(self, key: ModelToolCallSignalKey, value: bool | None) -> bool:
        current = self.flags.get(key)
        if current == value or (value is True and current is None):
            return False
        self.flags[key] = value
        return True

    async def mark_tool_calls_unverified(self, provider: str, model: str) -> bool:
        key = (NotBlankStr(provider), NotBlankStr(model))
        self.unverified_calls.append(key)
        return self._apply(key, False)

    async def mark_tool_calls_verified(self, provider: str, model: str) -> bool:
        key = (NotBlankStr(provider), NotBlankStr(model))
        self.verified_calls.append(key)
        return self._apply(key, True)

    async def clear_tool_calls_verification(self, provider: str, model: str) -> bool:
        key = (NotBlankStr(provider), NotBlankStr(model))
        self.cleared_calls.append(key)
        return self._apply(key, None)


class _FakeSettings:
    """Live settings reader double."""

    def __init__(
        self, *, enabled: bool = True, threshold: int = 3, half_life: int = 3600
    ) -> None:
        self.enabled = enabled
        self.threshold = threshold
        self.half_life = half_life

    async def get_bool(self, namespace: str, key: str) -> bool:
        assert namespace == "providers"
        assert key == "tool_call_feedback_enabled"
        return self.enabled

    async def get_int(self, namespace: str, key: str) -> int:
        assert namespace == "providers"
        if key == "tool_call_failure_threshold":
            return self.threshold
        if key == "tool_call_failure_decay_half_life_seconds":
            return self.half_life
        msg = f"unexpected key {key}"
        raise AssertionError(msg)


def _tracker(
    *,
    repo: _FakeSignalRepo | None = None,
    writer: _FakeWriter | None = None,
    settings: _FakeSettings | None = None,
    clock: FakeClock | None = None,
) -> tuple[
    ToolCallFeedbackTracker, _FakeSignalRepo, _FakeWriter, _FakeSettings, FakeClock
]:
    repo = repo or _FakeSignalRepo()
    writer = writer or _FakeWriter()
    settings = settings or _FakeSettings()
    clock = clock or FakeClock()
    tracker = ToolCallFeedbackTracker(
        repo=repo, writer=writer, settings=settings, clock=clock
    )
    return tracker, repo, writer, settings, clock


async def _failure(tracker: ToolCallFeedbackTracker) -> None:
    await tracker.record(
        provider=_PROVIDER, model=_MODEL, outcome=ToolCallOutcome.FAILURE
    )


async def _success(tracker: ToolCallFeedbackTracker) -> None:
    await tracker.record(
        provider=_PROVIDER, model=_MODEL, outcome=ToolCallOutcome.SUCCESS
    )


class TestFailureAccumulation:
    async def test_single_failure_below_threshold_no_downgrade(self) -> None:
        tracker, repo, writer, _, _ = _tracker()
        await _failure(tracker)
        assert repo.rows[_KEY].failure_score == pytest.approx(1.0)
        assert writer.unverified_calls == []

    async def test_reaching_threshold_downgrades(self) -> None:
        tracker, repo, writer, _, _ = _tracker(settings=_FakeSettings(threshold=3))
        await _failure(tracker)
        await _failure(tracker)
        assert writer.unverified_calls == []
        await _failure(tracker)
        assert writer.unverified_calls == [_KEY]
        assert repo.rows[_KEY].failure_score == pytest.approx(3.0)

    async def test_decay_prevents_spaced_failures_from_downgrading(self) -> None:
        # half-life 100s, threshold 3: failures spaced one half-life apart
        # converge toward 2.0 and never reach the threshold.
        clock = FakeClock()
        tracker, _, writer, _, _ = _tracker(
            settings=_FakeSettings(threshold=3, half_life=100), clock=clock
        )
        for _ in range(6):
            await _failure(tracker)
            clock.advance(100)
        assert writer.unverified_calls == []

    async def test_decay_math_halves_over_one_half_life(self) -> None:
        clock = FakeClock()
        tracker, repo, _, _, _ = _tracker(
            settings=_FakeSettings(threshold=99, half_life=100), clock=clock
        )
        await _failure(tracker)  # score 1.0
        clock.advance(100)  # one half-life
        await _failure(tracker)  # 1.0*0.5 + 1 = 1.5
        assert repo.rows[_KEY].failure_score == pytest.approx(1.5)

    async def test_two_failures_at_boundary_below_threshold(self) -> None:
        # threshold=3: two un-decayed failures land at exactly 2.0, just under
        # the boundary, so the model is not downgraded.
        tracker, repo, writer, _, _ = _tracker(settings=_FakeSettings(threshold=3))
        await _failure(tracker)
        await _failure(tracker)
        assert repo.rows[_KEY].failure_score == pytest.approx(2.0)
        assert writer.unverified_calls == []

    async def test_cold_start_hydrates_prior_row_then_downgrades(self) -> None:
        # A fresh tracker (empty cache) must hydrate the persisted accumulator
        # from the repo: a model already at 2.9 crosses threshold=3 on the
        # next failure (2.9 -> 3.9), proving the DB read is not skipped.
        clock = FakeClock()
        repo = _FakeSignalRepo()
        repo.rows[_KEY] = ModelToolCallSignal(
            provider_name=_PROVIDER,
            model_id=_MODEL,
            failure_score=2.9,
            decayed_at=clock.now().timestamp(),
        )
        tracker, _, writer, _, _ = _tracker(
            repo=repo, settings=_FakeSettings(threshold=3, half_life=3600), clock=clock
        )
        await _failure(tracker)
        assert repo.rows[_KEY].failure_score == pytest.approx(3.9)
        assert writer.unverified_calls == [_KEY]

    async def test_non_positive_half_life_decays_to_zero(self) -> None:
        # Defence-in-depth: a mis-set half_life of 0 must not ZeroDivisionError
        # (swallowed silently); it floors the decayed prior to 0.0.
        clock = FakeClock()
        repo = _FakeSignalRepo()
        repo.rows[_KEY] = ModelToolCallSignal(
            provider_name=_PROVIDER,
            model_id=_MODEL,
            failure_score=10.0,
            decayed_at=clock.now().timestamp(),
        )
        tracker, _, writer, _, _ = _tracker(
            repo=repo, settings=_FakeSettings(threshold=3, half_life=0), clock=clock
        )
        await _failure(tracker)
        assert repo.rows[_KEY].failure_score == pytest.approx(1.0)
        assert writer.unverified_calls == []


class TestSuccessRecovery:
    async def test_success_after_failures_clears_and_reenables(self) -> None:
        tracker, repo, writer, _, _ = _tracker(settings=_FakeSettings(threshold=3))
        for _ in range(3):
            await _failure(tracker)
        assert writer.unverified_calls == [_KEY]
        await _success(tracker)
        assert writer.verified_calls == [_KEY]
        assert _KEY not in repo.rows

    async def test_success_on_healthy_model_is_noop(self) -> None:
        tracker, repo, writer, _, _ = _tracker()
        await _success(tracker)
        assert writer.verified_calls == []
        assert repo.rows == {}

    async def test_success_below_threshold_clears_row(self) -> None:
        tracker, repo, writer, _, _ = _tracker(settings=_FakeSettings(threshold=5))
        await _failure(tracker)
        assert _KEY in repo.rows
        await _success(tracker)
        # Row cleared; the writer is called but no-ops (the model was never
        # downgraded), so its flag stays None -- no spurious None -> True.
        assert _KEY not in repo.rows
        assert writer.verified_calls == [_KEY]
        assert writer.flags.get(_KEY) is None


class TestGuards:
    async def test_disabled_is_noop(self) -> None:
        tracker, repo, writer, _, _ = _tracker(settings=_FakeSettings(enabled=False))
        await _failure(tracker)
        assert repo.rows == {}
        assert writer.unverified_calls == []

    async def test_record_swallows_repo_errors(self) -> None:
        repo = _FakeSignalRepo()
        repo.raise_on_save = True
        tracker, _, writer, _, _ = _tracker(repo=repo)
        # Must not raise into the provider hot path.
        await _failure(tracker)
        assert writer.unverified_calls == []

    async def test_clear_resets_flag_and_row(self) -> None:
        tracker, repo, writer, _, _ = _tracker(settings=_FakeSettings(threshold=1))
        await _failure(tracker)
        assert _KEY in repo.rows
        await tracker.clear(provider=_PROVIDER, model=_MODEL)
        assert _KEY not in repo.rows
        assert writer.cleared_calls == [_KEY]
