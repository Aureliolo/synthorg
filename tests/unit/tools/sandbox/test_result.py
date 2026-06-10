"""Tests for SandboxResult model."""

import pytest
from pydantic import ValidationError

from synthorg.tools.sandbox.result import SandboxResult
from tests._shared import JsonDict

pytestmark = pytest.mark.unit


class TestSandboxResult:
    """SandboxResult is frozen with a computed ``success`` field."""

    @pytest.mark.parametrize(
        ("returncode", "timed_out", "expected_success"),
        [
            (0, False, True),
            (1, False, False),
            (0, True, False),
            (-1, True, False),
        ],
        ids=["zero-no-timeout", "nonzero", "timeout-zero", "timeout-neg"],
    )
    def test_success_matrix(
        self,
        returncode: int,
        timed_out: bool,
        expected_success: bool,
    ) -> None:
        result = SandboxResult(
            stdout="",
            stderr="",
            returncode=returncode,
            timed_out=timed_out,
        )
        assert result.success is expected_success

    def test_frozen(self) -> None:
        result = SandboxResult(
            stdout="ok",
            stderr="",
            returncode=0,
        )
        with pytest.raises(ValidationError):
            result.stdout = "modified"  # type: ignore[misc]

    def test_timed_out_defaults_false(self) -> None:
        result = SandboxResult(
            stdout="",
            stderr="",
            returncode=0,
        )
        assert result.timed_out is False

    def test_negative_returncode(self) -> None:
        result = SandboxResult(
            stdout="",
            stderr="signal",
            returncode=-9,
        )
        assert result.success is False


class TestSandboxResultContainerFields:
    """Container-specific optional fields for Docker log shipping."""

    def test_new_fields_default_to_none_or_empty(self) -> None:
        result = SandboxResult(
            stdout="ok",
            stderr="",
            returncode=0,
        )
        assert result.container_id is None
        assert result.sidecar_id is None
        assert result.sidecar_logs == ()
        assert result.agent_id is None
        assert result.execution_time_ms is None

    def test_backward_compat_old_construction_unchanged(self) -> None:
        """Constructing with only original fields still works."""
        result = SandboxResult(
            stdout="out",
            stderr="err",
            returncode=1,
            timed_out=True,
        )
        assert result.stdout == "out"
        assert result.stderr == "err"
        assert result.returncode == 1
        assert result.timed_out is True
        assert result.success is False

    def test_all_container_fields_populated(self) -> None:
        logs: tuple[JsonDict, ...] = ({"ts": "2026-04-14T00:00:00Z", "msg": "hello"},)
        result = SandboxResult(
            stdout="output",
            stderr="",
            returncode=0,
            container_id="abc123def456",
            sidecar_id="sidecar789",
            sidecar_logs=logs,
            agent_id="agent-ceo",
            execution_time_ms=1500,
        )
        assert result.container_id == "abc123def456"
        assert result.sidecar_id == "sidecar789"
        assert len(result.sidecar_logs) == 1
        assert result.sidecar_logs[0]["msg"] == "hello"
        assert result.agent_id == "agent-ceo"
        assert result.execution_time_ms == 1500
        assert result.success is True

    def test_sidecar_logs_accepts_plain_dicts(self) -> None:
        result = SandboxResult(
            stdout="",
            stderr="",
            returncode=0,
            sidecar_logs=({"level": "info", "msg": "test"},),
        )
        assert len(result.sidecar_logs) == 1

    def test_model_copy_enrichment(self) -> None:
        """model_copy(update=...) works for post-execution enrichment."""
        base = SandboxResult(
            stdout="out",
            stderr="",
            returncode=0,
            container_id="abc123",
            execution_time_ms=500,
        )
        enriched = base.model_copy(
            update={
                "sidecar_id": "side456",
                "sidecar_logs": ({"msg": "enriched"},),
                "agent_id": "agent-cto",
            },
        )
        assert enriched.container_id == "abc123"
        assert enriched.sidecar_id == "side456"
        assert enriched.sidecar_logs[0]["msg"] == "enriched"
        assert enriched.agent_id == "agent-cto"
        assert enriched.execution_time_ms == 500
        # Original unchanged
        assert base.sidecar_id is None

    def test_frozen_new_fields(self) -> None:
        result = SandboxResult(
            stdout="",
            stderr="",
            returncode=0,
            container_id="abc",
        )
        with pytest.raises(ValidationError):
            result.container_id = "new"  # type: ignore[misc]

    def test_blank_container_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="container_id"):
            SandboxResult(
                stdout="",
                stderr="",
                returncode=0,
                container_id="   ",
            )

    def test_blank_sidecar_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="sidecar_id"):
            SandboxResult(
                stdout="",
                stderr="",
                returncode=0,
                sidecar_id="",
            )

    def test_blank_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="agent_id"):
            SandboxResult(
                stdout="",
                stderr="",
                returncode=0,
                agent_id="   ",
            )

    def test_negative_execution_time_rejected(self) -> None:
        with pytest.raises(ValidationError, match="execution_time_ms"):
            SandboxResult(
                stdout="",
                stderr="",
                returncode=0,
                execution_time_ms=-1,
            )
