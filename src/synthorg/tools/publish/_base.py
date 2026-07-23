"""Publish-specific bindings for the shared governed-connection tool base.

The connection-resolution / approval-gating / dispatch / error-mapping
pipeline lives in :mod:`synthorg.tools._governed_connection_tool`; this module
supplies the publish-specific hooks plus the two things that make this family
different from forge and chat:

* the connection is resolved from the call's ``target``, checked against the
  operator's allowlist *before* any credential is brokered; and
* the approval action type is derived from the resolved connection's channel,
  so a push to a production registry is gated as a production action even
  though the agent chose the target.
"""

from abc import ABC
from typing import ClassVar, Final, override

from pydantic import BaseModel

from synthorg.core.registry import StrategyFactoryNotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import Connection, ConnectionType
from synthorg.integrations.connections.registry_target import (
    RegistryChannel,
    resolve_auth_host,
    resolve_channel,
    resolve_provider,
    resolve_repository,
    resolve_username,
)
from synthorg.integrations.errors import (
    RegistryApiAuthError,
    RegistryApiError,
    RegistryApiRateLimitError,
)
from synthorg.integrations.registry_api import (
    RegistryApiClient,
    build_registry_api_client,
    registry_api_supported,
    valid_repository,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import (
    PUBLISH_TOOL_CONNECTION_FAILED,
    PUBLISH_TOOL_CREDENTIAL_FAILED,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools._governed_connection_tool import GovernedConnectionTool
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.errors import ToolError
from synthorg.tools.publish._args import PublishInspectArgs, PublishPushArgs
from synthorg.tools.publish._runtime import PublishToolDeps, PublishToolsRuntime
from synthorg.tools.publish.errors import (
    PublishConnectionNotFoundError,
    PublishCredentialError,
    PublishRateLimitedError,
    PublishSetupRequiredError,
    PublishTargetNotAllowedError,
    PublishToolArgumentError,
    PublishUnsupportedError,
    PublishUpstreamError,
)

logger = get_logger(__name__)

_CHANNEL_ACTION_TYPES: Final[dict[RegistryChannel, str]] = {
    RegistryChannel.STAGING: ActionType.PUBLISH_STAGING.value,
    RegistryChannel.PRODUCTION: ActionType.PUBLISH_PRODUCTION.value,
}


class _BasePublishTool(
    GovernedConnectionTool[RegistryApiClient, PublishToolsRuntime], ABC
):
    """Publish bindings for the shared governed-connection tool pipeline."""

    _KIND: ClassVar[str] = "Publish"
    _CONNECTION_FAILED_EVENT: ClassVar[str] = PUBLISH_TOOL_CONNECTION_FAILED
    _CREDENTIAL_FAILED_EVENT: ClassVar[str] = PUBLISH_TOOL_CREDENTIAL_FAILED
    _REQUIRE_BASE_URL: ClassVar[bool] = True
    _UNSUPPORTED_MSG: ClassVar[str] = "Registry provider {ctype!r} has no wired client"
    _UNSUPPORTED_REASON: ClassVar[str] = "unsupported_provider"
    _not_found_error: ClassVar[type[ToolError]] = PublishConnectionNotFoundError
    _unsupported_error: ClassVar[type[ToolError]] = PublishUnsupportedError
    _argument_error: ClassVar[type[ToolError]] = PublishToolArgumentError
    _credential_error: ClassVar[type[ToolError]] = PublishCredentialError
    _rate_limited_error: ClassVar[type[ToolError]] = PublishRateLimitedError
    # The conservative declared type. The gate narrows to the resolved
    # target's true channel per call; declaring the stricter value means the
    # SecOps pre-tool screen never sees a push understated.
    _ACTION_TYPE: ClassVar[str] = ActionType.PUBLISH_PRODUCTION.value

    def __init__(
        self,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        deps: PublishToolDeps,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            args_model=args_model,
            runtime=deps.runtime,
            gate_deps=deps,
        )
        # Captured on resolve so the write tool can read the target's default
        # publish method from the record. The instance is single-use (built
        # per credentialed-MCP call), so this holds no cross-call state.
        self._resolved_connection: Connection | None = None

    @override
    def _action_type_for(self, conn: Connection) -> str:
        """Resolve the action type from the target's declared channel.

        Args:
            conn: The resolved registry connection.

        Returns:
            The staging or production action type. Read from the connection
            record, never from an argument, so an agent cannot route a
            production push through a staging grant.
        """
        channel = resolve_channel(dict(conn.metadata))
        return _CHANNEL_ACTION_TYPES.get(channel, ActionType.PUBLISH_PRODUCTION.value)

    @override
    async def _resolve_connection(self, args: BaseModel) -> Connection:
        """Resolve the named target, enforcing the operator allowlist.

        Args:
            args: The parsed arguments carrying ``target``.

        Returns:
            The resolved registry connection.

        Raises:
            PublishToolArgumentError: The arguments are not a publish shape.
            PublishTargetNotAllowedError: The target is not allowlisted.
            PublishConnectionNotFoundError: No such connection.
            PublishSetupRequiredError: The connection exists but is not a
                usable registry target yet.
        """
        if not isinstance(args, PublishInspectArgs | PublishPushArgs):
            msg = "publish tool received unexpected arguments"
            raise PublishToolArgumentError(msg)
        target = str(args.target)
        # Checked first, and before any credential is read: an agent naming a
        # target nobody approved must not cause a secret to be brokered.
        if target not in self._runtime.allowed_targets:
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=target,
                reason="target_not_allowlisted",
            )
            msg = (
                f"Publish target {target!r} is not on the allowlist. An operator "
                "must add it to the publish targets setting."
            )
            raise PublishTargetNotAllowedError(msg)
        conn = await self._catalog.get(target)
        if conn is None:
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=target,
                reason="connection_not_found",
            )
            msg = f"Publish connection {target!r} not found"
            raise PublishConnectionNotFoundError(msg)
        self._require_ready(conn)
        self._resolved_connection = conn
        return conn

    def _require_ready(self, conn: Connection) -> None:
        """Reject a target a human has not finished setting up.

        Args:
            conn: The resolved connection.

        Raises:
            PublishSetupRequiredError: When the connection is not a registry
                target, has no base_url, or declares no usable provider or
                repository. The message names what is missing so the agent can
                ask a person for exactly that.
        """
        if not self._supported(conn.connection_type):
            msg = (
                f"Connection {conn.name!r} is not a registry target "
                f"(it is a {conn.connection_type.value} connection)."
            )
            raise PublishSetupRequiredError(msg)
        metadata = dict(conn.metadata)
        missing: list[str] = []
        if not conn.base_url:
            missing.append("the registry API URL")
        if resolve_provider(metadata) is None:
            missing.append("a supported provider")
        repository = resolve_repository(metadata)
        if not repository:
            missing.append("the repository")
        elif not valid_repository(repository):
            missing.append("a valid repository path")
        if missing:
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=conn.name,
                reason="setup_incomplete",
            )
            msg = (
                f"Publish target {conn.name!r} needs setup: a person must supply "
                f"{', '.join(missing)} on the connection before it can be used."
            )
            raise PublishSetupRequiredError(msg)

    @override
    def _supported(self, connection_type: ConnectionType) -> bool:
        return connection_type is ConnectionType.REGISTRY

    @override
    def _build_client(
        self,
        *,
        conn: Connection,
        token: str,
        timeout: float,
    ) -> RegistryApiClient:
        """Build the per-call client pinned to the target's base URL.

        Args:
            conn: The resolved registry connection.
            token: The brokered registry credential.
            timeout: Per-request timeout in seconds.

        Returns:
            The registry client.

        Raises:
            PublishUnsupportedError: The provider has no wired client.
            PublishToolArgumentError: The base URL is unusable.
        """
        metadata = dict(conn.metadata)
        provider = resolve_provider(metadata)
        if provider is None or not registry_api_supported(provider):
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=conn.name,
                reason="unsupported_provider",
            )
            raise PublishUnsupportedError(self._UNSUPPORTED_MSG.format(ctype=provider))
        # ``_require_ready`` already refused a target without a valid
        # repository, so it is guaranteed present and well-formed here.
        repository = resolve_repository(metadata)
        try:
            return build_registry_api_client(
                provider=provider,
                base_url=str(conn.base_url or ""),
                repository=NotBlankStr(repository),
                username=resolve_username(metadata),
                token=token,
                timeout=timeout,
                auth_host=resolve_auth_host(metadata),
            )
        except StrategyFactoryNotFoundError as exc:
            raise PublishUnsupportedError(safe_error_description(exc)) from exc
        except RegistryApiError as exc:
            raise PublishToolArgumentError(safe_error_description(exc)) from exc

    @override
    async def _dispatch_guarded(
        self, client: RegistryApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Dispatch, mapping registry-client errors onto the typed leaves.

        Args:
            client: The registry client.
            args: The parsed arguments.

        Returns:
            The tool result.

        Raises:
            PublishRateLimitedError: The registry rate-limited the call.
            PublishUpstreamError: Any other registry failure.
        """
        try:
            return await self._dispatch(client, args)
        except RegistryApiRateLimitError as exc:
            raise PublishRateLimitedError(
                safe_error_description(exc),
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc
        except RegistryApiAuthError as exc:
            raise PublishUpstreamError(safe_error_description(exc)) from exc
        except RegistryApiError as exc:
            raise PublishUpstreamError(safe_error_description(exc)) from exc
