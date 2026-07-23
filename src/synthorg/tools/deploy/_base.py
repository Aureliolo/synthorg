"""Deploy-specific bindings for the shared governed-connection tool base.

The connection-resolution / approval-gating / dispatch / error-mapping
pipeline lives in :mod:`synthorg.tools._governed_connection_tool`; this
module supplies the deploy-specific hooks plus the two things that make
this family different from forge and chat:

* the connection is resolved from the call's ``target``, checked against
  the operator's allowlist *before* any credential is brokered; and
* the approval action type is derived from the resolved connection's
  environment, so a release to production is gated as a production
  action even though the agent chose the target.
"""

from abc import ABC
from typing import ClassVar, Final, override

from pydantic import BaseModel

from synthorg.core.registry import StrategyFactoryNotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.deploy_target import (
    METADATA_KEY_PROJECT,
    DeployEnvironment,
    resolve_environment,
    resolve_platform,
)
from synthorg.integrations.connections.models import Connection, ConnectionType
from synthorg.integrations.deploy_api import (
    DeployApiClient,
    build_deploy_api_client,
    deploy_api_supported,
)
from synthorg.integrations.errors import (
    DeployApiAuthError,
    DeployApiError,
    DeployApiRateLimitError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import (
    DEPLOY_TOOL_CONNECTION_FAILED,
    DEPLOY_TOOL_CREDENTIAL_FAILED,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools._governed_connection_tool import GovernedConnectionTool
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.deploy._args import DeployReleaseArgs, DeployRunArgs
from synthorg.tools.deploy._runtime import DeployToolDeps, DeployToolsRuntime
from synthorg.tools.deploy.errors import (
    DeployConnectionNotFoundError,
    DeployCredentialError,
    DeployRateLimitedError,
    DeploySetupRequiredError,
    DeployTargetNotAllowedError,
    DeployToolArgumentError,
    DeployUnsupportedError,
    DeployUpstreamError,
)
from synthorg.tools.errors import ToolError

logger = get_logger(__name__)

_ENVIRONMENT_ACTION_TYPES: Final[dict[DeployEnvironment, str]] = {
    DeployEnvironment.STAGING: ActionType.DEPLOY_STAGING.value,
    DeployEnvironment.PRODUCTION: ActionType.DEPLOY_PRODUCTION.value,
}


class _BaseDeployTool(GovernedConnectionTool[DeployApiClient, DeployToolsRuntime], ABC):
    """Deploy bindings for the shared governed-connection tool pipeline."""

    _KIND: ClassVar[str] = "Deploy"
    _CONNECTION_FAILED_EVENT: ClassVar[str] = DEPLOY_TOOL_CONNECTION_FAILED
    _CREDENTIAL_FAILED_EVENT: ClassVar[str] = DEPLOY_TOOL_CREDENTIAL_FAILED
    _REQUIRE_BASE_URL: ClassVar[bool] = True
    _UNSUPPORTED_MSG: ClassVar[str] = "Deploy platform {ctype!r} has no wired client"
    _UNSUPPORTED_REASON: ClassVar[str] = "unsupported_platform"
    _not_found_error: ClassVar[type[ToolError]] = DeployConnectionNotFoundError
    _unsupported_error: ClassVar[type[ToolError]] = DeployUnsupportedError
    _argument_error: ClassVar[type[ToolError]] = DeployToolArgumentError
    _credential_error: ClassVar[type[ToolError]] = DeployCredentialError
    _rate_limited_error: ClassVar[type[ToolError]] = DeployRateLimitedError
    # The conservative declared type. The gate narrows to the resolved
    # target's true environment per call; declaring the stricter value
    # means the SecOps pre-tool screen never sees a deploy understated.
    _ACTION_TYPE: ClassVar[str] = ActionType.DEPLOY_PRODUCTION.value

    def __init__(
        self,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        deps: DeployToolDeps,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            args_model=args_model,
            runtime=deps.runtime,
            gate_deps=deps,
        )

    @override
    def _action_type_for(self, conn: Connection) -> str:
        """Resolve the action type from the target's declared environment.

        Args:
            conn: The resolved deploy connection.

        Returns:
            The staging or production action type. Read from the
            connection record, never from an argument, so an agent
            cannot route a production release through a staging grant.
        """
        environment = resolve_environment(dict(conn.metadata))
        # ``.get`` with the strictest default, not ``[]``: a future
        # ``DeployEnvironment`` member added without updating this map must
        # degrade to production gating (a raw KeyError would escape every
        # ``execute()`` except clause and crash the call), mirroring
        # ``resolve_environment``'s own fail-safe.
        return _ENVIRONMENT_ACTION_TYPES.get(
            environment, ActionType.DEPLOY_PRODUCTION.value
        )

    @override
    async def _resolve_connection(self, args: BaseModel) -> Connection:
        """Resolve the named target, enforcing the operator allowlist.

        Args:
            args: The parsed arguments carrying ``target``.

        Returns:
            The resolved deploy connection.

        Raises:
            DeployToolArgumentError: The arguments are not a deploy shape.
            DeployTargetNotAllowedError: The target is not allowlisted.
            DeployConnectionNotFoundError: No such connection.
            DeploySetupRequiredError: The connection exists but is not a
                usable deploy target yet.
        """
        # Narrowed rather than read via ``getattr(..., "")``: every deploy
        # args model declares a required ``target``, so a renamed or removed
        # field is a programming defect and must fail loudly here instead of
        # degrading into a governance denial that looks like a real refusal.
        if not isinstance(args, DeployReleaseArgs | DeployRunArgs):
            msg = "deploy tool received unexpected arguments"
            raise DeployToolArgumentError(msg)
        target = str(args.target)
        # Checked first, and before any credential is read: an agent
        # naming a target nobody approved must not cause a secret to be
        # brokered, let alone a request to be made.
        if target not in self._runtime.allowed_targets:
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=target,
                reason="target_not_allowlisted",
            )
            msg = (
                f"Deploy target {target!r} is not on the allowlist. An operator "
                "must add it to the deploy targets setting."
            )
            raise DeployTargetNotAllowedError(msg)
        conn = await self._catalog.get(target)
        if conn is None:
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=target,
                reason="connection_not_found",
            )
            msg = f"Deploy connection {target!r} not found"
            raise DeployConnectionNotFoundError(msg)
        self._require_ready(conn)
        return conn

    def _require_ready(self, conn: Connection) -> None:
        """Reject a target a human has not finished setting up.

        Args:
            conn: The resolved connection.

        Raises:
            DeploySetupRequiredError: When the connection is not a deploy
                target, has no base_url, or declares no usable platform
                or project. The message names what is missing so the
                agent can ask a person for exactly that.
        """
        # Delegate the type check to _supported so there is one source of
        # truth for "is this a deploy connection", shared with the base
        # pipeline's own gate rather than duplicated here.
        if not self._supported(conn.connection_type):
            msg = (
                f"Connection {conn.name!r} is not a deploy target "
                f"(it is a {conn.connection_type.value} connection)."
            )
            raise DeploySetupRequiredError(msg)
        metadata = dict(conn.metadata)
        missing: list[str] = []
        if not conn.base_url:
            missing.append("the platform API URL")
        if resolve_platform(metadata) is None:
            missing.append("a supported platform")
        if not metadata.get(METADATA_KEY_PROJECT, "").strip():
            missing.append("the project identifier")
        if missing:
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=conn.name,
                reason="setup_incomplete",
            )
            msg = (
                f"Deploy target {conn.name!r} needs setup: a person must supply "
                f"{', '.join(missing)} on the connection before it can be used."
            )
            raise DeploySetupRequiredError(msg)

    @override
    def _supported(self, connection_type: ConnectionType) -> bool:
        return connection_type is ConnectionType.DEPLOY

    @override
    def _build_client(
        self,
        *,
        conn: Connection,
        token: str,
        timeout: float,
    ) -> DeployApiClient:
        """Build the per-call client pinned to the target's base URL.

        Args:
            conn: The resolved deploy connection.
            token: The brokered platform token.
            timeout: Per-request timeout in seconds.

        Returns:
            The platform client.

        Raises:
            DeployUnsupportedError: The platform has no wired client.
            DeployToolArgumentError: The base URL is unusable.
        """
        metadata = dict(conn.metadata)
        platform = resolve_platform(metadata)
        if platform is None or not deploy_api_supported(platform):
            # Reachable when a DeployPlatform member is added without a wired
            # client (an interim state during incremental rollout), so log it
            # like every other rejection in this family before raising.
            logger.warning(
                self._CONNECTION_FAILED_EVENT,
                connection=conn.name,
                reason="unsupported_platform",
            )
            raise DeployUnsupportedError(self._UNSUPPORTED_MSG.format(ctype=platform))
        # ``_resolve_connection`` already refused a target without one, so
        # the project is guaranteed present by the time a client is built.
        project = metadata[METADATA_KEY_PROJECT].strip()
        try:
            return build_deploy_api_client(
                platform=platform,
                base_url=str(conn.base_url or ""),
                token=token,
                timeout=timeout,
                project=NotBlankStr(project),
                environment=resolve_environment(metadata),
            )
        except StrategyFactoryNotFoundError as exc:
            raise DeployUnsupportedError(safe_error_description(exc)) from exc
        except DeployApiError as exc:
            raise DeployToolArgumentError(safe_error_description(exc)) from exc

    @override
    async def _dispatch_guarded(
        self, client: DeployApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Dispatch, mapping platform-client errors onto the typed leaves.

        Args:
            client: The platform client.
            args: The parsed arguments.

        Returns:
            The tool result.

        Raises:
            DeployRateLimitedError: The platform rate-limited the call.
            DeployUpstreamError: Any other platform failure.
        """
        try:
            return await self._dispatch(client, args)
        except DeployApiRateLimitError as exc:
            raise DeployRateLimitedError(
                safe_error_description(exc),
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc
        except DeployApiAuthError as exc:
            raise DeployUpstreamError(safe_error_description(exc)) from exc
        except DeployApiError as exc:
            raise DeployUpstreamError(safe_error_description(exc)) from exc
