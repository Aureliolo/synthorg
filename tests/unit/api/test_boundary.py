"""Tests for ``synthorg.api.boundary.parse_typed``."""

import pytest
import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.api.boundary import parse_typed


class _Sample(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: str
    age: int


@pytest.mark.unit
class TestParseTyped:
    def test_returns_typed_instance_on_valid_input(self) -> None:
        result = parse_typed("test", {"name": "alice", "age": 30}, _Sample)
        assert isinstance(result, _Sample)
        assert result.name == "alice"
        assert result.age == 30

    def test_raises_validation_error_on_missing_field(self) -> None:
        with pytest.raises(ValidationError):
            parse_typed("test", {"name": "alice"}, _Sample)

    def test_raises_validation_error_on_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            parse_typed("test", {"name": "alice", "age": 30, "extra": 1}, _Sample)

    def test_raises_validation_error_on_wrong_type(self) -> None:
        with pytest.raises(ValidationError):
            parse_typed("test", {"name": "alice", "age": "not-an-int"}, _Sample)

    def test_none_input_treated_as_empty_dict(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            parse_typed("test", None, _Sample)
        assert any(err["type"] == "missing" for err in exc_info.value.errors())

    def test_empty_dict_input_raises_for_required_fields(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            parse_typed("test", {}, _Sample)
        assert len(exc_info.value.errors()) == 2

    def test_validation_failure_emits_structured_log(self) -> None:
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ValidationError),
        ):
            parse_typed("jwt", {"name": "alice"}, _Sample)

        boundary_logs = [
            log for log in logs if log.get("event") == "api.boundary.validation_failed"
        ]
        assert len(boundary_logs) == 1
        record = boundary_logs[0]
        assert record["boundary"] == "jwt"
        assert record["error_type"] == "ValidationError"
        assert record["error_count"] == 1
        assert record["error_locations"] == ("age",)
        assert record["log_level"] == "warning"

    def test_boundary_label_propagates_to_log(self) -> None:
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ValidationError),
        ):
            parse_typed("ws.control", {"name": "alice"}, _Sample)

        boundary_logs = [
            log for log in logs if log.get("event") == "api.boundary.validation_failed"
        ]
        assert len(boundary_logs) == 1
        assert boundary_logs[0]["boundary"] == "ws.control"
