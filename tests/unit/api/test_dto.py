"""Tests for DTO models and response envelopes."""

import pytest
from pydantic import ValidationError

from synthorg.api.dto import (
    ApiResponse,
    ApproveRequest,
    CoordinateTaskRequest,
    CoordinationPhaseResponse,
    CoordinationResultResponse,
    CreateApprovalRequest,
    ErrorDetail,
    PaginatedResponse,
    PaginationMeta,
)
from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.error_taxonomy import (
    ErrorCategory,
    ErrorCode,
    category_title,
    category_type_uri,
)
from tests._shared import JsonDict

pytestmark = pytest.mark.unit


def _make_error_detail(
    cat: ErrorCategory = ErrorCategory.INTERNAL,
    **kwargs: object,
) -> ErrorDetail:
    """Build an ``ErrorDetail`` with title/type derived from category."""
    defaults: JsonDict = {
        "detail": "err",
        "error_code": ErrorCode.INTERNAL_ERROR,
        "error_category": cat,
        "instance": "x",
        "title": category_title(cat),
        "type": category_type_uri(cat),
    }
    return ErrorDetail(**(defaults | kwargs))


class TestApiResponseEnvelope:
    def test_success_true_when_no_error(self) -> None:
        resp = ApiResponse(data={"key": "value"})
        assert resp.success is True
        assert resp.error is None

    def test_success_false_when_error_set(self) -> None:
        resp = ApiResponse[None](error="Something went wrong")
        assert resp.success is False
        assert resp.data is None

    def test_success_computed_in_serialization(self) -> None:
        resp = ApiResponse[None](error="fail")
        dumped = resp.model_dump()
        assert dumped["success"] is False
        assert dumped["error"] == "fail"

    def test_success_true_in_serialization(self) -> None:
        resp = ApiResponse(data="ok")
        dumped = resp.model_dump()
        assert dumped["success"] is True
        assert dumped["data"] == "ok"

    def test_error_detail_none_on_success(self) -> None:
        resp = ApiResponse(data="ok")
        assert resp.error_detail is None
        dumped = resp.model_dump()
        assert dumped["error_detail"] is None

    def test_error_detail_populated_on_error(self) -> None:
        detail = _make_error_detail(
            ErrorCategory.NOT_FOUND,
            detail="Not found",
            error_code=3000,
            retryable=False,
            instance="abc-123",
        )
        resp = ApiResponse[None](error="Not found", error_detail=detail)
        assert resp.success is False
        assert resp.error_detail is not None
        assert resp.error_detail.error_code == 3000
        assert resp.error_detail.error_category == ErrorCategory.NOT_FOUND


