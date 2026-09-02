"""Tests for the identity a quality gate dispatches its judge under.

A gate reads an artefact another agent wrote, so an injection planted in
that work executes inside the reviewing session. What it can reach is
decided by the identity the gate dispatches, and a roster agent carries
whatever its operator gave it for its day job. These pin that the session
is narrowed to what judging needs and that nothing about the agent's own
identity is rewritten in the process.
"""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, ToolPermissions
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.tool_names import (
    SUBMIT_COMPLETION_ORACLE_VERDICT_TOOL_NAME,
)
from synthorg.engine.review_session import as_review_session
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.permissions import ToolPermissionChecker
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


def _privileged_holder() -> AgentIdentity:
    """Build a role holder an operator granted a broad day-job surface."""
    return AgentIdentity(
        id=as_uuid("senior-qa"),
        name=NotBlankStr("Ada"),
        role=NotBlankStr(COMPLETION_REVIEWER_ROLE_NAME),
        department=NotBlankStr("Quality Assurance"),
        model=ModelConfig(
            provider=NotBlankStr("example-provider"),
            model_id=NotBlankStr("example-expert-001"),
            capability="expert",
        ),
        tools=ToolPermissions(
            access_level=ToolAccessLevel.ELEVATED,
            mcp_capabilities=(NotBlankStr("*"),),
        ),
        autonomy_level=AutonomyLevel.FULL,
        hiring_date=date(2026, 1, 15),
    )


class TestNarrowing:
    def test_the_session_drops_elevated_access_and_mcp_reach(self) -> None:
        session = as_review_session(_privileged_holder())
        assert session.tools.access_level is ToolAccessLevel.STANDARD
        assert session.tools.mcp_capabilities == ()

    def test_the_session_runs_supervised(self) -> None:
        session = as_review_session(_privileged_holder())
        assert session.autonomy_level is AutonomyLevel.SUPERVISED

    def test_identity_role_and_bound_model_survive(self) -> None:
        """Narrowing authority must not rewrite attribution or the pair.

        The verdict is recorded against the real agent and runs on the model
        its operator chose; only what the session may DO is reduced.
        """
        holder = _privileged_holder()
        session = as_review_session(holder)
        assert session.id == holder.id
        assert session.name == holder.name
        assert session.role == holder.role
        assert session.department == holder.department
        assert session.model == holder.model

    def test_the_session_can_file_the_verdict_it_exists_to_file(self) -> None:
        """Narrowing must not remove the one thing judging is for.

        The verdict tool is ``ToolCategory.OTHER``, which STANDARD does not
        admit, so the category check alone denies it. A live run handed the
        reviewer the tool in its registry and refused it at the invoke
        boundary twice, after which the session went looking for another way
        to file and never produced a verdict at all.
        """
        session = as_review_session(_privileged_holder())
        permissions = ToolPermissionChecker.from_permissions(session.tools)
        assert permissions.is_permitted(
            SUBMIT_COMPLETION_ORACLE_VERDICT_TOOL_NAME, ToolCategory.OTHER
        )

    def test_the_allowance_is_that_one_tool_and_not_the_category(self) -> None:
        """Allow by name, so no other OTHER-category tool comes with it."""
        session = as_review_session(_privileged_holder())
        permissions = ToolPermissionChecker.from_permissions(session.tools)
        assert not permissions.is_permitted("some_other_tool", ToolCategory.OTHER)

    @pytest.mark.parametrize(
        "tool_name",
        ["deploy_release", "publish_push", "forge_push", "chat_messages"],
    )
    def test_no_governed_connection_tool_is_reachable(self, tool_name: str) -> None:
        """A judge cannot reach anything that acts outside the organisation.

        The engine appends the governed connection families to every run's
        registry without consulting the identity, and STANDARD grants their
        category, so the refusal has to come from the session's own
        permissions. An injection planted in the reviewed artefact runs here.
        """
        session = as_review_session(_privileged_holder())
        permissions = ToolPermissionChecker.from_permissions(session.tools)
        assert not permissions.is_permitted(tool_name, ToolCategory.EXTERNAL_DATA)

    def test_the_bar_is_the_category_not_a_name_list(self) -> None:
        """A tool added to the category later is refused without an edit."""
        session = as_review_session(_privileged_holder())
        permissions = ToolPermissionChecker.from_permissions(session.tools)
        assert not permissions.is_permitted(
            "some_future_external_tool", ToolCategory.EXTERNAL_DATA
        )

    def test_reading_the_deliverable_still_works_and_writing_does_not(self) -> None:
        """The narrowing keeps what judging rests on and nothing that authors.

        A judge that writes or runs a shell is authoring what it judges; the
        recorded build and test runs reach it through the completion gates'
        own record instead.
        """
        session = as_review_session(_privileged_holder())
        permissions = ToolPermissionChecker.from_permissions(session.tools)
        assert permissions.is_permitted("read_file", ToolCategory.FILE_SYSTEM)
        assert not permissions.is_permitted("write_file", ToolCategory.FILE_SYSTEM)
        assert not permissions.is_permitted("shell_command", ToolCategory.TERMINAL)

    def test_the_roster_identity_is_untouched(self) -> None:
        holder = _privileged_holder()
        as_review_session(holder)
        assert holder.tools.access_level is ToolAccessLevel.ELEVATED
        assert holder.autonomy_level is AutonomyLevel.FULL
