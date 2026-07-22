"""Vercel deploy-platform client.

Paths are code-defined constants, never configuration: combined with the
pinned ``base_url`` in :class:`BaseDeployClient`, that is what makes the
egress guarantee structural rather than a policy an operator could
mis-set.

Platform deployment states are normalised onto :class:`DeployState` so
neither the tool layer nor an agent branches on vendor vocabulary.
"""

from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.integrations.deploy_api._base import BaseDeployClient
from synthorg.integrations.deploy_api._http import raise_for_deploy_status
from synthorg.integrations.deploy_api.protocol import (
    DeployLogLine,
    Deployment,
    DeployState,
)
from synthorg.integrations.errors import DeployApiError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    DEPLOY_API_DEPLOYMENT_TRIGGERED,
)

logger = get_logger(__name__)

_DEPLOYMENTS_PATH: Final[str] = "v13/deployments"
_DEPLOYMENT_PATH: Final[str] = "v13/deployments/{deployment_id}"
_LIST_PATH: Final[str] = "v6/deployments"
_EVENTS_PATH: Final[str] = "v3/deployments/{deployment_id}/events"

_STATE_MAP: Final[dict[str, DeployState]] = {
    "QUEUED": DeployState.QUEUED,
    "INITIALIZING": DeployState.BUILDING,
    "BUILDING": DeployState.BUILDING,
    "READY": DeployState.READY,
    "ERROR": DeployState.FAILED,
    "CANCELED": DeployState.CANCELLED,
}


def _normalise_state(raw: object) -> DeployState:
    """Map a platform state string onto the vendor-neutral leaf.

    Args:
        raw: The platform's ``readyState`` value.

    Returns:
        The mapped state, or :attr:`DeployState.QUEUED` when the platform
        reports something this build does not recognise. An unknown state
        is treated as still-in-flight rather than as success, so a poller
        never reports a deploy finished on a state it cannot interpret.
    """
    if isinstance(raw, str):
        return _STATE_MAP.get(raw.upper(), DeployState.QUEUED)
    return DeployState.QUEUED


def _deployment_from(payload: object) -> Deployment:
    """Build a :class:`Deployment` from a platform payload.

    Args:
        payload: One deployment object from the platform response.

    Returns:
        The parsed deployment.

    Raises:
        DeployApiError: When the payload is not an object carrying an id.
    """
    if not isinstance(payload, dict):
        msg = "deploy platform returned a non-object deployment"
        raise DeployApiError(msg)
    raw_id = payload.get("id") or payload.get("uid")
    if not isinstance(raw_id, str) or not raw_id.strip():
        msg = "deploy platform returned a deployment without an id"
        raise DeployApiError(msg)
    url = payload.get("url")
    created = payload.get("createdAt")
    return Deployment(
        id=NotBlankStr(raw_id),
        state=_normalise_state(payload.get("readyState") or payload.get("state")),
        url=f"https://{url}" if isinstance(url, str) and url else "",
        created_at=str(created) if created is not None else "",
    )


class VercelDeployClient(BaseDeployClient):
    """Deploy client for the Vercel deployments API."""

    def __init__(
        self,
        *,
        api_base_url: str,
        token: str,
        timeout: float,
        project: NotBlankStr,
    ) -> None:
        super().__init__(
            api_base_url=api_base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        self._project = project

    async def trigger_deployment(self, *, git_ref: str) -> Deployment:
        """Start a deployment of ``git_ref`` for the bound project.

        Args:
            git_ref: The git ref to deploy.

        Returns:
            The created deployment.
        """
        body: dict[str, object] = {"name": str(self._project), "target": "production"}
        if git_ref:
            body["gitSource"] = {"ref": git_ref}
        resp = await self._request(
            "POST", _DEPLOYMENTS_PATH, action="trigger a deployment", json=body
        )
        raise_for_deploy_status(resp, action="trigger a deployment")
        deployment = _deployment_from(resp.json())
        logger.info(
            DEPLOY_API_DEPLOYMENT_TRIGGERED,
            deployment_id=deployment.id,
            state=deployment.state.value,
        )
        return deployment

    async def get_deployment(self, *, deployment_id: NotBlankStr) -> Deployment:
        """Fetch one deployment's current state.

        Args:
            deployment_id: The platform's deployment identifier.

        Returns:
            The deployment record.
        """
        path = _DEPLOYMENT_PATH.format(deployment_id=deployment_id)
        resp = await self._request("GET", path, action="read a deployment")
        raise_for_deploy_status(resp, action="read a deployment")
        return _deployment_from(resp.json())

    async def list_deployments(self, *, limit: int) -> tuple[Deployment, ...]:
        """List recent deployments for the bound project, newest first.

        Args:
            limit: Maximum number of deployments to return.

        Returns:
            The deployment records.

        Raises:
            DeployApiError: When the platform returns a non-list payload.
        """
        resp = await self._request(
            "GET",
            _LIST_PATH,
            action="list deployments",
            params={"app": str(self._project), "limit": limit},
        )
        raise_for_deploy_status(resp, action="list deployments")
        payload = resp.json()
        items = payload.get("deployments") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            msg = "deploy platform returned no deployment list"
            raise DeployApiError(msg)
        return tuple(_deployment_from(item) for item in items[:limit])

    async def get_deployment_logs(
        self, *, deployment_id: NotBlankStr, limit: int
    ) -> tuple[DeployLogLine, ...]:
        """Fetch a deployment's log lines.

        Args:
            deployment_id: The platform's deployment identifier.
            limit: Maximum number of log lines to return.

        Returns:
            The log lines, oldest first.
        """
        path = _EVENTS_PATH.format(deployment_id=deployment_id)
        resp = await self._request(
            "GET", path, action="read deployment logs", params={"limit": limit}
        )
        raise_for_deploy_status(resp, action="read deployment logs")
        payload = resp.json()
        events = payload if isinstance(payload, list) else []
        lines: list[DeployLogLine] = []
        for event in events[:limit]:
            if not isinstance(event, dict):
                continue
            text = event.get("text") or event.get("payload")
            if isinstance(text, dict):
                text = text.get("text")
            created = event.get("created") or event.get("date")
            lines.append(
                DeployLogLine(
                    timestamp=str(created) if created is not None else "",
                    text=text if isinstance(text, str) else "",
                )
            )
        return tuple(lines)


__all__ = ["VercelDeployClient"]
