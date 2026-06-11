"""Structural contract for typed config resolution.

The dispatcher, kill switch, and lifecycle wiring depend on the resolver's
scalar accessors and per-namespace bridge-config reads, not on the concrete
:class:`~synthorg.settings.resolver.ConfigResolver` (which welds in
``SettingsService`` and the full ``RootConfig``). Annotating against this
``@runtime_checkable`` Protocol lets them hold the resolver structurally.

The surface is deliberately the runtime-resolvable subset: scalar getters plus
the ``*BridgeConfig`` reads, whose return types resolve at module level. The
composed-config getters (``get_budget_config`` / ``get_api_config`` /
``get_agents`` / ...) are excluded on purpose: their return types are genuine
import-cycle breakers the resolver itself guards under ``TYPE_CHECKING``, and
their callers hold the concrete resolver at runtime-wired sites. Naming those
return types here would reintroduce the circular import their callers avoid.
"""

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.settings.bridge_configs import (
    A2ABridgeConfig,
    ApiBridgeConfig,
    ClientBridgeConfig,
    CommunicationBridgeConfig,
    CoordinationBridgeConfig,
    EngineBridgeConfig,
    IntegrationsBridgeConfig,
    MemoryBridgeConfig,
    MetaBridgeConfig,
    NotificationsBridgeConfig,
    ObservabilityBridgeConfig,
    SettingsDispatcherBridgeConfig,
    ToolsBridgeConfig,
    WorkersBridgeConfig,
)


@runtime_checkable
class ConfigResolverProtocol(Protocol):
    """The runtime-resolvable resolver surface: scalars and bridge reads."""

    async def get_str(self, namespace: str, key: str) -> str:
        """Resolve a setting as a string."""
        ...

    async def get_int(self, namespace: str, key: str) -> int:
        """Resolve a setting as an int."""
        ...

    async def get_float(self, namespace: str, key: str) -> float:
        """Resolve a setting as a float."""
        ...

    async def get_bool(self, namespace: str, key: str) -> bool:
        """Resolve a setting as a bool."""
        ...

    async def get_enum[E: StrEnum](
        self,
        namespace: str,
        key: str,
        enum_cls: type[E],
    ) -> E:
        """Resolve a setting as a member of *enum_cls*."""
        ...

    async def get_autonomy_level(self) -> AutonomyLevel:
        """Resolve the company-wide autonomy level."""
        ...

    async def get_json(  # type: ignore[explicit-any]  # parsed JSON feeds pydantic validation
        self,
        namespace: str,
        key: str,
    ) -> Any:
        """Resolve a setting as parsed JSON."""
        ...

    async def get_api_bridge_config(self) -> ApiBridgeConfig:
        """Assemble the API bridge config from bridged settings."""
        ...

    async def get_coordination_bridge_config(self) -> CoordinationBridgeConfig:
        """Assemble the coordination bridge config from bridged settings."""
        ...

    async def get_workers_bridge_config(self) -> WorkersBridgeConfig:
        """Assemble the workers bridge config from bridged settings."""
        ...

    async def get_communication_bridge_config(self) -> CommunicationBridgeConfig:
        """Assemble the communication bridge config from bridged settings."""
        ...

    async def get_a2a_bridge_config(self) -> A2ABridgeConfig:
        """Assemble the A2A bridge config from bridged settings."""
        ...

    async def get_engine_bridge_config(self) -> EngineBridgeConfig:
        """Assemble the engine bridge config from bridged settings."""
        ...

    async def get_client_bridge_config(self) -> ClientBridgeConfig:
        """Assemble the client bridge config from bridged settings."""
        ...

    async def get_memory_bridge_config(self) -> MemoryBridgeConfig:
        """Assemble the memory bridge config from bridged settings."""
        ...

    async def get_integrations_bridge_config(self) -> IntegrationsBridgeConfig:
        """Assemble the integrations bridge config from bridged settings."""
        ...

    async def get_meta_bridge_config(self) -> MetaBridgeConfig:
        """Assemble the meta bridge config from bridged settings."""
        ...

    async def get_notifications_bridge_config(self) -> NotificationsBridgeConfig:
        """Assemble the notifications bridge config from bridged settings."""
        ...

    async def get_tools_bridge_config(self) -> ToolsBridgeConfig:
        """Assemble the tools bridge config from bridged settings."""
        ...

    async def get_observability_bridge_config(self) -> ObservabilityBridgeConfig:
        """Assemble the observability bridge config from bridged settings."""
        ...

    async def get_settings_dispatcher_bridge_config(
        self,
    ) -> SettingsDispatcherBridgeConfig:
        """Assemble the settings-dispatcher bridge config from bridged settings."""
        ...
