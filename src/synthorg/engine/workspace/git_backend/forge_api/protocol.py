"""Forge REST API client protocol for repository provisioning.

The external-remote git backend clones/pushes over HTTPS, but creating
a repository that does not exist yet requires the forge's REST API
(git itself cannot create a remote repo). This protocol abstracts the
per-forge create/exists surface; concrete implementations live beside
it (``github`` / ``gitlab`` / ``gitea``; Forgejo reuses Gitea's REST
surface) and are selected by :class:`ConnectionType` via the factory.

APIs diverge enough (namespace model, endpoint shapes, rate-limit
headers) that a single client cannot serve all four, so each forge has
its own implementation rather than the shared token-only validator the
authenticators started with.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr  # noqa: TC001


class ForgeRepo(BaseModel):
    """Result of a forge repository lookup or creation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    full_name: NotBlankStr
    default_branch: NotBlankStr
    private: bool
    clone_url: NotBlankStr


@runtime_checkable
class ForgeApiClient(Protocol):
    """Per-forge REST client for repository existence + provisioning."""

    async def repo_exists(self, *, owner: NotBlankStr, repo: NotBlankStr) -> bool:
        """Return ``True`` if ``owner/repo`` exists on the forge.

        Raises:
            GitBackendForgeAuthError: Credentials are invalid/expired.
            GitBackendRateLimitError: The forge rate-limited the call.
            GitBackendForgeApiError: Any other non-2xx / transport error.
        """
        ...

    async def create_repo(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        private: bool = True,
    ) -> ForgeRepo:
        """Create ``owner/repo`` on the forge and return its descriptor.

        Raises:
            GitBackendForgeAuthError: Credentials are invalid/expired.
            GitBackendRateLimitError: The forge rate-limited the call.
            GitBackendForgeApiError: Any other non-2xx / transport error.
        """
        ...

    async def aclose(self) -> None:
        """Release the underlying HTTP client."""
        ...


__all__ = ["ForgeApiClient", "ForgeRepo"]
