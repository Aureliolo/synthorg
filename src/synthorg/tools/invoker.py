# module-kind: complex_service
"""Tool invoker -- validates and executes tool calls.

Bridges LLM ``ToolCall`` objects with concrete ``BaseTool.execute``
methods.  Recoverable errors are returned as ``ToolResult(is_error=True)``;
non-recoverable errors (``MemoryError``, ``RecursionError``) are logged and
re-raised.  ``BaseException`` subclasses (``KeyboardInterrupt``,
``SystemExit``, ``asyncio.CancelledError``) propagate uncaught.
"""

import asyncio
import copy
from collections.abc import Iterable
from contextlib import nullcontext
from datetime import UTC, datetime

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.approval.models import EscalationInfo
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import (
    SECURITY_INTERCEPTOR_ERROR,
    SECURITY_OUTPUT_SCAN_ERROR,
    SECURITY_POLICY_DECISION_DENY,
    SECURITY_POLICY_ENGINE_ERROR,
    SECURITY_POLICY_LOG_ONLY_DENY,
)
from synthorg.observability.events.tool import (
    TOOL_INVOKE_ALL_COMPLETE,
    TOOL_INVOKE_ALL_START,
    TOOL_INVOKE_EXECUTION_ERROR,
    TOOL_INVOKE_NON_RECOVERABLE,
    TOOL_INVOKE_NOT_FOUND,
    TOOL_INVOKE_START,
    TOOL_INVOKE_SUCCESS,
    TOOL_INVOKE_TOOL_ERROR,
    TOOL_PERMISSION_DENIED,
    TOOL_SECURITY_DENIED,
    TOOL_SECURITY_ESCALATED,
)
from synthorg.observability.tracing import tool_span
from synthorg.providers.models import ToolCall, ToolResult
from synthorg.security.models import SecurityContext, SecurityVerdictType
from synthorg.security.policy_engine.protocol import PolicyEngine
from synthorg.security.protocol import SecurityInterceptionStrategy
from synthorg.tools.html_parse_guard import HTMLParseGuard

from .base import BaseTool, ToolExecutionResult
from .errors import ToolExecutionError, ToolNotFoundError
from .invocation_bridge import record_tool_invocation
from .invocation_tracker import ToolInvocationTracker
from .invoker_discovery import ToolInvokerDiscoveryMixin
from .invoker_validation import ToolInvokerValidationMixin
from .permissions import ToolPermissionChecker
from .registry import ToolRegistry
from .scan_result_handler import handle_sensitive_scan

logger = get_logger(__name__)


