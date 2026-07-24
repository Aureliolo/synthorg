"""Registry-target vocabulary shared by the catalog, the client and the tools.

A registry connection carries four operator-set facts in its ``metadata``: the
provider preset that selects the code-defined API paths + auth flow, the
repository the image is published under, the channel that decides how hard a
push is gated, and the default publish method.

The channel lives here rather than in a tool argument on purpose. It determines
the approval action type, so an agent able to assert it could route a production
push through a staging autonomy grant. Reading it from the connection record
means the agent chooses only *which* operator-approved target to use, never how
dangerous that target is.
"""

from enum import StrEnum
from typing import Final

from synthorg.core.normalization import normalize_identifier
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    REGISTRY_TARGET_METADATA_UNRECOGNISED,
)

logger = get_logger(__name__)

METADATA_KEY_PROVIDER: Final[str] = "provider"
METADATA_KEY_CHANNEL: Final[str] = "channel"
METADATA_KEY_REPOSITORY: Final[str] = "repository"
METADATA_KEY_DEFAULT_METHOD: Final[str] = "default_publish_method"
METADATA_KEY_USERNAME: Final[str] = "username"
METADATA_KEY_AUTH_HOST: Final[str] = "auth_host"


class RegistryProvider(StrEnum):
    """A container registry with a code-defined API path set + auth flow."""

    # The OCI Distribution Spec v2 protocol, shared by GHCR, Docker Hub, Quay,
    # Harbor, GitLab and Artifact Registry. One client covers them all; a
    # registry whose auth genuinely differs (ECR SigV4, GCR OAuth) is a future
    # preset, not a reason to fork the protocol.
    GENERIC_OCI = "generic_oci"


class RegistryChannel(StrEnum):
    """How much blast radius a registry target carries."""

    STAGING = "staging"
    PRODUCTION = "production"


class PublishMethod(StrEnum):
    """How an image reaches the registry.

    ``auto`` resolves from the call's inputs; the two concrete methods are the
    shipped :class:`~synthorg.tools.publish.strategies` strategies.
    """

    AUTO = "auto"
    # The agent built an OCI image layout in its run workspace; the host-side
    # tool reads it and uploads blobs + manifest with brokered credentials.
    WORKSPACE_PUSH = "workspace_push"
    # Repoint a destination tag at an existing immutable source digest already
    # in the target repository (manifest GET by digest, PUT by tag).
    DIGEST_PROMOTE = "digest_promote"


def resolve_channel(metadata: dict[str, str]) -> RegistryChannel:
    """Read the release channel from a connection's metadata.

    Args:
        metadata: The connection record's metadata mapping.

    Returns:
        The declared channel, or :attr:`RegistryChannel.PRODUCTION` when it is
        absent or unrecognised. Resolving the *stricter* value on bad data
        means a mislabelled target is over-gated rather than silently treated
        as throwaway.
    """
    declared = metadata.get(METADATA_KEY_CHANNEL, "")
    try:
        return RegistryChannel(declared)
    except ValueError:
        if declared:
            # A non-empty-but-unrecognised value is a typo (e.g. "Production",
            # "staging "): log it, because the operator will otherwise only
            # infer the over-gating from stricter-than-expected approvals. An
            # absent key is the documented fail-safe, not a misconfiguration.
            logger.warning(
                REGISTRY_TARGET_METADATA_UNRECOGNISED,
                field=METADATA_KEY_CHANNEL,
                resolved=RegistryChannel.PRODUCTION.value,
            )
        return RegistryChannel.PRODUCTION


def resolve_provider(metadata: dict[str, str]) -> RegistryProvider | None:
    """Read the provider preset from a connection's metadata.

    Args:
        metadata: The connection record's metadata mapping.

    Returns:
        The declared provider, or ``None`` when it is absent or names a preset
        this build has no client for. ``None`` is a setup problem for a human
        to fix, never a reason to guess a provider.
    """
    declared = metadata.get(METADATA_KEY_PROVIDER, "")
    try:
        return RegistryProvider(declared)
    except ValueError:
        if declared:
            logger.warning(
                REGISTRY_TARGET_METADATA_UNRECOGNISED,
                field=METADATA_KEY_PROVIDER,
                resolved="none",
            )
        return None


def resolve_repository(metadata: dict[str, str]) -> str:
    """Read the bound repository from a connection's metadata.

    Args:
        metadata: The connection record's metadata mapping.

    Returns:
        The declared repository path (e.g. ``"library/nginx"``), stripped, or
        the empty string when absent. An empty repository is a setup problem
        for a human to fix.
    """
    return metadata.get(METADATA_KEY_REPOSITORY, "").strip()


def resolve_username(metadata: dict[str, str]) -> str:
    """Read the registry username from a connection's metadata.

    Args:
        metadata: The connection record's metadata mapping.

    Returns:
        The declared username, stripped, or the empty string. Used as the
        Basic-auth principal for the token exchange; many registries accept
        an empty username with the token as the password.
    """
    return metadata.get(METADATA_KEY_USERNAME, "").strip()


def resolve_auth_host(metadata: dict[str, str]) -> str:
    """Read the operator-approved token-exchange host from metadata.

    The OCI bearer challenge advertises a ``realm`` the client fetches a
    token from, sending the brokered credential. A registry that split its
    token endpoint onto another host (Docker Hub authenticates at
    ``auth.docker.io``) must have that host declared here, so a compromised
    registry cannot redirect the credential to an arbitrary host. Empty
    means the token endpoint must live on the registry's own host.

    Args:
        metadata: The connection record's metadata mapping.

    Returns:
        The declared auth host, stripped and case-folded, or the empty
        string.
    """
    return normalize_identifier(metadata.get(METADATA_KEY_AUTH_HOST, ""))


def resolve_default_method(metadata: dict[str, str]) -> PublishMethod:
    """Read the target's default publish method from its metadata.

    Args:
        metadata: The connection record's metadata mapping.

    Returns:
        The declared default method, or :attr:`PublishMethod.AUTO` when it is
        absent or unrecognised. ``auto`` resolves the concrete method from the
        call's inputs, so an unset default degrades to input-driven selection
        rather than to a fixed method the operator did not choose.
    """
    declared = metadata.get(METADATA_KEY_DEFAULT_METHOD, "")
    try:
        return PublishMethod(declared)
    except ValueError:
        if declared:
            logger.warning(
                REGISTRY_TARGET_METADATA_UNRECOGNISED,
                field=METADATA_KEY_DEFAULT_METHOD,
                resolved=PublishMethod.AUTO.value,
            )
        return PublishMethod.AUTO


__all__ = [
    "METADATA_KEY_AUTH_HOST",
    "METADATA_KEY_CHANNEL",
    "METADATA_KEY_DEFAULT_METHOD",
    "METADATA_KEY_PROVIDER",
    "METADATA_KEY_REPOSITORY",
    "METADATA_KEY_USERNAME",
    "PublishMethod",
    "RegistryChannel",
    "RegistryProvider",
    "resolve_auth_host",
    "resolve_channel",
    "resolve_default_method",
    "resolve_provider",
    "resolve_repository",
    "resolve_username",
]
