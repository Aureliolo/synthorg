"""A judging session can perform the one act it exists to perform.

Two owners decided whether the completion reviewer could file its verdict.
``REVIEW_TOOL_PERMISSIONS`` named the tool explicitly, because STANDARD does
not reach ``ToolCategory.OTHER`` and a previous round had already watched a
reviewer be refused at the invoke boundary. The autonomy gate then refused it
again on a different ground: the tool declared ``comms:internal`` for want of
a SecOps bucket, and SUPERVISED sends everything leaving the sandbox to a
human.

The quieter owner won, and what the operator saw was the gate reporting that
the reviewer had filed no verdict, which blames the reviewer for a refusal
the product issued. A live run:

    tool.security.escalated  tool_name=submit_completion_oracle_verdict
                             reason="Human approval required by autonomy
                             level 'supervised'"

and the session is BOUNDED, so the approval it was told to wait for could not
arrive inside it. That is a wait with no reachable exit, dressed as a gate.

The invariant: a tool a narrowed session is allowed BY NAME is a tool that
session can actually call. Anything else is a permission granted and revoked
in the same breath.
"""

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.engine.completion_oracle.protocol import (
    CompletionOracleReportRepository,
)
from synthorg.engine.completion_oracle.tools.submit_verdict import (
    SubmitCompletionOracleVerdictTool,
)
from synthorg.engine.review_session import (
    REVIEW_AUTONOMY_LEVEL,
    REVIEW_TOOL_PERMISSIONS,
)
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.models import BUILTIN_PRESETS
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _auto_approved(level: AutonomyLevel) -> frozenset[str]:
    """Every concrete action type the preset for *level* auto-approves.

    Returns:
        The expanded set, so a bare category grant is compared the way the
        resolver expands it rather than as the word an operator wrote.
    """
    preset = BUILTIN_PRESETS[level.value]
    registry = ActionTypeRegistry()
    expanded: set[str] = set()
    for grant in preset.auto_approve:
        expanded |= set(registry.expand_category(grant, builtin_only=True))
    return frozenset(expanded)


def _verdict_tool() -> SubmitCompletionOracleVerdictTool:
    """Build the reviewer's terminal submit tool.

    Returns:
        The tool, with a substituted archive.
    """
    return SubmitCompletionOracleVerdictTool(
        report_repo=mock_of[CompletionOracleReportRepository]()
    )


class TestTheReviewerCanFileItsVerdict:
    def test_the_named_tool_is_callable_at_the_session_s_autonomy(self) -> None:
        tool = _verdict_tool()
        assert tool.name in REVIEW_TOOL_PERMISSIONS.allowed
        assert tool.action_type in _auto_approved(REVIEW_AUTONOMY_LEVEL), (
            f"{tool.name} is allowed by name but its action type "
            f"{tool.action_type!r} needs an approval the bounded session "
            "cannot wait for"
        )

    def test_the_verdict_is_not_classified_as_leaving_the_sandbox(self) -> None:
        # The specific misclassification, named so the convenience that
        # produced it cannot be repeated: a verdict is written to the archive
        # the gate reads, and nothing about it reaches the world.
        assert not _verdict_tool().action_type.startswith("comms:")
