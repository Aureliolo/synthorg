# module-kind: declarative
"""Canonical completion-oracle tool names.

A leaf with no imports, because the name is needed by modules the tool's own
module cannot reach without a cycle: the judging session narrows an identity
and must allow this tool by name, and reaching for it in ``submit_verdict``
pulls the report protocol, the review models and the red-team package back
round into the session module that started the import.
"""

from typing import Final

SUBMIT_COMPLETION_ORACLE_VERDICT_TOOL_NAME: Final[str] = (
    "submit_completion_oracle_verdict"
)
"""Canonical tool name. Used by the gate prompt, the review session, and tests."""
