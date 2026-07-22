"""Deploy-platform API clients behind one vendor-neutral protocol."""

from synthorg.integrations.deploy_api.factory import (
    build_deploy_api_client,
    deploy_api_supported,
)
from synthorg.integrations.deploy_api.protocol import (
    DeployApiClient,
    DeployLogLine,
    Deployment,
    DeployState,
)

__all__ = [
    "DeployApiClient",
    "DeployLogLine",
    "DeployState",
    "Deployment",
    "build_deploy_api_client",
    "deploy_api_supported",
]
