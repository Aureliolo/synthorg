"""Tests for DTO models and response envelopes."""

import pytest

from ai_company.api.dto import ApiResponse, CreateApprovalRequest
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
