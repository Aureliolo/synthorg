"""LLM-backed tool blueprint authoring.

Given a recurring capability gap, the generator asks the configured
provider to author a sandbox tool addressing that capability. The tool
name is derived deterministically from the gap's ``domain:action``
capability (so it always aligns with the wire schema), while the model
supplies the description, JSON Schema, action type, and script body. The
sandbox backend and network policy come from config, never the model, so
an authored tool cannot widen its own isolation.
"""

import json
from collections.abc import Sequence
from typing import Final
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.core.boundary import parse_typed
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.meta.toolsmith.config import ToolsmithConfig
from synthorg.meta.toolsmith.errors import (
    ToolAuthoringError,
    ToolCapabilityNotAllowedError,
)
from synthorg.meta.toolsmith.models import (
    CapabilityGap,
    ToolBlueprint,
    ToolBlueprintState,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_AUTHOR_COMPLETED,
    TOOLSMITH_AUTHOR_FAILED,
    TOOLSMITH_AUTHOR_STARTED,
    TOOLSMITH_PROPOSAL_GUARD_REJECTED,
)
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.cost_recording import cost_recording_scope

logger = get_logger(__name__)

_SYSTEM_PROMPT: Final[str] = """\
You author sandboxed tools for SynthOrg, a framework for synthetic \
organisations. The organisation has repeatedly been unable to perform a \
capability; your job is to author ONE self-contained Python tool that \
provides it.

The tool runs in an isolated sandbox with NO network access. It MUST:
- Read its arguments from the SYNTHORG_TOOL_ARGS environment variable, a \
JSON object matching the parameters schema.
- Print exactly one JSON value to stdout (the tool result).
- Use only the Python standard library.
- Be deterministic and side-effect-free beyond its stdout.

Respond with ONE JSON object, no markdown fences or commentary:
{
  "description": "one-line description of what the tool does",
  "action_type": "category:action (e.g. code:read) classifying the tool",
  "parameters_schema": {
    "type": "object",
    "properties": {"<name>": {"type": "string|integer|number|boolean"}},
    "required": ["<name>", ...]
  },
  "script_body": "complete Python source as described above"
}
""" + untrusted_content_directive((TAG_TASK_DATA,))


