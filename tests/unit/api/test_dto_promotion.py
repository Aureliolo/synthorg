"""Unit tests for the promotion DTO invariants."""

from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import ValidationError

from synthorg.api.dto_promotion import (
    CriterionResultDTO,
    PromotionApplyResultDTO,
    PromotionRecordDTO,
    PromotionRequestDTO,
)
from tests._shared import sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _record(
    *,
    model_changed: bool,
    old_model_id: str | None = None,
    new_model_id: str | None = None,
) -> PromotionRecordDTO:
    return PromotionRecordDTO(
        id=sid("rec-1"),
        agent_id=sid("agent-1"),
        agent_name="Agent One",
        old_level="junior",
        new_level="senior",
        direction="promotion",
        effective_at=_NOW,
        initiated_by="system",
        model_changed=model_changed,
        old_model_id=old_model_id,
        new_model_id=new_model_id,
    )


def _request(
    *,
    status: Literal["pending", "approved", "rejected", "expired"] = "approved",
) -> PromotionRequestDTO:
    return PromotionRequestDTO(
        id=sid("req-1"),
        agent_id=sid("agent-1"),
        agent_name="Agent One",
        current_level="junior",
        target_level="senior",
        direction="promotion",
        status=status,
        created_at=_NOW,
    )


class TestCriterionResultDTO:
    def test_weight_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CriterionResultDTO(
                name="speed",
                met=True,
                current_value=1.0,
                threshold=0.5,
                weight=5.0,
            )

    def test_weight_in_range_accepted(self) -> None:
        dto = CriterionResultDTO(
            name="speed",
            met=True,
            current_value=1.0,
            threshold=0.5,
            weight=0.75,
        )
        assert dto.weight == 0.75


class TestPromotionRecordDTO:
    def test_model_changed_requires_both_ids(self) -> None:
        with pytest.raises(ValidationError, match="model_changed=True"):
            _record(model_changed=True, old_model_id=sid("m-old"))

    def test_model_unchanged_forbids_ids(self) -> None:
        with pytest.raises(ValidationError, match="model_changed=False"):
            _record(model_changed=False, new_model_id=sid("m-new"))

    def test_consistent_model_change_accepted(self) -> None:
        dto = _record(
            model_changed=True,
            old_model_id=sid("m-old"),
            new_model_id=sid("m-new"),
        )
        assert dto.model_changed

    def test_consistent_no_model_change_accepted(self) -> None:
        assert not _record(model_changed=False).model_changed

    def test_invalid_direction_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PromotionRecordDTO(
                id=sid("rec-1"),
                agent_id=sid("agent-1"),
                agent_name="Agent One",
                old_level="junior",
                new_level="senior",
                direction="sideways",  # type: ignore[arg-type]
                effective_at=_NOW,
                initiated_by="system",
                model_changed=False,
            )


class TestPromotionApplyResultDTO:
    def test_applied_requires_approved_request(self) -> None:
        with pytest.raises(ValidationError, match="approved"):
            PromotionApplyResultDTO(
                request=_request(status="pending"),
                applied=_record(model_changed=False),
            )

    def test_applied_with_approved_request_accepted(self) -> None:
        result = PromotionApplyResultDTO(
            request=_request(status="approved"),
            applied=_record(model_changed=False),
        )
        assert result.applied is not None

    def test_pending_without_applied_accepted(self) -> None:
        result = PromotionApplyResultDTO(request=_request(status="pending"))
        assert result.applied is None
