"""Unit coverage for :class:`ExperimentService`.

Pins the deterministic-assignment contract: identical subjects always
land on the same variant, weight skews the population correctly, and
once-recorded assignments survive variant edits.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.experiments.in_memory_repository import InMemoryExperimentRepository
from synthorg.experiments.service import ExperimentService
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


def _service() -> tuple[ExperimentService, InMemoryExperimentRepository, FakeClock]:
    repo = InMemoryExperimentRepository()
    clock = FakeClock(start=datetime(2026, 5, 15, tzinfo=UTC))
    svc = ExperimentService(repository=repo, clock=clock)
    return svc, repo, clock


async def test_assign_deterministic_same_subject_same_variant() -> None:
    svc, _, _ = _service()
    await svc.register_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("control"),
        weight=1,
    )
    await svc.register_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("treatment"),
        weight=1,
    )
    a = await svc.assign(experiment=NotBlankStr("exp"), subject_id=NotBlankStr("u-1"))
    b = await svc.assign(experiment=NotBlankStr("exp"), subject_id=NotBlankStr("u-1"))
    assert a.variant == b.variant


async def test_assign_respects_weight_distribution() -> None:
    svc, _, _ = _service()
    await svc.register_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("control"),
        weight=1,
    )
    await svc.register_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("treatment"),
        weight=9,
    )
    counts = {"control": 0, "treatment": 0}
    for i in range(1000):
        result = await svc.assign(
            experiment=NotBlankStr("exp"),
            subject_id=NotBlankStr(f"u-{i}"),
        )
        counts[str(result.variant)] += 1
    # 90/10 split tolerated within sane bounds.
    assert counts["treatment"] > 7 * counts["control"]


async def test_assign_replays_recorded_assignment_after_variant_change() -> None:
    svc, repo, _ = _service()
    await svc.register_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("control"),
        weight=1,
    )
    await svc.register_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("treatment"),
        weight=1,
    )
    first = await svc.assign(
        experiment=NotBlankStr("exp"),
        subject_id=NotBlankStr("u-stable"),
    )
    # Add a new variant; recorded assignment must still be returned.
    await svc.register_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("variant-c"),
        weight=10,
    )
    second = await svc.assign(
        experiment=NotBlankStr("exp"),
        subject_id=NotBlankStr("u-stable"),
    )
    assert second.variant == first.variant
    stored = await repo.get_assignment(
        experiment=NotBlankStr("exp"),
        subject_id=NotBlankStr("u-stable"),
    )
    assert stored is not None
    assert stored.variant == first.variant


async def test_assign_raises_when_no_variants_registered() -> None:
    svc, _, _ = _service()
    from synthorg.core.domain_errors import NotFoundError

    with pytest.raises(NotFoundError, match="no registered variants"):
        await svc.assign(
            experiment=NotBlankStr("nope"),
            subject_id=NotBlankStr("u-1"),
        )


async def test_register_variant_idempotent_replace() -> None:
    svc, repo, _ = _service()
    await svc.register_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("v1"),
        weight=1,
    )
    await svc.register_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("v1"),
        weight=5,
    )
    variants = await repo.list_for_experiment(NotBlankStr("exp"))
    assert len(variants) == 1
    assert variants[0].weight == 5


async def test_delete_variant_returns_false_when_absent() -> None:
    svc, _, _ = _service()
    removed = await svc.delete_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("ghost"),
    )
    assert removed is False


async def test_list_assignments_paginates_in_recency_order() -> None:
    svc, _, clock = _service()
    await svc.register_variant(
        experiment=NotBlankStr("exp"),
        variant=NotBlankStr("v"),
        weight=1,
    )
    for i in range(5):
        await svc.assign(
            experiment=NotBlankStr("exp"),
            subject_id=NotBlankStr(f"u-{i}"),
        )
        # Advance the FakeClock so each assignment carries a distinct
        # timestamp; without this the recency sort would degenerate to
        # stable insertion order and the assertion below would pass for
        # an ascending-sort regression too.
        clock.advance(1)
    page, total = await svc.list_assignments(
        NotBlankStr("exp"),
        limit=3,
        offset=0,
    )
    assert total == 5
    assert len(page) == 3
    # Assignments were created in order u-0 ... u-4 with strictly
    # increasing timestamps; the page contract is "most recent first,"
    # so the first three subject ids on page must be the last three
    # created in reverse order. Without this assert an ordering
    # regression (e.g. a repo switching from descending to ascending
    # sort) would still pass the total / length checks.
    assert [str(item.subject_id) for item in page] == ["u-4", "u-3", "u-2"]
    # The ``assigned_at`` timestamps must also be strictly decreasing
    # across the page; this is the underlying contract that any future
    # durable repo must honour even when subject ids are unordered.
    timestamps = [item.assigned_at for item in page]
    assert timestamps == sorted(timestamps, reverse=True)