class ToolInvoker(ToolInvokerDiscoveryMixin, ToolInvokerValidationMixin):
    """Validate parameters, enforce security policies, and execute tools.

    Recoverable errors are returned as ``ToolResult(is_error=True)``.
    Non-recoverable errors (``MemoryError``, ``RecursionError``) are
    re-raised after logging.

    Examples:
        Invoke a single tool call::

            invoker = ToolInvoker(registry)
            result = await invoker.invoke(tool_call)

        Invoke multiple tool calls concurrently::

            results = await invoker.invoke_all(tool_calls)

        Limit concurrency::

            results = await invoker.invoke_all(tool_calls, max_concurrency=3)
    """

    def __init__(  # noqa: PLR0913
        self,
        registry: ToolRegistry,
        *,
        permission_checker: ToolPermissionChecker | None = None,
        security_interceptor: SecurityInterceptionStrategy | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        agent_provider_name: str | None = None,
        invocation_tracker: ToolInvocationTracker | None = None,
        policy_engine: PolicyEngine | None = None,
        policy_evaluation_mode: str = "log_only",
    ) -> None:
        """Initialize with a tool registry and optional checkers.

        Args:
            registry: Registry to look up tools from.
            permission_checker: Optional checker for access-level gating.
                When ``None``, all registered tools are permitted.
            security_interceptor: Optional pre/post-tool security layer.
            agent_id: Agent ID for security context.
            task_id: Task ID for security context.
            agent_provider_name: Provider name the agent is using,
                for cross-family LLM security evaluation.
            invocation_tracker: Optional tracker for recording
                invocations for the activity timeline.
            policy_engine: Optional runtime policy engine evaluated before
                each tool call. ``None`` is a transparent pass-through.
            policy_evaluation_mode: ``"enforce"`` blocks denied tool calls;
                ``"log_only"`` logs the denial and proceeds.
        """
        self._registry = registry
        self._permission_checker = permission_checker
        self._security_interceptor = security_interceptor
        self._agent_id = agent_id
        self._task_id = task_id
        self._agent_provider_name = agent_provider_name
        self._invocation_tracker = invocation_tracker
        self._policy_engine = policy_engine
        self._policy_evaluation_mode = policy_evaluation_mode

        self._pending_escalations: list[EscalationInfo] = []
        self._html_guard: HTMLParseGuard | None = None

    @property
    def registry(self) -> ToolRegistry:
        """Read-only access to the underlying tool registry."""
        return self._registry

    @property
    def pending_escalations(self) -> tuple[EscalationInfo, ...]:
        """Escalations detected during the most recent invoke/invoke_all.

        Populated when a security ESCALATE verdict with a non-``None``
        ``approval_id`` is returned, or when a tool returns
        ``requires_parking`` metadata.  Cleared at the start of every
        ``invoke()`` and ``invoke_all()`` call.
        """
        return tuple(self._pending_escalations)

    def _check_permission(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolResult | None:
        """Check tool permission.

        Returns ``None`` if permitted, or a ``ToolResult(is_error=True)``
        if denied.

        Returns:
            The resulting ``ToolResult``, or ``None`` when unavailable.
        """
        if self._permission_checker is None:
            return None
        if self._permission_checker.is_permitted(tool.name, tool.category):
            return None
        reason = self._permission_checker.denial_reason(tool.name, tool.category)
        logger.warning(
            TOOL_PERMISSION_DENIED,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            reason=reason,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Permission denied: {reason}",
            is_error=True,
        )

    async def _check_policy(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolResult | None:
        """Evaluate the runtime policy engine before a tool call.

        Returns ``None`` to proceed (no engine wired, an allow, or a
        ``log_only`` deny), or a denying ``ToolResult`` when the engine
        denies in ``enforce`` mode. A request-construction / evaluation
        error fails open here (proceeds); ``CedarPolicyEngine`` applies its
        own configured fail-closed policy internally.

        Returns:
            A denying ``ToolResult`` in enforce mode, otherwise ``None``.
        """
        if self._policy_engine is None:
            return None
        from synthorg.security.policy_engine.models import (  # noqa: PLC0415
            PolicyActionRequest,
        )

        try:
            request = PolicyActionRequest(
                action_type="tool_invoke",
                principal=str(self._agent_id or "unknown"),
                resource=tool.name,
                context={"task_id": str(self._task_id or "unknown")},
            )
            decision = await self._policy_engine.evaluate(request)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.error(
                SECURITY_POLICY_ENGINE_ERROR,
                tool_name=tool_call.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        if decision.allow:
            return None
        if self._policy_evaluation_mode == "enforce":
            logger.warning(
                SECURITY_POLICY_DECISION_DENY,
                tool_name=tool_call.name,
                reason=decision.reason,
                mode="enforce",
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Policy denied: {decision.reason}",
                is_error=True,
            )
        logger.warning(
            SECURITY_POLICY_LOG_ONLY_DENY,
            tool_name=tool_call.name,
            reason=decision.reason,
        )
        return None

    def _check_sub_constraints(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolResult | None:
        """Check granular sub-constraints via the permission checker.

        Returns ``None`` if permitted, or a ``ToolResult`` if denied or
        if the action requires approval (escalation).

        Returns:
            The resulting ``ToolResult``, or ``None`` when unavailable.
        """
        if self._permission_checker is None:
            return None
        safe_args = self._safe_deepcopy_args(tool_call)
        if isinstance(safe_args, ToolResult):
            return safe_args
        violation = self._permission_checker.check_sub_constraints(
            tool.name,
            tool.category,
            tool.action_type,
            safe_args,
        )
        if violation is None:
            return None
        if violation.requires_approval:
            approval_id = f"sub-constraint-{tool_call.id}"
            self._pending_escalations.append(
                EscalationInfo(
                    approval_id=approval_id,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    action_type=tool.action_type,
                    risk_level=ApprovalRiskLevel.HIGH,
                    reason=violation.reason,
                ),
            )
            logger.warning(
                TOOL_SECURITY_ESCALATED,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                reason=violation.reason,
                approval_id=approval_id,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=(
                    f"Sub-constraint escalation: {violation.reason}. "
                    f"Human approval required (id={approval_id})"
                ),
                is_error=True,
            )
        logger.warning(
            TOOL_PERMISSION_DENIED,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            reason=violation.reason,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Sub-constraint denied: {violation.reason}",
            is_error=True,
        )

    def _build_security_context(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> SecurityContext:
        """Build a ``SecurityContext`` for the given tool call.

        Returns:
            Result of type ``SecurityContext``.
        """
        return SecurityContext(
            tool_name=tool.name,
            tool_category=tool.category,
            action_type=tool.action_type,
            arguments=copy.deepcopy(dict(tool_call.arguments)),
            agent_id=self._agent_id,
            task_id=self._task_id,
            agent_provider_name=self._agent_provider_name,
        )

    async def _check_security(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> tuple[SecurityContext | None, ToolResult | None]:
        """Run the security interceptor (if any) before execution.

        Builds the ``SecurityContext`` inside the fail-closed handler so
        construction errors are also caught.

        Returns ``(context, None)`` if allowed, or ``(context, ToolResult)``
        if denied/escalated.  Returns ``(None, None)`` when no interceptor.

        Returns:
            Tuple ``(SecurityContext | None, ToolResult | None)``.
        """
        if self._security_interceptor is None:
            return None, None
        try:
            context = self._build_security_context(tool, tool_call)
            verdict = await self._security_interceptor.evaluate_pre_tool(
                context,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SECURITY_INTERCEPTOR_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None, ToolResult(
                tool_call_id=tool_call.id,
                content=(
                    "Security evaluation failed (fail-closed). Tool execution blocked."
                ),
                is_error=True,
            )
        if verdict.verdict == SecurityVerdictType.ALLOW:
            return context, None
        if verdict.verdict == SecurityVerdictType.ESCALATE:
            logger.warning(
                TOOL_SECURITY_ESCALATED,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                reason=verdict.reason,
                approval_id=verdict.approval_id,
            )
            if verdict.approval_id is not None:
                self._pending_escalations.append(
                    EscalationInfo(
                        approval_id=verdict.approval_id,
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                        action_type=tool.action_type,
                        risk_level=verdict.risk_level,
                        reason=verdict.reason,
                    ),
                )
            agent_reason = verdict.agent_visible_reason or verdict.reason
            msg = (
                f"Security escalation: {agent_reason}. "
                f"Approval required (id={verdict.approval_id})"
            )
            return context, ToolResult(
                tool_call_id=tool_call.id,
                content=msg,
                is_error=True,
            )
        # DENY
        logger.warning(
            TOOL_SECURITY_DENIED,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            reason=verdict.reason,
        )
        deny_reason = verdict.agent_visible_reason or verdict.reason
        return context, ToolResult(
            tool_call_id=tool_call.id,
            content=f"Security denied: {deny_reason}",
            is_error=True,
        )

    async def _scan_output(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
        context: SecurityContext,
    ) -> ToolExecutionResult:
        """Scan tool output for sensitive data (if interceptor is set).

        When sensitive data is detected (``has_sensitive_data=True``),
        delegates to ``handle_sensitive_scan`` which branches on
        ``outcome`` (``WITHHELD`` vs ``REDACTED``).  When no sensitive
        data is detected (including ``LOG_ONLY`` and ``CLEAN``
        outcomes), the original output passes through unchanged.

        Scanner exceptions are caught and fail-closed -- a generic error
        result is returned to prevent leaking sensitive data.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        if self._security_interceptor is None:
            return result

        try:
            scan_result = await self._security_interceptor.scan_output(
                context,
                result.content,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SECURITY_OUTPUT_SCAN_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content="Output scan failed (fail-closed). Tool output withheld.",
                is_error=True,
                metadata={**result.metadata, "output_scan_failed": True},
            )

        if scan_result.has_sensitive_data:
            return handle_sensitive_scan(tool_call, result, scan_result)
        return result

    async def invoke(self, tool_call: ToolCall) -> ToolResult:
        """Execute a single tool call.

        Steps:
            1. Look up the tool in the registry.
            2. Check permissions against the permission checker (if any).
            3. Check sub-constraints (network, terminal, git, approval).
            4. Validate arguments against the tool's JSON Schema (if any).
            5. Run security interceptor pre-tool check (if any).
            6. Call ``tool.execute(arguments=...)``.
            7. Scan tool output for sensitive data (if interceptor is set).
            8. Return a ``ToolResult`` with the output.

        Recoverable errors produce ``ToolResult(is_error=True)``.
        Non-recoverable errors are re-raised.

        Args:
            tool_call: The tool call from the LLM.

        Returns:
            A ``ToolResult`` with the tool's output or error message.
        """
        self._pending_escalations.clear()
        return await self._invoke_single(tool_call)

    async def _invoke_single(self, tool_call: ToolCall) -> ToolResult:
        """Core invoke logic without clearing escalations.

        Used by both ``invoke`` (after clearing) and ``invoke_all``
        (which clears once at the batch level). Wraps the full
        invocation in a ``tool {name}`` OTel span so latency and
        outcome are queryable in the tracing UI.

        Returns:
            Result of type ``ToolResult``.

        Raises:
            CancelledError: If the related operation fails.
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            Exception: Raised when the relevant invariant fails.
        """
        logger.info(
            TOOL_INVOKE_START,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
        )
        async with tool_span(
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
        ) as span:
            try:
                result = await self._invoke_single_inner(tool_call)
            except asyncio.CancelledError:
                # Map generic cancellation to ``error`` (not
                # ``timeout``): only an explicit deadline expiry is
                # a real timeout, and that path stamps
                # ``metadata["timed_out"]`` which the happy-path
                # branch above promotes to
                # ``span.outcome="timeout"`` via ``result.is_timeout``.
                # Treating every cancellation as a timeout would
                # over-report the timeout outcome.
                span.set_attribute("tool.outcome", "error")
                raise
            except MemoryError, RecursionError:
                span.set_attribute("tool.outcome", "error")
                raise
            except Exception:
                span.set_attribute("tool.outcome", "error")
                raise
            if result.is_timeout:
                span.set_attribute("tool.outcome", "timeout")
            elif result.is_error:
                span.set_attribute("tool.outcome", "error")
            else:
                span.set_attribute("tool.outcome", "success")
            return result

    async def _invoke_single_inner(
        self,
        tool_call: ToolCall,
    ) -> ToolResult:
        """Inner body of ``_invoke_single`` -- guarded by the span.

        Every exit path -- happy, error, or deadline-driven
        cancellation -- routes through the same recording call so
        success / error / timeout outcomes all land in
        ``synthorg_tool_invocations_total`` and the activity DB. The
        ``CancelledError`` branch synthesizes a sentinel ``ToolResult``
        before re-raising so the engine deadline that cancelled this
        coroutine is captured as ``outcome="timeout"``.

        Returns:
            Result of type ``ToolResult``.

        Raises:
            CancelledError: If the related operation fails.
        """
        started_at = datetime.now(UTC)
        try:
            result = await self._build_invocation_result(tool_call)
        except asyncio.CancelledError:
            # Generic cancellation is recorded as ``outcome="error"``,
            # not ``"timeout"``: a cancellation reaches us for many
            # reasons (engine shutdown, parent-task cancel, request
            # abort) and only deadline expiry is a real timeout. The
            # explicit-deadline path sets ``metadata["timed_out"]``
            # before cancelling, which the happy-path branch
            # promotes via ``_build_result``; if that metadata never
            # landed (because the inner coroutine was cancelled
            # before it could mark itself), classifying the failure
            # as a timeout would over-report the timeout outcome.
            cancelled_result = ToolResult(
                tool_call_id=tool_call.id,
                content="Tool invocation cancelled before completion.",
                is_error=True,
                is_timeout=False,
            )
            await record_tool_invocation(
                self,
                tool_call,
                cancelled_result,
                started_at=started_at,
            )
            raise
        await record_tool_invocation(self, tool_call, result, started_at=started_at)
        return result

    async def _build_invocation_result(
        self,
        tool_call: ToolCall,
    ) -> ToolResult:
        """Run the lookup -> permission -> exec -> scan pipeline.

        Returns a ``ToolResult`` for every outcome (lookup miss,
        permission denial, sub-constraint violation, param error,
        security block, execution failure, parking error, success).
        Caller is responsible for recording metrics around this
        function so all exit paths are observable.

        Returns:
            Result of type ``ToolResult``.
        """
        tool_or_error = self._lookup_tool(tool_call)
        if isinstance(tool_or_error, ToolResult):
            return tool_or_error

        permission_error = self._check_permission(tool_or_error, tool_call)
        if permission_error is not None:
            return permission_error

        sub_constraint_error = self._check_sub_constraints(tool_or_error, tool_call)
        if sub_constraint_error is not None:
            return sub_constraint_error

        param_outcome = self._validate_params(tool_or_error, tool_call)
        if isinstance(param_outcome, ToolResult):
            return param_outcome
        # ``param_outcome`` is now ``dict[str, object]`` (validated
        # args-model dump) or ``None`` (legacy JSON-Schema path).  The
        # dict, when present, supersedes ``tool_call.arguments`` for
        # the rest of the pipeline so coercions/defaults reach the
        # tool body.
        validated_arguments: dict[str, object] | None = param_outcome

        # Build security context inside fail-closed handling.
        security_context, security_error = await self._check_security(
            tool_or_error,
            tool_call,
        )
        if security_error is not None:
            return security_error

        policy_error = await self._check_policy(tool_or_error, tool_call)
        if policy_error is not None:
            return policy_error

        exec_result = await self._execute_tool(
            tool_or_error,
            tool_call,
            validated_arguments=validated_arguments,
        )
        if isinstance(exec_result, ToolResult):
            return exec_result

        # Sanitize HTML in tool output to strip hidden injection vectors
        # (scripts, styles, display:none elements).  Runs before output
        # scanning so the scanner sees only visible content.
        exec_result = self._apply_html_guard(exec_result)

        # Detect parking metadata from tools like request_human_approval.
        # Returns an error ToolResult if tracking fails, preventing the
        # agent from silently bypassing the approval gate.
        parking_error = self._track_parking_metadata(
            exec_result,
            tool_or_error,
            tool_call,
        )
        if parking_error is not None:
            return parking_error

        if security_context is not None:
            exec_result = await self._scan_output(
                tool_call,
                exec_result,
                security_context,
            )

        return self._build_result(tool_call, exec_result)

    def _lookup_tool(self, tool_call: ToolCall) -> BaseTool | ToolResult:
        """Look up a tool in the registry, returning an error on miss.

        Returns:
            Result of type ``BaseTool | ToolResult``.
        """
        try:
            return self._registry.get(tool_call.name)
        except ToolNotFoundError as exc:
            logger.warning(
                TOOL_INVOKE_NOT_FOUND,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(exc),
                is_error=True,
            )

    async def _execute_tool(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
        *,
        validated_arguments: dict[str, object] | None = None,
    ) -> ToolExecutionResult | ToolResult:
        """Deep-copy arguments for isolation, then execute the tool.

        When ``validated_arguments`` is provided (set by ``_validate_params``
        for tools that declare an ``args_model``), it carries the
        normalized model dump including defaults, coercions, and
        ``AfterValidator`` results.  We still deep-copy before dispatch
        so nested ``dict``/``list`` fields aren't shared with subsequent
        invocations.  When ``None`` (legacy JSON-Schema path), fall
        back to deep-copying the raw ``tool_call.arguments``.

        Returns:
            Result of type ``ToolExecutionResult | ToolResult``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        if validated_arguments is not None:
            safe_args: dict[str, object] = copy.deepcopy(validated_arguments)
        else:
            deepcopied = self._safe_deepcopy_args(tool_call)
            if isinstance(deepcopied, ToolResult):
                return deepcopied
            safe_args = deepcopied
        try:
            return await tool.execute(
                arguments=safe_args,
            )
        except (MemoryError, RecursionError) as exc:
            logger.warning(
                TOOL_INVOKE_NON_RECOVERABLE,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Propagated error string lands in agent context; redact to
            # prevent credential leakage from third-party HTTP / driver
            # exceptions.
            redacted_error = safe_error_description(exc)
            logger.warning(
                TOOL_INVOKE_EXECUTION_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                error_type=type(exc).__name__,
                error=redacted_error,
            )
            exec_err = ToolExecutionError(
                redacted_error,
                context={"tool": tool_call.name},
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(exec_err),
                is_error=True,
            )

    def _track_parking_metadata(
        self,
        result: ToolExecutionResult,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolResult | None:
        """Detect ``requires_parking`` metadata and add to escalations.

        Tools like ``request_human_approval`` signal parking via
        ``ToolExecutionResult.metadata``.  Only tracks when both
        ``requires_parking=True`` and ``approval_id`` are present.

        Returns:
            ``None`` on success, or an error ``ToolResult`` if tracking
            fails -- ensures the agent does not silently bypass the
            approval gate.
        """
        if result.metadata.get("requires_parking") is not True:
            return None
        if not result.metadata.get("approval_id"):
            logger.error(
                TOOL_INVOKE_EXECUTION_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool.name,
                note="requires_parking=True but approval_id missing",
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=(
                    "Tool signalled requires_parking=True but did not "
                    "provide an approval_id -- cannot track escalation"
                ),
                is_error=True,
            )
        raw_risk = result.metadata.get("risk_level", "high")
        risk_value = raw_risk if isinstance(raw_risk, str) else "high"
        try:
            self._pending_escalations.append(
                EscalationInfo(
                    approval_id=str(result.metadata["approval_id"]),
                    tool_call_id=tool_call.id,
                    tool_name=tool.name,
                    action_type=str(
                        result.metadata.get("action_type", tool.action_type),
                    ),
                    risk_level=ApprovalRiskLevel(risk_value),
                    reason="Agent requested human approval",
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TOOL_INVOKE_EXECUTION_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool.name,
                note="Failed to track parking metadata",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Approval escalation tracking failed: {safe_error_description(exc)}",  # noqa: E501
                is_error=True,
            )
        return None

    def _build_result(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
    ) -> ToolResult:
        """Map a successful execution result to a ``ToolResult``.

        ``is_error`` is normalized BEFORE logging so a timed-out
        execution is not mis-logged as ``TOOL_INVOKE_SUCCESS``: the
        ``ToolResult`` validator enforces ``is_timeout => is_error``,
        so the returned result is always marked errored when the
        underlying execution flagged a timeout, even if the inner
        ``result.is_error`` was left at its default ``False``.

        Returns:
            Result of type ``ToolResult``.
        """
        # Strict identity check (``is True``) so a tool that
        # accidentally stamped a string like ``"false"`` or a 1
        # into ``metadata["timed_out"]`` does not get reclassified
        # as a timeout by ``bool(...)`` truthiness rules.
        is_timeout = result.metadata.get("timed_out") is True
        is_error = result.is_error or is_timeout
        if is_error:
            logger.warning(
                TOOL_INVOKE_TOOL_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                content=result.content,
            )
        else:
            logger.info(
                TOOL_INVOKE_SUCCESS,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=result.content,
            is_error=is_error,
            is_timeout=is_timeout,
        )

    def _apply_html_guard(
        self,
        result: ToolExecutionResult,
    ) -> ToolExecutionResult:
        """Apply HTML parse guard to sanitize tool output.

        Strips scripts, styles, hidden elements, and detects
        render-gap injection attacks.  Returns the original result
        unchanged if the output is not HTML or on parse errors.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        if result.is_error or not result.content:
            return result

        if self._html_guard is None:
            from synthorg.tools.html_parse_guard import (  # noqa: PLC0415
                HTMLParseGuard,
            )

            self._html_guard = HTMLParseGuard()

        sanitized = self._html_guard.sanitize(result.content)
        if sanitized.cleaned == result.content:
            return result
        return result.model_copy(
            update={
                "content": sanitized.cleaned,
                "metadata": {
                    **result.metadata,
                    "html_guard": {
                        "gap_detected": sanitized.gap_detected,
                        "gap_ratio": sanitized.gap_ratio,
                        "stripped_element_count": sanitized.stripped_element_count,
                    },
                },
            },
        )

    async def _run_guarded(
        self,
        index: int,
        tool_call: ToolCall,
        results: dict[int, ToolResult],
        fatal_errors: list[Exception],
        semaphore: asyncio.Semaphore | None,
    ) -> None:
        """Execute a single tool call, storing fatal errors instead of raising.

        This wrapper ensures that ``MemoryError`` / ``RecursionError`` do not
        cancel sibling tasks inside a ``TaskGroup``.  ``BaseException``
        subclasses (``KeyboardInterrupt``, ``CancelledError``) are not
        intercepted and will cancel the group normally.
        """
        try:
            ctx = semaphore if semaphore is not None else nullcontext()
            async with ctx:
                results[index] = await self._invoke_single(tool_call)
        except (MemoryError, RecursionError) as exc:
            fatal_errors.append(exc)

    @staticmethod
    def _raise_fatal_errors(fatal_errors: list[Exception]) -> None:
        """Re-raise collected fatal errors after all tasks complete.

        Raises:
            ExceptionGroup: Raised when the relevant invariant fails.
        """
        if not fatal_errors:
            return
        if len(fatal_errors) == 1:
            raise fatal_errors[0]
        msg = "multiple non-recoverable tool errors"
        raise ExceptionGroup(msg, fatal_errors)

    async def invoke_all(
        self,
        tool_calls: Iterable[ToolCall],
        *,
        max_concurrency: int | None = None,
    ) -> tuple[ToolResult, ...]:
        """Execute multiple tool calls concurrently.

        Args:
            tool_calls: Tool calls to execute.
            max_concurrency: Max concurrent invocations (``>= 1``).

        Returns:
            Tuple of results in the same order as the input.

        Raises:
            ValueError: If *max_concurrency* < 1.
            MemoryError: Re-raised if a single fatal error occurred.
            RecursionError: Re-raised if a single fatal error occurred.
            ExceptionGroup: If multiple fatal errors occurred.
        """
        self._pending_escalations.clear()

        if max_concurrency is not None and max_concurrency < 1:
            msg = f"max_concurrency must be >= 1, got {max_concurrency}"
            raise ValueError(msg)

        calls = list(tool_calls)
        if not calls:
            return ()

        logger.info(
            TOOL_INVOKE_ALL_START,
            count=len(calls),
            max_concurrency=max_concurrency,
        )

        results: dict[int, ToolResult] = {}
        fatal_errors: list[Exception] = []
        semaphore = (
            asyncio.Semaphore(max_concurrency) if max_concurrency is not None else None
        )

        async with asyncio.TaskGroup() as tg:
            for idx, call in enumerate(calls):
                _ = tg.create_task(
                    self._run_guarded(
                        idx,
                        call,
                        results,
                        fatal_errors,
                        semaphore,
                    ),
                )

        logger.info(
            TOOL_INVOKE_ALL_COMPLETE,
            count=len(calls),
            fatal_count=len(fatal_errors),
        )

        self._raise_fatal_errors(fatal_errors)

        # Sort escalations by tool-call index for deterministic ordering.
        if len(self._pending_escalations) > 1:
            call_id_order = {tc.id: idx for idx, tc in enumerate(calls)}
            self._pending_escalations.sort(
                key=lambda e: call_id_order.get(e.tool_call_id, len(calls)),
            )

        return tuple(results[i] for i in range(len(calls)))
