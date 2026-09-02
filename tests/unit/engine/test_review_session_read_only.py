"""A judging session reads; it never writes, runs or commits.

A judge that writes is authoring what it judges. A recorded corpus put 36
file-writing shell calls in sessions whose only job was to file a verdict,
so the narrowing now withholds every way of running a command and every
mutating member of the categories a reviewer keeps for reading.
"""

import pytest

from synthorg.engine.completion_oracle.tool_names import (
    SUBMIT_COMPLETION_ORACLE_VERDICT_TOOL_NAME,
)
from synthorg.engine.review_session import (
    REVIEW_DENIED_TOOLS,
    REVIEW_TOOL_PERMISSIONS,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.permissions import ToolPermissionChecker

pytestmark = pytest.mark.unit


def _checker() -> ToolPermissionChecker:
    return ToolPermissionChecker.from_permissions(REVIEW_TOOL_PERMISSIONS)


class TestTheReviewerIsReadOnly:
    @pytest.mark.parametrize(
        ("tool", "category"),
        [
            ("write_file", ToolCategory.FILE_SYSTEM),
            ("edit_file", ToolCategory.FILE_SYSTEM),
            ("delete_file", ToolCategory.FILE_SYSTEM),
            ("git_commit", ToolCategory.VERSION_CONTROL),
            ("shell_command", ToolCategory.TERMINAL),
            ("run_code", ToolCategory.CODE_EXECUTION),
            ("deploy_release", ToolCategory.EXTERNAL_DATA),
        ],
    )
    def test_mutating_and_executing_tools_are_refused(
        self, tool: str, category: ToolCategory
    ) -> None:
        assert not _checker().is_permitted(tool, category)

    @pytest.mark.parametrize(
        ("tool", "category"),
        [
            ("read_file", ToolCategory.FILE_SYSTEM),
            ("list_directory", ToolCategory.FILE_SYSTEM),
            ("git_diff", ToolCategory.VERSION_CONTROL),
            ("git_log", ToolCategory.VERSION_CONTROL),
            (SUBMIT_COMPLETION_ORACLE_VERDICT_TOOL_NAME, ToolCategory.OTHER),
        ],
    )
    def test_reading_and_filing_stay_permitted(
        self, tool: str, category: ToolCategory
    ) -> None:
        assert _checker().is_permitted(tool, category)

    def test_every_named_denial_sits_in_a_category_the_reviewer_keeps(
        self,
    ) -> None:
        # A name in the list is only needed where the category cannot be
        # withheld whole; a name whose category is already denied is a
        # second answer to the same question.
        kept = {ToolCategory.FILE_SYSTEM, ToolCategory.VERSION_CONTROL}
        for name in REVIEW_DENIED_TOOLS:
            assert any(not _checker().is_permitted(name, category) for category in kept)
        assert not (set(REVIEW_TOOL_PERMISSIONS.denied_categories) & kept)
