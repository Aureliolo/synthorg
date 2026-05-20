"""GitHub REST API forge client (repository existence + creation)."""

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

_API_VERSION: Final[str] = "2022-11-28"
_HTTP_NOT_FOUND: Final[int] = 404


class GitHubForgeClient(BaseForgeClient):
    """Creates/inspects repositories via the GitHub REST API.

    Resolves whether ``owner`` is the authenticated user or an
    organisation (``GET /user``) so creation targets the correct
    endpoint (``/user/repos`` vs ``/orgs/{owner}/repos``); GitHub has
    no single create-under-any-owner endpoint.
    """

    def __init__(self, *, api_base_url: str, token: str, timeout: float) -> None:
        super().__init__(
            api_base_url=api_base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _API_VERSION,
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
        login = await self._authenticated_login()
        url = "/user/repos" if login == str(owner) else f"/orgs/{owner}/repos"
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

    async def _authenticated_login(self) -> str:
        """Resolve the token's account login (``GET /user``)."""
        action = "resolve authenticated user"
        resp = await self._request("GET", "/user", action=action)
        raise_for_forge_status(resp, action=action)
        login = resp.json().get("login")
        if not isinstance(login, str) or not login:
            msg = "GitHub /user response missing 'login'"
            raise GitBackendForgeApiError(msg)
        return login


def _parse_repo(data: dict[str, Any]) -> ForgeRepo:
    full_name = data.get("full_name")
    default_branch = data.get("default_branch") or "main"
    clone_url = data.get("clone_url")
    if not isinstance(full_name, str) or not isinstance(clone_url, str):
        msg = "GitHub repo response missing 'full_name'/'clone_url'"
        raise GitBackendForgeApiError(msg)
    return ForgeRepo(
        full_name=NotBlankStr(full_name),
        default_branch=NotBlankStr(default_branch),
        private=bool(data.get("private", True)),
        clone_url=NotBlankStr(clone_url),
    )


__all__ = ["GitHubForgeClient"]
