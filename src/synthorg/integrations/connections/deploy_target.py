"""Deploy-target vocabulary shared by the catalog, the client and the tools.

A deploy connection carries three operator-set facts in its ``metadata``: the
platform preset that selects the code-defined API paths, the environment that
decides how hard the action is gated, and the project the platform deploys.

The environment lives here rather than in a tool argument on purpose. It
determines the approval action type, so an agent able to assert it could
route a production release through a staging autonomy grant. Reading it from
the connection record means the agent chooses only *which* operator-approved
target to use, never how dangerous that target is.
"""

from enum import StrEnum
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    DEPLOY_TARGET_METADATA_UNRECOGNISED,
)

logger = get_logger(__name__)

METADATA_KEY_PLATFORM: Final[str] = "platform"
METADATA_KEY_ENVIRONMENT: Final[str] = "environment"
METADATA_KEY_PROJECT: Final[str] = "project"


class DeployPlatform(StrEnum):
    """A hosting platform with a code-defined deploy API path set."""

    VERCEL = "vercel"


class DeployEnvironment(StrEnum):
    """How much blast radius a deploy target carries."""

    STAGING = "staging"
    PRODUCTION = "production"


def resolve_environment(metadata: dict[str, str]) -> DeployEnvironment:
    """Read the target environment from a connection's metadata.

    Args:
        metadata: The connection record's metadata mapping.

    Returns:
        The declared environment, or :attr:`DeployEnvironment.PRODUCTION`
        when it is absent or unrecognised. Resolving the *stricter* value
        on bad data means a mislabelled target is over-gated rather than
        silently treated as throwaway.
    """
    declared = metadata.get(METADATA_KEY_ENVIRONMENT, "")
    try:
        return DeployEnvironment(declared)
    except ValueError:
        if declared:
            # A non-empty-but-unrecognised value is a typo (e.g. "Production",
            # "staging "): log it, because the operator will otherwise only
            # infer the over-gating from stricter-than-expected approvals. An
            # absent key is the documented fail-safe, not a misconfiguration.
            logger.warning(
                DEPLOY_TARGET_METADATA_UNRECOGNISED,
                field=METADATA_KEY_ENVIRONMENT,
                resolved=DeployEnvironment.PRODUCTION.value,
            )
        return DeployEnvironment.PRODUCTION


def resolve_platform(metadata: dict[str, str]) -> DeployPlatform | None:
    """Read the platform preset from a connection's metadata.

    Args:
        metadata: The connection record's metadata mapping.

    Returns:
        The declared platform, or ``None`` when it is absent or names a
        preset this build has no client for. ``None`` is a setup problem
        for a human to fix, never a reason to guess a platform.
    """
    declared = metadata.get(METADATA_KEY_PLATFORM, "")
    try:
        return DeployPlatform(declared)
    except ValueError:
        if declared:
            logger.warning(
                DEPLOY_TARGET_METADATA_UNRECOGNISED,
                field=METADATA_KEY_PLATFORM,
                resolved="none",
            )
        return None


__all__ = [
    "METADATA_KEY_ENVIRONMENT",
    "METADATA_KEY_PLATFORM",
    "METADATA_KEY_PROJECT",
    "DeployEnvironment",
    "DeployPlatform",
    "resolve_environment",
    "resolve_platform",
]
