"""Smoke tests for the MCP agents domain args module."""

import pytest
from pydantic import ValidationError

from synthorg.meta.mcp.domains._agents_args import (
    AgentsCreateArgs,
    AgentsDeleteArgs,
    AgentsGetArgs,
    AgentsListArgs,
    AutonomyUpdateArgs,
)

pytestmark = pytest.mark.unit


class TestAgentsCRUD:
    def test_list_pagination_defaults(self) -> None:
        args = AgentsListArgs()
        assert args.offset == 0
        assert args.limit == 50

    def test_get_requires_agent_name(self) -> None:
        with pytest.raises(ValidationError):
            AgentsGetArgs.model_validate({})

    def test_create_carries_identity(self) -> None:
        args = AgentsCreateArgs(
            identity={"name": "alice", "role": "engineer"},
            confirm=True,
            reason="staffing the new team",
        )
        assert args.identity == {"name": "alice", "role": "engineer"}
        with pytest.raises(ValidationError):
            AgentsCreateArgs.model_validate({})

    def test_create_is_guardrailed_like_delete(self) -> None:
        """Creating a principal is confirmed, like removing one.

        The payload alone is not enough: this call mints an organisational
        member that holds a role and spends budget, so it carries the same
        confirm + reason the destructive sibling does.
        """
        with pytest.raises(ValidationError):
            AgentsCreateArgs.model_validate(
                {"identity": {"name": "alice", "role": "engineer"}},
            )

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


class TestAutonomyUpdateArgs:
    def test_level_is_closed(self) -> None:
        AutonomyUpdateArgs(agent_id="a1", level="semi", reason="rollout")
        with pytest.raises(ValidationError):
            AutonomyUpdateArgs.model_validate(
                {"agent_id": "a1", "level": "blocked", "reason": "x"},
            )

    def test_reason_blank_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AutonomyUpdateArgs(agent_id="a1", level="full", reason="   ")
        AutonomyUpdateArgs(agent_id="a1", level="full", reason="approved by lead")
