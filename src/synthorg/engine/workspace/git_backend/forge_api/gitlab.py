"""GitLab REST API forge client (repository existence + creation)."""

from typing import TYPE_CHECKING, Final
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.api.boundary import parse_typed
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

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger(__name__)

_HTTP_NOT_FOUND: Final[int] = 404


class _GitLabUser(BaseModel):  # lint-allow: frozen-extra-forbid -- forge extras
    """Typed view of the ``GET /user`` fields the client consumes.

    ``extra="ignore"`` because the forge response carries many fields
    beyond the username we model.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    username: NotBlankStr


class _GitLabNamespace(BaseModel):  # lint-allow: frozen-extra-forbid -- forge extras
    """Typed view of the ``GET /namespaces/<owner>`` id field.

    ``extra="ignore"`` because the namespace payload carries many fields
    beyond the id we model.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: int


class _GitLabProject(BaseModel):  # lint-allow: frozen-extra-forbid -- forge extras
    """Typed view of the project-creation response fields the client uses.

    ``extra="ignore"`` because the forge payload carries many fields
    beyond the four we model.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    path_with_namespace: NotBlankStr
    http_url_to_repo: NotBlankStr
    default_branch: str = "main"
    visibility: str = ""


class GitLabForgeClient(BaseForgeClient):
    """Creates/inspects projects via the GitLab REST API (v4).

    GitLab addresses repositories as ``namespace/project``; creation
    posts to ``/projects`` with a resolved ``namespace_id`` (the
    authenticated user's own namespace is implicit when ``owner`` is
    the token user, so no ``namespace_id`` is sent in that case).
    """

    def __init__(self, *, api_base_url: str, token: str, timeout: float) -> None:
        super().__init__(
            api_base_url=api_base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    async def repo_exists(self, *, owner: NotBlankStr, repo: NotBlankStr) -> bool:
        """Return ``True`` if ``owner/repo`` exists."""
        encoded = quote(f"{owner}/{repo}", safe="")
        action = f"check project {owner}/{repo}"
        resp = await self._request("GET", f"/projects/{encoded}", action=action)
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
        """Create ``owner/repo``; resolve the group namespace if needed."""
        payload: dict[str, object] = {
            "name": str(repo),
            "path": str(repo),
            "visibility": "private" if private else "public",
        }
        username = await self._authenticated_username()
        # Forge logins are case-insensitive, so match case-folded.
        if username.casefold() != str(owner).casefold():
            payload["namespace_id"] = await self._namespace_id(owner)
        action = f"create project {owner}/{repo}"
        resp = await self._request("POST", "/projects", action=action, json=payload)
        raise_for_forge_status(resp, action=action)
        repo_model = _parse_repo(resp.json())
        logger.info(FORGE_API_REPO_CREATED, full_name=str(repo_model.full_name))
        return repo_model

    async def _authenticated_username(self) -> str:
        action = "resolve authenticated user"
        resp = await self._request("GET", "/user", action=action)
        raise_for_forge_status(resp, action=action)
        try:
            user = parse_typed("forge.gitlab.user", resp.json(), _GitLabUser)
        except ValidationError as exc:
            msg = "GitLab /user response missing 'username'"
            raise GitBackendForgeApiError(msg) from exc
        return str(user.username)

    async def _namespace_id(self, owner: NotBlankStr) -> int:
        action = f"resolve namespace {owner}"
        resp = await self._request(
            "GET",
            f"/namespaces/{quote(str(owner), safe='')}",
            action=action,
        )
        raise_for_forge_status(resp, action=action)
        try:
            namespace = parse_typed(
                "forge.gitlab.namespace", resp.json(), _GitLabNamespace
            )
        except ValidationError as exc:
            msg = f"GitLab namespace {owner!r} response missing integer 'id'"
            raise GitBackendForgeApiError(msg) from exc
        return namespace.id


def _parse_repo(data: Mapping[str, object] | None) -> ForgeRepo:
    try:
        project = parse_typed("forge.gitlab.project", data, _GitLabProject)
    except ValidationError as exc:
        msg = "GitLab project response missing 'path_with_namespace'/'http_url_to_repo'"
        raise GitBackendForgeApiError(msg) from exc
    return ForgeRepo(
        full_name=project.path_with_namespace,
        default_branch=NotBlankStr(project.default_branch or "main"),
        private=project.visibility == "private",
        clone_url=project.http_url_to_repo,
    )


__all__ = ["GitLabForgeClient"]
