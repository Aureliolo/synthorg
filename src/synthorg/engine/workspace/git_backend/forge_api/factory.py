"""Forge-API client factory keyed on the connection type.

Derives the forge REST base URL from the connection's git ``base_url``
host (the git base and the API base differ per forge) and dispatches to
the matching per-forge client via a
:class:`~synthorg.core.registry.StrategyRegistry`.
"""

from typing import Final
from urllib.parse import urlsplit

from synthorg.core.registry import StrategyRegistry
from synthorg.engine.errors import GitBackendConfigError
from synthorg.engine.workspace.git_backend.forge_api.gitea import (
    ForgejoForgeClient,
    GiteaForgeClient,
)
from synthorg.engine.workspace.git_backend.forge_api.github import GitHubForgeClient
from synthorg.engine.workspace.git_backend.forge_api.gitlab import GitLabForgeClient
from synthorg.engine.workspace.git_backend.forge_api.protocol import ForgeApiClient
from synthorg.integrations.connections.models import ConnectionType

_GITHUB_COM_HOST: Final[str] = "github.com"
_GITHUB_PUBLIC_API: Final[str] = "https://api.github.com"
_GITHUB_ENTERPRISE_API_SUFFIX: Final[str] = "/api/v3"
_GITLAB_API_SUFFIX: Final[str] = "/api/v4"
_GITEA_API_SUFFIX: Final[str] = "/api/v1"


def _host_origin(base_url: str) -> tuple[str, str]:
    """Return ``(scheme://host[:port], host)`` from a git base URL.

    Raises:
        GitBackendConfigError: When ``base_url`` is not HTTPS or
            carries no parseable host.
    """
    split = urlsplit(base_url)
    if split.scheme != "https" or not split.hostname:
        msg = "forge connection base_url must be an https URL with a host"
        raise GitBackendConfigError(msg)
    host = split.hostname
    netloc = f"{host}:{split.port}" if split.port is not None else host
    return f"https://{netloc}", host


def _github_api_base(base_url: str) -> str:
    origin, host = _host_origin(base_url)
    if host == _GITHUB_COM_HOST:
        return _GITHUB_PUBLIC_API
    return f"{origin}{_GITHUB_ENTERPRISE_API_SUFFIX}"


def _gitlab_api_base(base_url: str) -> str:
    origin, _ = _host_origin(base_url)
    return f"{origin}{_GITLAB_API_SUFFIX}"


def _gitea_api_base(base_url: str) -> str:
    origin, _ = _host_origin(base_url)
    return f"{origin}{_GITEA_API_SUFFIX}"


def _build_github(base_url: str, token: str, timeout: float) -> ForgeApiClient:
    return GitHubForgeClient(
        api_base_url=_github_api_base(base_url),
        token=token,
        timeout=timeout,
    )


def _build_gitlab(base_url: str, token: str, timeout: float) -> ForgeApiClient:
    return GitLabForgeClient(
        api_base_url=_gitlab_api_base(base_url),
        token=token,
        timeout=timeout,
    )


def _build_gitea(base_url: str, token: str, timeout: float) -> ForgeApiClient:
    return GiteaForgeClient(
        api_base_url=_gitea_api_base(base_url),
        token=token,
        timeout=timeout,
    )


def _build_forgejo(base_url: str, token: str, timeout: float) -> ForgeApiClient:
    return ForgejoForgeClient(
        api_base_url=_gitea_api_base(base_url),
        token=token,
        timeout=timeout,
    )


_REGISTRY: StrategyRegistry[ForgeApiClient] = StrategyRegistry(
    {
        ConnectionType.GITHUB: _build_github,
        ConnectionType.GITLAB: _build_gitlab,
        ConnectionType.GITEA: _build_gitea,
        ConnectionType.FORGEJO: _build_forgejo,
    },
    kind="forge_api_client",
)


def build_forge_api_client(
    *,
    connection_type: ConnectionType,
    base_url: str,
    token: str,
    timeout: float,
) -> ForgeApiClient:
    """Build the per-forge REST client for repository provisioning.

    Args:
        connection_type: Selects the forge implementation.
        base_url: The connection's git base URL (host derives the API base).
        token: Resolved access token (header auth only, never logged).
        timeout: Per-request timeout in seconds.

    Returns:
        A client satisfying :class:`ForgeApiClient`.

    Raises:
        StrategyFactoryNotFoundError: ``connection_type`` is not a forge.
        GitBackendConfigError: ``base_url`` is not a valid https URL.
    """
    return _REGISTRY.build(connection_type, base_url, token, timeout)


__all__ = ["build_forge_api_client"]
