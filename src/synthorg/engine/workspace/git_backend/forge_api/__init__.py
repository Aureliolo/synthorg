"""Forge REST API clients for external-remote repository provisioning.

Two client surfaces share this package: the narrow provisioning surface
(:class:`ForgeApiClient`) the external-remote git backend uses, and the
richer agent-operations surface (:class:`ForgeAgentApiClient`) the
agent-facing forge tools drive.
"""

from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeCiRun,
    ForgeComment,
    ForgeDirEntry,
    ForgeEntryKind,
    ForgeFileContent,
    ForgeIssue,
    ForgeIssueState,
    ForgeMergeMethod,
    ForgeMergeResult,
    ForgeOpenClosedState,
    ForgePullRequest,
    ForgePullState,
    ForgeReview,
    ForgeReviewDecision,
)
from synthorg.engine.workspace.git_backend.forge_api.agent_protocol import (
    ForgeAgentApiClient,
)
from synthorg.engine.workspace.git_backend.forge_api.factory import (
    build_forge_agent_api_client,
    build_forge_api_client,
    forge_agent_api_supported,
)
from synthorg.engine.workspace.git_backend.forge_api.protocol import (
    ForgeApiClient,
    ForgeRepo,
)

__all__ = [
    "ForgeAgentApiClient",
    "ForgeApiClient",
    "ForgeCiRun",
    "ForgeComment",
    "ForgeDirEntry",
    "ForgeEntryKind",
    "ForgeFileContent",
    "ForgeIssue",
    "ForgeIssueState",
    "ForgeMergeMethod",
    "ForgeMergeResult",
    "ForgeOpenClosedState",
    "ForgePullRequest",
    "ForgePullState",
    "ForgeRepo",
    "ForgeReview",
    "ForgeReviewDecision",
    "build_forge_agent_api_client",
    "build_forge_api_client",
    "forge_agent_api_supported",
]