class TestErrorDetail:
    def test_construction_all_fields(self) -> None:
        detail = _make_error_detail(
            ErrorCategory.RATE_LIMIT,
            detail="Rate limited",
            error_code=5000,
            retryable=True,
            retry_after=30,
            instance="req-456",
        )
        assert detail.detail == "Rate limited"
        assert detail.error_code == 5000
        assert detail.error_category == ErrorCategory.RATE_LIMIT
        assert detail.retryable is True
        assert detail.retry_after == 30
        assert detail.instance == "req-456"

    def test_retry_after_defaults_to_none(self) -> None:
        detail = _make_error_detail()
        assert detail.retry_after is None
        assert detail.retryable is False

    def test_frozen_immutability(self) -> None:
        detail = _make_error_detail()
        with pytest.raises(ValidationError):
            detail.detail = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        detail = _make_error_detail(
            ErrorCategory.CONFLICT,
            detail="Conflict",
            error_code=4000,
            retryable=False,
            retry_after=None,
            instance="req-789",
        )
        dumped = detail.model_dump()
        restored = ErrorDetail.model_validate(dumped)
        assert restored == detail

    def test_retry_after_rejects_negative(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            _make_error_detail(
                ErrorCategory.RATE_LIMIT,
                error_code=5000,
                retryable=True,
                retry_after=-1,
            )

    def test_retry_after_rejects_when_not_retryable(self) -> None:
        with pytest.raises(ValidationError, match="retry_after must be None"):
            _make_error_detail(
                retryable=False,
                retry_after=30,
            )

    def test_retry_after_allowed_when_retryable(self) -> None:
        detail = _make_error_detail(
            ErrorCategory.RATE_LIMIT,
            detail="Slow down",
            error_code=5000,
            retryable=True,
            retry_after=60,
        )
        assert detail.retry_after == 60


class TestApiResponseErrorDetailConsistency:
    def test_error_detail_on_success_rejected(self) -> None:
        detail = _make_error_detail()
        with pytest.raises(ValidationError, match="error_detail requires error"):
            ApiResponse(data="ok", error_detail=detail)

    def test_error_detail_on_paginated_success_rejected(self) -> None:
        detail = _make_error_detail()
        with pytest.raises(ValidationError, match="error_detail requires error"):
            PaginatedResponse(
                data=("a",),
                error_detail=detail,
                pagination=PaginationMeta(limit=10, next_cursor=None, has_more=False),
            )


class TestPaginatedResponseDegradedSources:
    def test_degraded_sources_defaults_to_empty(self) -> None:
        resp: PaginatedResponse[str] = PaginatedResponse(
            pagination=PaginationMeta(limit=10, next_cursor=None, has_more=False),
        )
        assert resp.degraded_sources == ()

    def test_degraded_sources_round_trips_through_json(self) -> None:
        resp: PaginatedResponse[str] = PaginatedResponse(
            pagination=PaginationMeta(limit=10, next_cursor=None, has_more=False),
            degraded_sources=("cost_tracker", "tool_invocation_tracker"),
        )
        data = resp.model_dump(mode="json")
        assert data["degraded_sources"] == [
            "cost_tracker",
            "tool_invocation_tracker",
        ]


class TestPaginatedResponseErrorDetail:
    def test_error_detail_defaults_to_none(self) -> None:
        resp: PaginatedResponse[str] = PaginatedResponse(
            pagination=PaginationMeta(limit=10, next_cursor=None, has_more=False),
        )
        assert resp.error_detail is None

    def test_error_detail_field_present(self) -> None:
        detail = _make_error_detail()
        resp: PaginatedResponse[str] = PaginatedResponse(
            error="err",
            error_detail=detail,
            pagination=PaginationMeta(limit=10, next_cursor=None, has_more=False),
        )
        assert resp.error_detail is not None
        assert resp.error_detail.error_code == 8000


class TestCreateApprovalRequestMetadata:
    def test_metadata_too_many_keys(self) -> None:
        many_keys = {f"k{i}": f"v{i}" for i in range(21)}
        with pytest.raises(ValueError, match="at most 20 keys"):
            CreateApprovalRequest(
                action_type="deploy:release",
                title="Test",
                description="Test desc",
                risk_level=ApprovalRiskLevel.LOW,
                metadata=many_keys,
            )

    def test_metadata_key_too_long(self) -> None:
        long_key = "k" * 257
        with pytest.raises(ValueError, match="metadata key"):
            CreateApprovalRequest(
                action_type="deploy:release",
                title="Test",
                description="Test desc",
                risk_level=ApprovalRiskLevel.LOW,
                metadata={long_key: "val"},
            )

    def test_metadata_value_too_long(self) -> None:
        long_val = "v" * 257
        with pytest.raises(ValueError, match="metadata value"):
            CreateApprovalRequest(
                action_type="deploy:release",
                title="Test",
                description="Test desc",
                risk_level=ApprovalRiskLevel.LOW,
                metadata={"key": long_val},
            )

    def test_metadata_within_bounds(self) -> None:
        req = CreateApprovalRequest(
            action_type="deploy:release",
            title="Test",
            description="Test desc",
            risk_level=ApprovalRiskLevel.LOW,
            metadata={"key": "value"},
        )
        assert req.metadata == {"key": "value"}


class TestCreateApprovalRequestActionType:
    @pytest.mark.parametrize(
        "invalid_action_type",
        [
            "deploy",
            ":release",
            "deploy:",
            "deploy:  ",
            "  :release",
            "a:b:c",
        ],
    )
    def test_invalid_format_rejected(self, invalid_action_type: str) -> None:
        with pytest.raises(ValueError, match="category:action"):
            CreateApprovalRequest(
                action_type=invalid_action_type,
                title="Test",
                description="Test desc",
                risk_level=ApprovalRiskLevel.LOW,
            )

    @pytest.mark.parametrize(
        "valid_action_type",
        [
            "deploy:production",
            "db:admin",
            "comms:internal",
            "test:action",
        ],
    )
    def test_valid_format_accepted(self, valid_action_type: str) -> None:
        req = CreateApprovalRequest(
            action_type=valid_action_type,
            title="Test",
            description="Test desc",
            risk_level=ApprovalRiskLevel.LOW,
        )
        assert req.action_type == valid_action_type


class TestCreateApprovalRequestTtl:
    def test_ttl_below_minimum_rejected(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 60"):
            CreateApprovalRequest(
                action_type="deploy:release",
                title="Test",
                description="Test desc",
                risk_level=ApprovalRiskLevel.LOW,
                ttl_seconds=30,
            )

    def test_ttl_above_maximum_rejected(self) -> None:
        with pytest.raises(ValueError, match="less than or equal to 604800"):
            CreateApprovalRequest(
                action_type="deploy:release",
                title="Test",
                description="Test desc",
                risk_level=ApprovalRiskLevel.LOW,
                ttl_seconds=700000,
            )

    def test_ttl_within_bounds(self) -> None:
        req = CreateApprovalRequest(
            action_type="deploy:release",
            title="Test",
            description="Test desc",
            risk_level=ApprovalRiskLevel.LOW,
            ttl_seconds=3600,
        )
        assert req.ttl_seconds == 3600

    def test_ttl_none_by_default(self) -> None:
        req = CreateApprovalRequest(
            action_type="deploy:release",
            title="Test",
            description="Test desc",
            risk_level=ApprovalRiskLevel.LOW,
        )
        assert req.ttl_seconds is None


class TestApproveRequestDto:
    def test_comment_optional(self) -> None:
        req = ApproveRequest()
        assert req.comment is None

    def test_comment_within_bounds(self) -> None:
        req = ApproveRequest(comment="Looks good")
        assert req.comment == "Looks good"

    def test_comment_too_long(self) -> None:
        with pytest.raises(ValueError, match="at most 4096"):
            ApproveRequest(comment="x" * 5000)


class TestCoordinateTaskRequest:
    """Validation tests for CoordinateTaskRequest."""

    def test_valid_minimal(self) -> None:
        req = CoordinateTaskRequest()
        assert req.agent_names is None
        assert req.max_subtasks == 10

    def test_agent_names_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            CoordinateTaskRequest(agent_names=())

    def test_agent_names_max_length(self) -> None:
        names = tuple(f"agent-{i}" for i in range(51))
        with pytest.raises(ValidationError):
            CoordinateTaskRequest(agent_names=names)

    def test_duplicate_agent_names_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate agent name"):
            CoordinateTaskRequest(agent_names=("alice", "Alice"))

    def test_unique_agent_names_accepted(self) -> None:
        req = CoordinateTaskRequest(agent_names=("alice", "bob"))
        assert req.agent_names == ("alice", "bob")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_subtasks", 0),
            ("max_subtasks", 51),
            ("max_concurrency_per_wave", 0),
            ("max_concurrency_per_wave", 51),
        ],
    )
    def test_bounds_rejected(self, field: str, value: int) -> None:
        with pytest.raises(ValidationError):
            CoordinateTaskRequest(**{field: value})  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_subtasks", 1),
            ("max_subtasks", 50),
            ("max_concurrency_per_wave", 1),
            ("max_concurrency_per_wave", 50),
        ],
    )
    def test_bounds_accepted(self, field: str, value: int) -> None:
        req = CoordinateTaskRequest(**{field: value})  # type: ignore[arg-type]
        assert getattr(req, field) == value


