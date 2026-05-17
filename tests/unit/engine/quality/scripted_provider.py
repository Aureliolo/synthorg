"""Re-export of the canonical scripted provider for the quality suites.

The implementation lives in :mod:`tests._shared.scripted_provider`;
this module keeps the historical import path stable for the LLM
decomposer and grader suites.
"""

from tests._shared.scripted_provider import (
    TEST_CAPABILITIES,
    ScriptedProvider,
    build_tool_call_response,
)

__all__ = [
    "TEST_CAPABILITIES",
    "ScriptedProvider",
    "build_tool_call_response",
]
