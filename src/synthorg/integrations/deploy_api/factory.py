"""Deploy-platform API client factory keyed on the platform preset.

Selects the per-platform client via a
:class:`~synthorg.core.registry.StrategyRegistry`, keyed on the deploy
connection's declared platform, so the agent-facing deploy tools stay
vendor-neutral. Adding a platform is a preset entry plus a client; the
tool layer, its approval gating and its tests do not change.

Unlike the chat factory there is no host allowlist per platform: a deploy
target's API host is genuinely operator-chosen (self-hosted control
planes are normal). The egress guarantee instead comes from pinning the
client to that one ``base_url`` and defining every path in code, so a
call can never leave the host the operator approved.
"""

from synthorg.core.registry import StrategyRegistry
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.deploy_target import (
    DeployEnvironment,
    DeployPlatform,
)
from synthorg.integrations.deploy_api.protocol import DeployApiClient
from synthorg.integrations.deploy_api.vercel import VercelDeployClient
from synthorg.integrations.errors import DeployApiError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import DEPLOY_API_CONFIG_INVALID

logger = get_logger(__name__)


def _require_base_url(base_url: str) -> str:
    """Reject an empty or non-HTTPS deploy base URL.

    Args:
        base_url: The connection's configured base URL.

    Returns:
        The validated base URL.

    Raises:
        DeployApiError: When it is blank or not HTTPS. Plain HTTP would
            put the platform token on the wire in clear text, so it is
            refused rather than downgraded.
    """
    if not base_url or not base_url.startswith("https://"):
        # Never log the value: a base_url can carry embedded credentials.
        logger.warning(DEPLOY_API_CONFIG_INVALID, reason="base_url_not_https")
        msg = "Deploy connection base_url must be an https:// URL"
        raise DeployApiError(msg)
    return base_url


def _build_vercel(
    base_url: str,
    token: str,
    timeout: float,
    project: NotBlankStr,
    environment: DeployEnvironment,
) -> DeployApiClient:
    return VercelDeployClient(
        api_base_url=_require_base_url(base_url),
        token=token,
        timeout=timeout,
        project=project,
        environment=environment,
    )


_REGISTRY: StrategyRegistry[DeployApiClient] = StrategyRegistry(
    {DeployPlatform.VERCEL: _build_vercel},
    kind="deploy_api_client",
)


def deploy_api_supported(platform: DeployPlatform) -> bool:
    """Return whether a deploy client is wired for the platform.

    Args:
        platform: The platform preset declared on the connection.

    Returns:
        ``True`` when a client is wired.
    """
    return platform in _REGISTRY


def build_deploy_api_client(
    *,
    platform: DeployPlatform,
    base_url: str,
    token: str,
    timeout: float,
    project: NotBlankStr,
    environment: DeployEnvironment,
) -> DeployApiClient:
    """Build the per-platform deploy API client.

    Args:
        platform: Selects the platform implementation.
        base_url: The connection's base URL; every request is pinned to it.
        token: Resolved platform token (header auth only, never logged).
        timeout: Per-request timeout in seconds.
        project: The operator-configured project the client is bound to.
        environment: The target environment; decides the vendor-side deploy
            target so a staging connection cannot emit a production release.

    Returns:
        A client satisfying :class:`DeployApiClient`.

    Raises:
        StrategyFactoryNotFoundError: ``platform`` has no wired client
            (check ``deploy_api_supported`` first).
        DeployApiError: The base URL is blank or not HTTPS.
    """
    return _REGISTRY.build(platform, base_url, token, timeout, project, environment)


__all__ = ["build_deploy_api_client", "deploy_api_supported"]
