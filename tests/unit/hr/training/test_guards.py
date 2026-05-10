"""Unit tests for training mode guards."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.enums import SeniorityLevel
from synthorg.hr.training.guards.review_gate import ReviewGateGuard
from synthorg.hr.training.guards.sanitization import SanitizationGuard
from synthorg.hr.training.guards.volume_cap import VolumeCapGuard
from synthorg.hr.training.models import (
    ContentType,
    TrainingItem,
    TrainingPlan,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _make_item(
    *,
    content: str = "Always validate inputs before processing",
    content_type: ContentType = ContentType.PROCEDURAL,
) -> TrainingItem:
    return TrainingItem(
        source_agent_id="senior-1",
        content_type=content_type,
        content=content,
        created_at=_now(),
    )


def _make_plan(
    *,
    require_review: bool = True,
    volume_caps: tuple[tuple[ContentType, int], ...] | None = None,
) -> TrainingPlan:
    return TrainingPlan(
        new_agent_id="new-1",
        new_agent_role="engineer",
        new_agent_level=SeniorityLevel.JUNIOR,
        require_review=require_review,
        volume_caps=volume_caps
        or (
            (ContentType.PROCEDURAL, 50),
            (ContentType.SEMANTIC, 10),
            (ContentType.TOOL_PATTERNS, 20),
        ),
        created_at=_now(),
    )


# -- SanitizationGuard -----------------------------------------------


@pytest.mark.unit
class TestSanitizationGuard:
    """SanitizationGuard tests."""

    def test_name(self) -> None:
        guard = SanitizationGuard()
        assert guard.name == "sanitization"

    async def test_passes_clean_content(self) -> None:
        items = (_make_item(content="Clean content here"),)
        guard = SanitizationGuard()
        decision = await guard.evaluate(
            items,
            content_type=ContentType.PROCEDURAL,
            plan=_make_plan(),
        )
        assert len(decision.approved_items) == 1
        assert decision.rejected_count == 0

    async def test_sanitizes_file_paths(self) -> None:
        items = (
            _make_item(
                content="Error at C:\\Users\\secret\\file.py line 42",
            ),
        )
        guard = SanitizationGuard()
        decision = await guard.evaluate(
            items,
            content_type=ContentType.PROCEDURAL,
            plan=_make_plan(),
        )
        # Should still pass (sanitized, not rejected)
        assert len(decision.approved_items) == 1
        # Content should have path redacted
        assert "C:\\Users" not in decision.approved_items[0].content

    async def test_rejects_fully_redacted_content(self) -> None:
        # Content with only non-alphanumeric chars after sanitization
        items = (_make_item(content="---...!!!"),)
        guard = SanitizationGuard()
        decision = await guard.evaluate(
            items,
            content_type=ContentType.PROCEDURAL,
            plan=_make_plan(),
        )
        assert decision.rejected_count == 1
        assert len(decision.approved_items) == 0

    async def test_empty_input(self) -> None:
        guard = SanitizationGuard()
        decision = await guard.evaluate(
            (),
            content_type=ContentType.PROCEDURAL,
            plan=_make_plan(),
        )
        assert decision.approved_items == ()
        assert decision.rejected_count == 0


# -- VolumeCapGuard ---------------------------------------------------


@pytest.mark.unit
class TestVolumeCapGuard:
    """VolumeCapGuard tests."""

    def test_name(self) -> None:
        guard = VolumeCapGuard()
        assert guard.name == "volume_cap"

    async def test_passes_when_under_cap(self) -> None:
        items = tuple(_make_item() for _ in range(5))
        guard = VolumeCapGuard()
        plan = _make_plan(
            volume_caps=((ContentType.PROCEDURAL, 50),),
        )
        decision = await guard.evaluate(
            items,
            content_type=ContentType.PROCEDURAL,
            plan=plan,
        )
        assert len(decision.approved_items) == 5
        assert decision.rejected_count == 0

    async def test_truncates_when_over_cap(self) -> None:
        items = tuple(_make_item() for _ in range(10))
        guard = VolumeCapGuard()
        plan = _make_plan(
            volume_caps=((ContentType.PROCEDURAL, 3),),
        )
        decision = await guard.evaluate(
            items,
            content_type=ContentType.PROCEDURAL,
            plan=plan,
        )
        assert len(decision.approved_items) == 3
        assert decision.rejected_count == 7

    async def test_uses_default_cap_when_not_in_plan(self) -> None:
        items = tuple(_make_item(content_type=ContentType.SEMANTIC) for _ in range(15))
        guard = VolumeCapGuard()
        # Plan has caps for PROCEDURAL only
        plan = _make_plan(
            volume_caps=((ContentType.PROCEDURAL, 50),),
        )
        decision = await guard.evaluate(
            items,
            content_type=ContentType.SEMANTIC,
            plan=plan,
        )
        # Should pass all -- no cap defined for SEMANTIC in plan
        assert len(decision.approved_items) == 15

    async def test_empty_input(self) -> None:
        guard = VolumeCapGuard()
        decision = await guard.evaluate(
            (),
            content_type=ContentType.PROCEDURAL,
            plan=_make_plan(),
        )
        assert decision.approved_items == ()
        assert decision.rejected_count == 0


# -- ReviewGateGuard --------------------------------------------------


@pytest.mark.unit
class TestReviewGateGuard:
    """ReviewGateGuard tests."""

    def test_name(self) -> None:
        guard = ReviewGateGuard(approval_store=AsyncMock(spec=ApprovalStoreProtocol))
        assert guard.name == "review_gate"

    async def test_creates_approval_when_review_required(self) -> None:
        store = AsyncMock(spec=ApprovalStoreProtocol)
        guard = ReviewGateGuard(approval_store=store)
        items = (_make_item(),)
        plan = _make_plan(require_review=True)

        decision = await guard.evaluate(
            items,
            content_type=ContentType.PROCEDURAL,
            plan=plan,
        )
        # Items blocked pending approval
        assert len(decision.approved_items) == 0
        assert decision.approval_item_id is not None
        store.add.assert_awaited_once()

    async def test_passes_when_review_not_required(self) -> None:
        store = AsyncMock(spec=ApprovalStoreProtocol)
        guard = ReviewGateGuard(approval_store=store)
        items = (_make_item(),)
        plan = _make_plan(require_review=False)

        decision = await guard.evaluate(
            items,
            content_type=ContentType.PROCEDURAL,
            plan=plan,
        )
        assert len(decision.approved_items) == 1
        assert decision.approval_item_id is None
        store.add.assert_not_awaited()

    async def test_empty_input_skips_review(self) -> None:
        store = AsyncMock(spec=ApprovalStoreProtocol)
        guard = ReviewGateGuard(approval_store=store)
        decision = await guard.evaluate(
            (),
            content_type=ContentType.PROCEDURAL,
            plan=_make_plan(require_review=True),
        )
        assert decision.approved_items == ()
        store.add.assert_not_awaited()