class TestCoordinationPhaseResponse:
    """Validation tests for CoordinationPhaseResponse consistency."""

    def test_success_with_error_rejected(self) -> None:
        with pytest.raises(ValidationError, match="successful phase"):
            CoordinationPhaseResponse(
                phase="test", success=True, duration_seconds=0.1, error="oops"
            )

    def test_failure_without_error_rejected(self) -> None:
        with pytest.raises(ValidationError, match="failed phase"):
            CoordinationPhaseResponse(
                phase="test", success=False, duration_seconds=0.1, error=None
            )

    def test_failure_with_blank_error_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoordinationPhaseResponse(
                phase="test", success=False, duration_seconds=0.1, error="  "
            )

    def test_valid_success(self) -> None:
        p = CoordinationPhaseResponse(
            phase="decompose", success=True, duration_seconds=0.5
        )
        assert p.success is True
        assert p.error is None

    def test_valid_failure(self) -> None:
        p = CoordinationPhaseResponse(
            phase="route", success=False, duration_seconds=0.1, error="fail"
        )
        assert p.error == "fail"


class TestCoordinationResultResponse:
    """Tests for CoordinationResultResponse computed field."""

    def _ok_phase(self) -> CoordinationPhaseResponse:
        return CoordinationPhaseResponse(phase="p1", success=True, duration_seconds=0.1)

    def _fail_phase(self) -> CoordinationPhaseResponse:
        return CoordinationPhaseResponse(
            phase="p2", success=False, duration_seconds=0.1, error="err"
        )

    def test_is_success_all_pass(self) -> None:
        r = CoordinationResultResponse(
            parent_task_id="t1",
            topology="sas",
            total_duration_seconds=1.0,
            total_cost=0.01,
            phases=(self._ok_phase(),),
            wave_count=0,
        )
        assert r.is_success is True

    def test_is_success_with_failure(self) -> None:
        r = CoordinationResultResponse(
            parent_task_id="t1",
            topology="sas",
            total_duration_seconds=1.0,
            total_cost=0.01,
            phases=(self._ok_phase(), self._fail_phase()),
            wave_count=1,
        )
        assert r.is_success is False

    def test_empty_phases_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoordinationResultResponse(
                parent_task_id="t1",
                topology="sas",
                total_duration_seconds=1.0,
                total_cost=0.01,
                phases=(),
                wave_count=0,
            )
