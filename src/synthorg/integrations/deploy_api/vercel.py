"""Vercel deploy-platform client.

Paths are code-defined constants, never configuration: combined with the
pinned ``base_url`` in :class:`BaseDeployClient`, that is what makes the
egress guarantee structural rather than a policy an operator could
mis-set.

Platform deployment states are normalised onto :class:`DeployState` so
neither the tool layer nor an agent branches on vendor vocabulary. The
client is bound to one project *and one environment*: the environment
decides the vendor-side ``target``, so a staging connection can never emit
a production release.
"""

from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.deploy_target import DeployEnvironment
from synthorg.integrations.deploy_api._base import BaseDeployClient
from synthorg.integrations.deploy_api._http import (
    raise_for_deploy_status,
    sanitize_body,
)
from synthorg.integrations.deploy_api.protocol import (
    DeployLogLine,
    Deployment,
    DeployState,
)
from synthorg.integrations.errors import DeployApiError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    DEPLOY_API_DEPLOYMENT_TRIGGERED,
    DEPLOY_API_REQUEST_FAILED,
    DEPLOY_API_UNKNOWN_STATE,
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

# Vendor-side deploy target per environment. Staging must never map to
# "production": that is the whole point of separating the two, and the
# approval gating upstream is meaningless if the vendor call ignores it.
_TARGET_MAP: Final[dict[DeployEnvironment, str]] = {
    DeployEnvironment.STAGING: "staging",
    DeployEnvironment.PRODUCTION: "production",
}


def _normalise_state(raw: object) -> DeployState:
    """Map a platform state string onto the vendor-neutral leaf.

    Args:
        raw: The platform's ``readyState`` value.

    Returns:
        The mapped state, or :attr:`DeployState.QUEUED` when the platform
        reports something this build does not recognise. An unknown state
        is treated as still-in-flight rather than as success, so a poller
        never reports a deploy finished on a state it cannot interpret. The
        fallback is logged so vendor-state-vocabulary drift is observable
        instead of silently stalling every poll on that state.
    """
    if isinstance(raw, str):
        mapped = _STATE_MAP.get(raw.upper())
        if mapped is not None:
            return mapped
    logger.warning(DEPLOY_API_UNKNOWN_STATE, raw_state=sanitize_body(str(raw)))
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
        logger.warning(
            DEPLOY_API_REQUEST_FAILED,
            action="parse a deployment",
            detail="non-object deployment payload",
        )
        msg = "deploy platform returned a non-object deployment"
        raise DeployApiError(msg)
    raw_id = payload.get("id") or payload.get("uid")
    if not isinstance(raw_id, str) or not raw_id.strip():
        logger.warning(
            DEPLOY_API_REQUEST_FAILED,
            action="parse a deployment",
            detail="deployment payload without an id",
        )
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
        environment: DeployEnvironment,
    ) -> None:
        super().__init__(
            api_base_url=api_base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        self._project = project
        self._target = _TARGET_MAP[environment]

    @property
    def project(self) -> NotBlankStr:
        """The operator-configured project this client is bound to.

        Returns:
            The bound project identifier.
        """
        return self._project

    async def trigger_deployment(self, *, git_ref: str) -> Deployment:
        """Start a deployment of ``git_ref`` for the bound project + environment.

        Args:
            git_ref: The git ref to deploy.

        Returns:
            The created deployment.
        """
        body: dict[str, object] = {"name": str(self._project), "target": self._target}
        if git_ref:
            body["gitSource"] = {"ref": git_ref}
        resp = await self._request(
            "POST", _DEPLOYMENTS_PATH, action="trigger a deployment", json=body
        )
        raise_for_deploy_status(resp, action="trigger a deployment")
        payload = self._json_or_raise(resp, action="trigger a deployment")
        deployment = _deployment_from(payload)
        logger.info(
            DEPLOY_API_DEPLOYMENT_TRIGGERED,
            deployment_id=deployment.id,
            state=deployment.state.value,
            target=self._target,
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
        return _deployment_from(self._json_or_raise(resp, action="read a deployment"))

    async def list_deployments(self, *, limit: int) -> tuple[Deployment, ...]:
        """List recent deployments for the bound project, newest first.

        Args:
            limit: Maximum number of deployments to return.

        Returns:
            The deployment records, scoped to the bound environment.

        Raises:
            DeployApiError: When the platform returns a non-list payload.
        """
        resp = await self._request(
            "GET",
            _LIST_PATH,
            action="list deployments",
            # Filtered by target, not just project: without it a
            # staging-bound client could enumerate production deployments
            # (ids, states, URLs), which would leak across the very
            # environment boundary the connection binding exists to hold.
            params={
                "app": str(self._project),
                "target": self._target,
                "limit": limit,
            },
        )
        raise_for_deploy_status(resp, action="list deployments")
        payload = self._json_or_raise(resp, action="list deployments")
        items = payload.get("deployments") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            logger.warning(
                DEPLOY_API_REQUEST_FAILED,
                action="list deployments",
                detail="response carried no deployment list",
            )
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

        Raises:
            DeployApiError: When the platform returns a non-list payload. A
                debugging tool must not silently report "no logs" when the
                fetch itself failed, so a malformed body fails loudly rather
                than returning an empty result.
        """
        path = _EVENTS_PATH.format(deployment_id=deployment_id)
        resp = await self._request(
            "GET", path, action="read deployment logs", params={"limit": limit}
        )
        raise_for_deploy_status(resp, action="read deployment logs")
        payload = self._json_or_raise(resp, action="read deployment logs")
        if not isinstance(payload, list):
            logger.warning(
                DEPLOY_API_REQUEST_FAILED,
                action="read deployment logs",
                detail="log payload was not a list",
            )
            msg = "deploy platform returned a malformed log payload"
            raise DeployApiError(msg)
        lines: list[DeployLogLine] = []
        for event in payload[:limit]:
            if not isinstance(event, dict):
                continue
            text = event.get("text") or event.get("payload")
            if isinstance(text, dict):
                text = text.get("text")
            if not isinstance(text, str) or not text.strip():
                # An empty / whitespace-only event carries no log content;
                # drop it rather than emit a blank line.
                continue
            created = event.get("created") or event.get("date")
            lines.append(
                DeployLogLine(
                    timestamp=str(created) if created is not None else "",
                    text=NotBlankStr(text),
                )
            )
        return tuple(lines)


__all__ = ["VercelDeployClient"]
