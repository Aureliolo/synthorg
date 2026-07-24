"""Governed publish tools: one destructive push, one read-only inspector.

The split is deliberate. Publishing an image replaces what a tag points at
and carries the full destructive treatment (guardrail triple, approval
parking, channel action type). Listing tags or reading a manifest observes
state rather than causing a change, so it stays a read: an agent that could
not cheaply inspect the tags it just published would be unable to verify a
push, which is worse for safety, not better.
"""

from typing import ClassVar, override

from pydantic import BaseModel

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.registry_target import (
    PublishMethod,
    resolve_default_method,
)
from synthorg.integrations.registry_api import RegistryApiClient
from synthorg.meta.mcp.handlers.common import require_admin_guardrails
from synthorg.observability import get_logger
from synthorg.observability.events.tool import PUBLISH_TOOL_PUSH_REQUESTED
from synthorg.tools._governed_connection_tool import json_result
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.publish._args import PublishInspectArgs, PublishPushArgs
from synthorg.tools.publish._base import _BasePublishTool
from synthorg.tools.publish._runtime import PublishToolDeps
from synthorg.tools.publish.errors import (
    PublishSourceError,
    PublishToolArgumentError,
)
from synthorg.tools.publish.strategies import (
    PublishRequest,
    build_publish_strategy,
    resolve_publish_method,
)

logger = get_logger(__name__)


class PublishPushTool(_BasePublishTool):
    """Publish an image to a tag on an allowlisted registry target.

    Destructive: a push overwrites what the tag points at. Every call carries
    the confirm + reason + actor guardrail and parks a human approval unless
    autonomy explicitly auto-approves this exact action type for this channel.
    """

    args_model: ClassVar[type[BaseModel] | None] = PublishPushArgs
    _DESTRUCTIVE: ClassVar[bool] = True

    def __init__(self, *, deps: PublishToolDeps, actor: AgentIdentity | None) -> None:
        super().__init__(
            name="publish_push",
            description=(
                "Publish an image to a tag on an allowlisted registry target. "
                "Overwrites the tag; requires confirm, a reason, and approval."
            ),
            args_model=PublishPushArgs,
            deps=deps,
        )
        self._actor = actor

    @override
    def _check_preconditions(self, args: BaseModel) -> None:
        """Enforce the confirm + reason + actor triple before the gate.

        Runs ahead of approval parking so an unconfirmed or unattributable
        push is refused outright, and the stated intent is recorded alongside
        the request the human will be asked to adjudicate.

        Args:
            args: The parsed push arguments.

        Raises:
            GuardrailViolationError: Confirm, reason, or actor missing.
            PublishToolArgumentError: Arguments were not the push shape.
        """
        reason, actor = require_admin_guardrails(
            args.model_dump(mode="json"), self._actor
        )
        if not isinstance(args, PublishPushArgs):
            msg = "publish_push received unexpected arguments"
            raise PublishToolArgumentError(msg)
        logger.info(
            PUBLISH_TOOL_PUSH_REQUESTED,
            connection=str(args.target),
            actor_id=str(actor.id),
            reason=reason,
        )

    @override
    async def _dispatch(
        self, client: RegistryApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Resolve the publish method and run its strategy.

        Args:
            client: The registry client, pinned to the target repository.
            args: The parsed push arguments.

        Returns:
            The publish outcome.

        Raises:
            PublishToolArgumentError: Arguments were not the push shape, or
                the method could not be resolved from the inputs.
        """
        if not isinstance(args, PublishPushArgs):
            msg = "publish_push received unexpected arguments"
            raise PublishToolArgumentError(msg)
        conn = self._resolved_connection
        if conn is None:  # pragma: no cover -- set by _resolve_connection first
            msg = "publish_push dispatched before its target was resolved"
            raise PublishToolArgumentError(msg)
        requested = PublishMethod(args.method)
        effective = (
            requested
            if requested is not PublishMethod.AUTO
            else resolve_default_method(dict(conn.metadata))
        )
        image_path = args.source_image_path.strip()
        method = resolve_publish_method(
            effective,
            has_digest=args.source_digest is not None,
            has_image_path=bool(image_path),
        )
        strategy = build_publish_strategy(method)
        request = PublishRequest(
            dest_tag=args.dest_tag,
            source_digest=args.source_digest,
            source_image_path=image_path,
            max_manifest_bytes=self._runtime.max_manifest_bytes,
            max_image_bytes=self._runtime.max_image_bytes,
            workspace_root=self._runtime.workspace_root,
        )
        outcome = await strategy.publish(client, request)
        return json_result(outcome.model_dump(mode="json"))


class PublishInspectTool(_BasePublishTool):
    """Inspect a registry target (read-only)."""

    args_model: ClassVar[type[BaseModel] | None] = PublishInspectArgs

    def __init__(self, *, deps: PublishToolDeps) -> None:
        super().__init__(
            name="publish_inspect",
            description=(
                "List the tags on an allowlisted registry target, or read a "
                "manifest's digest, media type and size by tag or digest."
            ),
            args_model=PublishInspectArgs,
            deps=deps,
        )

    @override
    async def _dispatch(
        self, client: RegistryApiClient, args: BaseModel
    ) -> ToolExecutionResult:
        """Map the read action onto a client call.

        Args:
            client: The registry client, pinned to the target repository.
            args: The parsed read arguments.

        Returns:
            The tag list, or the manifest's digest / media type / size.

        Raises:
            PublishToolArgumentError: Arguments were not the read shape.
            PublishSourceError: The manifest exceeds the manifest size cap.
        """
        if not isinstance(args, PublishInspectArgs):
            msg = "publish_inspect received unexpected arguments"
            raise PublishToolArgumentError(msg)
        if args.action == "get_manifest":
            reference = self._reference(args)
            manifest = await client.get_manifest(reference=reference)
            cap = self._runtime.max_manifest_bytes
            if manifest.size > cap:
                # The setting bounds what these tools read as well as publish,
                # so an oversized manifest is refused on the read path too.
                msg = f"manifest exceeds the configured manifest size cap ({cap} bytes)"
                raise PublishSourceError(msg)
            return json_result(
                {
                    "digest": str(manifest.digest),
                    "media_type": manifest.media_type,
                    "size": manifest.size,
                }
            )
        tags = await client.list_tags(limit=args.limit)
        return json_result(tags.model_dump(mode="json"))

    @staticmethod
    def _reference(args: PublishInspectArgs) -> NotBlankStr:
        """Return the reference the get_manifest action requires.

        The args validator already rejects a get_manifest call with no
        reference; this narrows the ``NotBlankStr | None`` field at the client
        boundary and fails loudly rather than silently if that invariant is
        ever bypassed.

        Raises:
            PublishToolArgumentError: The reference is absent.
        """
        if args.reference is None:
            msg = "reference is required for action 'get_manifest'"
            raise PublishToolArgumentError(msg)
        return args.reference


__all__ = ["PublishInspectTool", "PublishPushTool"]
