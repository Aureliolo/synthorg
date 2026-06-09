"""Unit tests for intake engine domain models."""

import pytest
from pydantic import ValidationError

from synthorg.engine.intake.models import IntakeResult

pytestmark = pytest.mark.unit


class TestIntakeResult:
    """Tests for the IntakeResult model."""

    def test_accepted_result(self) -> None:
        result = IntakeResult.accepted_result(
            request_id="req-1",
            task_id="task-1",
        )
        assert result.accepted is True
        assert result.task_id == "task-1"
        assert result.rejection_reason is None

    def test_rejected_result(self) -> None:
        result = IntakeResult.rejected_result(
            request_id="req-1",
            reason="Requirements unclear",
        )
        assert result.accepted is False
        assert result.task_id is None
        assert result.rejection_reason == "Requirements unclear"

    def test_direct_construction(self) -> None:
        result = IntakeResult(
            request_id="req-1",
            accepted=True,
            task_id="task-1",
        )
        assert result.request_id == "req-1"
        assert result.processed_at is not None

    def test_blank_request_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            IntakeResult(
                request_id="   ",
                accepted=True,
                task_id="task-1",
            )

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            (
                {"request_id": "req-1", "accepted": True},
                "task_id is required",
            ),
            (
                {
                    "request_id": "req-1",
                    "accepted": True,
                    "task_id": "task-1",
                    "rejection_reason": "should not be here",
                },
                "rejection_reason must be None",
            ),
            (
                {"request_id": "req-1", "accepted": False},
                "rejection_reason is required",
            ),
            (
                {
                    "request_id": "req-1",
                    "accepted": False,
                    "task_id": "task-1",
                    "rejection_reason": "bad request",
                },
                "task_id must be None",
            ),
        ],
    )
    def test_invalid_combinations_rejected(
        self,
        kwargs: dict[str, object],
        match: str,
    ) -> None:
        with pytest.raises(ValidationError, match=match):
            IntakeResult.model_validate(kwargs)

    def test_frozen(self) -> None:
        result = IntakeResult.accepted_result(
            request_id="req-1",
            task_id="task-1",
        )
        with pytest.raises(ValidationError):
            result.accepted = False  # type: ignore[misc]
