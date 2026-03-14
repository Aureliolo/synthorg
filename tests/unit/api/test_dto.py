"""Tests for DTO models and response envelopes."""

import pytest

from ai_company.api.dto import ApiResponse, ApproveRequest, CreateApprovalRequest
from ai_company.core.enums import ApprovalRiskLevel


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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
