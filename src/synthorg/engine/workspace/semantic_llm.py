"""LLM-based semantic conflict analyzer.

Uses an LLM provider to review merged code for semantic conflicts
that AST analysis cannot detect. Intended for use when conflict
escalation is REVIEW_AGENT and a provider is configured. The gating
logic resides in the workspace strategy layer.
"""

import asyncio
from collections.abc import Mapping  # noqa: TC003
from typing import Final

from synthorg.budget.call_category import LLMCallCategory

# These types appear in ``LlmSemanticAnalyzer.__init__`` and ``analyze``
# annotations and must resolve at runtime when downstream tooling
# evaluates type hints (DI containers, doc generators).
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.config import SemanticAnalysisConfig  # noqa: TC001
from synthorg.engine.workspace.models import (  # noqa: TC001
    MergeConflict,
    Workspace,
)
from synthorg.engine.workspace.semantic_analyzer import filter_files
from synthorg.engine.workspace.semantic_llm_prompt import (
    build_review_message,
    build_semantic_review_tool,
    build_system_message,
    parse_tool_call_response,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import (
    WORKSPACE_SEMANTIC_ANALYSIS_COMPLETE,
    WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
    WORKSPACE_SEMANTIC_ANALYSIS_START,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider  # noqa: TC001

logger = get_logger(__name__)
_DEFAULT_MAX_RETRIES: Final[int] = 2


class LlmSemanticAnalyzer:
    """LLM-based semantic conflict analyzer.

    Sends merged code diff to an LLM for semantic review. Parses
    the response via tool calls (preferred) or JSON content fallback.
    Retries on parse failure up to ``max_retries`` times.

    Args:
        provider: LLM completion provider.
        model: Model identifier for semantic review.
        config: Optional semantic analysis configuration.
    """

    __slots__ = ("_config", "_cost_tracker", "_model", "_provider")

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: str,
        config: SemanticAnalysisConfig | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        if not model or not model.strip():
            msg = "model must be a non-blank string"
            raise ValueError(msg)
        self._provider = provider
        self._model = model
        self._cost_tracker = cost_tracker

        if config is None:
            from synthorg.engine.workspace.config import (  # noqa: PLC0415
                SemanticAnalysisConfig,
            )

            config = SemanticAnalysisConfig()
        self._config = config

    async def analyze(
        self,
        *,
        workspace: Workspace,
        changed_files: tuple[str, ...],
        base_sources: Mapping[str, str],
        merged_sources: Mapping[str, str],
    ) -> tuple[MergeConflict, ...]:
        """Analyze merged files using an LLM for semantic conflicts.

        Args:
            workspace: The workspace that was just merged.
            changed_files: Paths of files modified by the merge.
            base_sources: Mapping of file path to source content
                before the merge.
            merged_sources: Mapping of file path to source content
                after the merge.

        Returns:
            Tuple of semantic MergeConflict instances from LLM review.
        """
        logger.info(
            WORKSPACE_SEMANTIC_ANALYSIS_START,
            workspace_id=workspace.workspace_id,
            analyzer="llm",
            file_count=len(changed_files),
        )

        result = self._prepare_review_context(
            changed_files,
            merged_sources,
            base_sources,
        )
        if result is None:
            return ()
        messages, tool_def, comp_config = result

        return await self._call_with_retry(
            workspace=workspace,
            messages=messages,
            tool_def=tool_def,
            comp_config=comp_config,
            max_retries=self._config.llm_max_retries,
        )

    def _prepare_review_context(
        self,
        changed_files: tuple[str, ...],
        merged_sources: Mapping[str, str],
        base_sources: Mapping[str, str],
    ) -> tuple[list[ChatMessage], ToolDefinition, CompletionConfig] | None:
        """Filter files, apply size limits, and build LLM messages.

        Returns:
            Tuple of (messages, tool_def, comp_config) or ``None``
            when there is nothing to review.
        """
        ordered = filter_files(changed_files, self._config)
        if not ordered:
            return None

        max_bytes = self._config.max_file_bytes
        merged_contents: dict[str, str] = {}
        for name in ordered:
            content = merged_sources.get(name)
            if content is not None and len(content.encode("utf-8")) <= max_bytes:
                merged_contents[name] = content
        if not merged_contents:
            return None

        diff_summary = _build_diff_summary(
            merged_contents,
            base_sources,
        )
        messages = [
            build_system_message(),
            build_review_message(
                diff_summary=diff_summary,
                changed_files=merged_contents,
            ),
        ]
        tool_def = build_semantic_review_tool()
        comp_config = CompletionConfig(
            temperature=self._config.llm_temperature,
            top_p=self._config.llm_top_p,
            max_tokens=self._config.llm_max_tokens,
        )
        return messages, tool_def, comp_config

    async def _call_with_retry(
        self,
        *,
        workspace: Workspace,
        messages: list[ChatMessage],
        tool_def: ToolDefinition,
        comp_config: CompletionConfig,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> tuple[MergeConflict, ...]:
        """Call LLM with retry on parse failure.

        Args:
            workspace: Workspace for logging context.
            messages: Chat messages to send.
            tool_def: Tool definition for structured output.
            comp_config: Completion configuration.
            max_retries: Maximum retry attempts on parse failure.

        Returns:
            Parsed conflicts, or empty tuple on exhaustion/error.
        """
        for attempt in range(1 + max_retries):
            result = await self._attempt_once(
                workspace=workspace,
                messages=messages,
                tool_def=tool_def,
                comp_config=comp_config,
                attempt=attempt,
                max_retries=max_retries,
            )
            if result is not None:
                return result
        return ()  # pragma: no cover

    async def _attempt_once(  # noqa: PLR0913
        self,
        *,
        workspace: Workspace,
        messages: list[ChatMessage],
        tool_def: ToolDefinition,
        comp_config: CompletionConfig,
        attempt: int,
        max_retries: int,
    ) -> tuple[MergeConflict, ...] | None:
        """Execute a single LLM call attempt.

        Returns:
            Parsed conflicts on success, empty tuple on terminal
            failure, or ``None`` to signal a retry.
        """
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=NotBlankStr("system"),
                task_id=NotBlankStr(f"system:workspace:{workspace.workspace_id}"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._model,
                    tools=[tool_def],
                    config=comp_config,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            # Drop exc_info + scrub the message -- provider
            # HTTPStatusError can carry the API key in str(exc), and
            # the traceback would re-emit it from frame-locals.
            logger.warning(
                WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
                workspace_id=workspace.workspace_id,
                analyzer="llm",
                reason="provider_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

        try:
            conflicts = parse_tool_call_response(response)
        except ValueError as exc:
            return self._handle_parse_error(
                workspace=workspace,
                attempt=attempt,
                max_retries=max_retries,
                error=exc,
            )
        else:
            logger.info(
                WORKSPACE_SEMANTIC_ANALYSIS_COMPLETE,
                workspace_id=workspace.workspace_id,
                analyzer="llm",
                conflicts=len(conflicts),
                attempt=attempt,
            )
            return conflicts

    @staticmethod
    def _handle_parse_error(
        *,
        workspace: Workspace,
        attempt: int,
        max_retries: int,
        error: ValueError,
    ) -> tuple[MergeConflict, ...] | None:
        """Handle a parse error from ``parse_tool_call_response``.

        Returns:
            ``None`` to signal a retry, or ``()`` on exhaustion.
        """
        if attempt < max_retries:
            logger.debug(
                WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
                workspace_id=workspace.workspace_id,
                analyzer="llm",
                attempt=attempt,
                reason="parse_error",
                error_type=type(error).__name__,
                error=safe_error_description(error),
            )
            return None
        logger.warning(
            WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
            workspace_id=workspace.workspace_id,
            analyzer="llm",
            reason="parse_exhausted",
            error_type=type(error).__name__,
            error=safe_error_description(error),
        )
        return ()


def _build_diff_summary(
    merged_contents: dict[str, str],
    base_sources: Mapping[str, str],
) -> str:
    """Build a change-type summary (NEW FILE / MODIFIED) for each file.

    Returns:
        A newline-joined summary annotating each changed file as
        ``NEW FILE`` or ``MODIFIED``, or ``"No changes"`` when
        nothing differs.
    """
    parts: list[str] = []
    for path, merged in merged_contents.items():
        base = base_sources.get(path)
        if base is None:
            parts.append(f"NEW FILE: {path}")
        elif base != merged:
            parts.append(f"MODIFIED: {path}")
    return "\n".join(parts) if parts else "No changes"