class LLMToolBlueprintGenerator:
    """Authors a :class:`ToolBlueprint` from a capability gap via the LLM.

    Args:
        config: Toolsmith configuration (allowlist, sandbox policy, model).
        provider: Completion provider for the authoring call.
        cost_tracker: Optional cost tracker for the LLM call.
        clock: Time source for the blueprint's ``created_at``.
    """

    def __init__(
        self,
        *,
        config: ToolsmithConfig,
        provider: BaseCompletionProvider,
        cost_tracker: CostTracker | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._cost_tracker = cost_tracker
        self._clock = clock or SystemClock()

    async def author(
        self,
        gap: CapabilityGap,
        *,
        existing_capabilities: Sequence[NotBlankStr],
    ) -> ToolBlueprint:
        """Author a candidate blueprint addressing ``gap``.

        Raises:
            ToolCapabilityNotAllowedError: If the gap's capability is not
                in the configured allowlist.
            ToolAuthoringError: If the model output cannot be parsed into
                a valid blueprint.

        Returns:
            ``ToolBlueprint`` instance.
        """
        capability = gap.signature
        if capability not in self._config.allowed_capabilities:
            msg = f"capability {capability!r} is not in the toolsmith allowlist"
            logger.warning(
                TOOLSMITH_PROPOSAL_GUARD_REJECTED,
                capability=capability,
                reason="capability_not_allowlisted",
                error_type=ToolCapabilityNotAllowedError.__name__,
            )
            raise ToolCapabilityNotAllowedError(msg)
        logger.info(TOOLSMITH_AUTHOR_STARTED, capability=capability)
        try:
            response = await self._call_llm(gap, existing_capabilities)
            blueprint = self._parse(capability, response)
        except ToolAuthoringError, ToolCapabilityNotAllowedError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            # ProviderError lands here too: a transient completion failure
            # is a per-gap authoring failure, not a batch-wide abort. The
            # service runs gaps under a TaskGroup, so wrapping it as
            # ToolAuthoringError lets _handle_gap skip just this gap rather
            # than cancelling the sibling authoring tasks.
            logger.warning(
                TOOLSMITH_AUTHOR_FAILED,
                capability=capability,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"authoring failed for capability {capability!r}"
            raise ToolAuthoringError(msg) from exc
        logger.info(
            TOOLSMITH_AUTHOR_COMPLETED,
            capability=capability,
            tool_name=blueprint.name,
        )
        return blueprint

    async def _call_llm(
        self,
        gap: CapabilityGap,
        existing_capabilities: Sequence[NotBlankStr],
    ) -> str:
        """Call the provider to author the tool; returns raw content.

        Returns:
            Resulting string.

        Raises:
            ToolAuthoringError: Raised on the corresponding failure path.
        """
        from synthorg.providers.enums import MessageRole  # noqa: PLC0415
        from synthorg.providers.models import (  # noqa: PLC0415
            ChatMessage,
            CompletionConfig,
        )

        user_prompt = self._build_user_prompt(gap, existing_capabilities)
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=MessageRole.USER, content=user_prompt),
        ]
        config = CompletionConfig(
            temperature=self._config.authoring.temperature,
            max_tokens=self._config.authoring.max_tokens,
        )
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=NotBlankStr("system"),
            task_id=NotBlankStr("system:toolsmith:author"),
            call_category=LLMCallCategory.SYSTEM,
        ):
            response = await self._provider.complete(
                messages=messages,
                model=str(self._config.authoring.model),
                config=config,
            )
        content = response.content
        if not content:
            msg = "authoring model returned an empty response"
            logger.warning(
                TOOLSMITH_AUTHOR_FAILED,
                reason="empty_model_response",
                error_type=ToolAuthoringError.__name__,
            )
            raise ToolAuthoringError(msg)
        return content

    def _build_user_prompt(
        self,
        gap: CapabilityGap,
        existing_capabilities: Sequence[NotBlankStr],
    ) -> str:
        """Build the authoring prompt; gap context is wrapped as untrusted.

        Returns:
            Resulting string.
        """
        context = {
            "capability": gap.signature,
            "occurrences": gap.occurrences,
            "existing_capabilities": sorted(str(c) for c in existing_capabilities),
        }
        wrapped = wrap_untrusted(TAG_TASK_DATA, json.dumps(context, sort_keys=True))
        return (
            f"The organisation could not perform capability "
            f"{gap.signature!r} on {gap.occurrences} occasions. Author a "
            f"sandbox tool that provides it.\n\nContext:\n{wrapped}"
        )

    def _parse(self, capability: NotBlankStr, response: str) -> ToolBlueprint:
        """Parse the model response into a validated blueprint.

        The provider output is an external dict ingestion, so it routes
        through the frozen :class:`_AuthoredBlueprintArgs` boundary model
        via ``parse_typed`` rather than ad-hoc field plucking. The sandbox
        backend and network policy come from config, never the model.

        Returns:
            ``ToolBlueprint`` instance.

        Raises:
            ToolAuthoringError: Raised on the corresponding failure path.
        """
        text = response.strip()
        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as exc:
            msg = "authoring response is not valid JSON"
            logger.warning(
                TOOLSMITH_AUTHOR_FAILED,
                reason="response_not_valid_json",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ToolAuthoringError(msg) from exc
        if not isinstance(payload, dict):
            msg = "authoring response must be a JSON object"
            logger.warning(
                TOOLSMITH_AUTHOR_FAILED,
                reason="response_not_json_object",
                error_type=ToolAuthoringError.__name__,
            )
            raise ToolAuthoringError(msg)
        try:
            args = parse_typed("toolsmith.authoring", payload, _AuthoredBlueprintArgs)
        except ValidationError as exc:
            msg = f"authored blueprint for {capability!r} has invalid fields"
            raise ToolAuthoringError(msg) from exc
        domain, action = capability.split(":", 1)
        name = f"synthorg_{domain}_{action}"
        try:
            return ToolBlueprint(
                id=NotBlankStr(f"bp-{uuid4().hex}"),
                name=NotBlankStr(name),
                description=args.description,
                capability=capability,
                parameters_schema=args.parameters_schema,
                script_body=args.script_body,
                sandbox_backend=self._config.sandbox_backend,
                requires_network=self._config.requires_network,
                action_type=args.action_type,
                state=ToolBlueprintState.PENDING,
                created_at=self._clock.now(),
            )
        except ValueError as exc:
            msg = f"authored blueprint for {capability!r} is invalid: {exc}"
            raise ToolAuthoringError(msg) from exc


class _AuthoredBlueprintArgs(BaseModel):
    """Typed boundary for the model-authored blueprint fields.

    Only the fields the model is asked to supply; the sandbox backend,
    network policy, name, id, and timestamps are stamped by the generator
    from trusted config, never the model output.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    description: NotBlankStr
    action_type: NotBlankStr
    script_body: NotBlankStr
    parameters_schema: dict[str, JsonValue]


__all__ = ["LLMToolBlueprintGenerator"]
