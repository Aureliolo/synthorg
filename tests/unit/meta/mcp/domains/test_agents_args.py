"""Smoke tests for the MCP agents domain args module."""

import pytest
from pydantic import ValidationError

from synthorg.meta.mcp.domains._agents_args import (
    AgentsCreateArgs,
    AgentsDeleteArgs,
    AgentsGetArgs,
    AgentsListArgs,
    AutonomyUpdateArgs,
    TrainingStartSessionArgs,
)


class TestAgentsCRUD:
    @pytest.mark.unit
    def test_list_pagination_defaults(self) -> None:
        args = AgentsListArgs()
        assert args.offset == 0
        assert args.limit == 50

    @pytest.mark.unit
    def test_get_requires_agent_name(self) -> None:
        with pytest.raises(ValidationError):
            AgentsGetArgs.model_validate({})

    @pytest.mark.unit
    def test_create_requires_three_fields(self) -> None:
        AgentsCreateArgs(name="alice", role="engineer", department="eng")
        with pytest.raises(ValidationError):
            AgentsCreateArgs.model_validate({"name": "alice"})

    @pytest.mark.unit
    def test_delete_destructive(self) -> None:
        AgentsDeleteArgs(
            agent_name="alice",
            confirm=True,
            reason="reorg cleanup",
        )
        with pytest.raises(ValidationError):
            AgentsDeleteArgs.model_validate(
                {"agent_name": "alice", "confirm": False, "reason": "x"},
            )


class TestTrainingStartSessionArgs:
    @pytest.mark.unit
    def test_seniority_is_closed(self) -> None:
        TrainingStartSessionArgs(
            new_agent_id="a1",
            new_agent_role="engineer",
            new_agent_level="senior",
        )
        with pytest.raises(ValidationError):
            TrainingStartSessionArgs.model_validate(
                {
                    "new_agent_id": "a1",
                    "new_agent_role": "engineer",
                    "new_agent_level": "principal",
                },
            )

    @pytest.mark.unit
    def test_content_types_are_closed(self) -> None:
        args = TrainingStartSessionArgs(
            new_agent_id="a1",
            new_agent_role="r",
            new_agent_level="mid",
            enabled_content_types=("procedural", "semantic"),
        )
        assert "procedural" in args.enabled_content_types

    @pytest.mark.unit
    def test_content_types_reject_unknown(self) -> None:
        """Closed-set guard: arbitrary strings are rejected.

        Without this case the closed-set assertion would still pass
        even if ``enabled_content_types`` ever widened to ``str``.
        """
        with pytest.raises(ValidationError):
            TrainingStartSessionArgs.model_validate(
                {
                    "new_agent_id": "a1",
                    "new_agent_role": "r",
                    "new_agent_level": "mid",
                    "enabled_content_types": ("procedural", "unknown_type"),
                },
            )


class TestAutonomyUpdateArgs:
    @pytest.mark.unit
    def test_level_is_closed(self) -> None:
        AutonomyUpdateArgs(agent_id="a1", level="semi", reason="rollout")
        with pytest.raises(ValidationError):
            AutonomyUpdateArgs.model_validate(
                {"agent_id": "a1", "level": "blocked", "reason": "x"},
            )

    @pytest.mark.unit
    def test_reason_blank_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AutonomyUpdateArgs(agent_id="a1", level="full", reason="   ")
        AutonomyUpdateArgs(agent_id="a1", level="full", reason="approved by lead")
