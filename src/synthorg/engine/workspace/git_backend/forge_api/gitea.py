"""Gitea/Forgejo REST API forge client (existence + creation).

Forgejo is a Gitea fork sharing the ``/api/v1`` REST surface, so
:class:`ForgejoForgeClient` is a thin subclass selected by a distinct
:class:`ConnectionType` discriminator, mirroring the
``_GiteaFamilyAuthenticator`` split in the connection authenticators.
"""

from typing import Any, Final

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendForgeApiError
from synthorg.engine.workspace.git_backend.forge_api._base import BaseForgeClient
from synthorg.engine.workspace.git_backend.forge_api._http import (
    raise_for_forge_status,
)
from synthorg.engine.workspace.git_backend.forge_api.protocol import ForgeRepo
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    FORGE_API_REPO_CREATED,
    FORGE_API_REPO_EXISTS_CHECK,
)

logger = get_logger(__name__)

_HTTP_NOT_FOUND: Final[int] = 404


class GiteaForgeClient(BaseForgeClient):
    """Creates/inspects repositories via the Gitea REST API (v1)."""

    def __init__(self, *, api_base_url: str, token: str, timeout: float) -> None:
        super().__init__(
            api_base_url=api_base_url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    async def repo_exists(self, *, owner: NotBlankStr, repo: NotBlankStr) -> bool:
        """Return ``True`` if ``owner/repo`` exists."""
        action = f"check repo {owner}/{repo}"
        resp = await self._request("GET", f"/repos/{owner}/{repo}", action=action)
        if resp.status_code == _HTTP_NOT_FOUND:
            logger.info(FORGE_API_REPO_EXISTS_CHECK, exists=False)
            return False
        raise_for_forge_status(resp, action=action)
        logger.info(FORGE_API_REPO_EXISTS_CHECK, exists=True)
        return True

    async def create_repo(
        self,
        *,
        owner: NotBlankStr,
        repo: NotBlankStr,
        private: bool = True,
    ) -> ForgeRepo:
        """Create ``owner/repo`` under the user or org namespace."""
        username = await self._authenticated_username()
        url = "/user/repos" if username == str(owner) else f"/orgs/{owner}/repos"
        action = f"create repo {owner}/{repo}"
        resp = await self._request(
            "POST",
            url,
            action=action,
            json={"name": str(repo), "private": private},
        )
        raise_for_forge_status(resp, action=action)
        repo_model = _parse_repo(resp.json())
        logger.info(FORGE_API_REPO_CREATED, full_name=str(repo_model.full_name))
        return repo_model

    async def _authenticated_username(self) -> str:
        action = "resolve authenticated user"
        resp = await self._request("GET", "/user", action=action)
        raise_for_forge_status(resp, action=action)
        username = resp.json().get("login")
        if not isinstance(username, str) or not username:
            msg = "Gitea /user response missing 'login'"
            raise GitBackendForgeApiError(msg)
        return username


class ForgejoForgeClient(GiteaForgeClient):
    """Forgejo forge client; shares the Gitea ``/api/v1`` surface."""


def _parse_repo(data: dict[str, Any]) -> ForgeRepo:
    full_name = data.get("full_name")
    default_branch = data.get("default_branch") or "main"
    clone_url = data.get("clone_url")
    if not isinstance(full_name, str) or not isinstance(clone_url, str):
        msg = "Gitea repo response missing 'full_name'/'clone_url'"
        raise GitBackendForgeApiError(msg)
    return ForgeRepo(
        full_name=NotBlankStr(full_name),
        default_branch=NotBlankStr(default_branch),
        private=bool(data.get("private", True)),
        clone_url=NotBlankStr(clone_url),
    )


__all__ = ["ForgejoForgeClient", "GiteaForgeClient"]
