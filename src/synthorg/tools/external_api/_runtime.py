"""Boot-scoped collaborators for the governed external-access tool.

Built once at runtime-service construction and carried on the ``AgentEngine``.
The per-run registry augmentation pairs this bundle with the run's identity,
task, and effective autonomy to construct the tool. ``None`` when the feature
is disabled or no connection catalog is wired, in which case the tool is not
registered.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synthorg.integrations.connections.catalog import ConnectionCatalog
    from synthorg.tools.external_api.provider import ExternalAccessProvider
    from synthorg.tools.network_validator import NetworkPolicy


@dataclass(frozen=True)
class ExternalApiRuntime:
    """Boot-scoped dependencies the external-access tool needs per run."""

    connection_catalog: ConnectionCatalog
    provider: ExternalAccessProvider
    network_policy: NetworkPolicy
    max_response_bytes: int
    timeout_seconds: float
    default_max_rpm: int
