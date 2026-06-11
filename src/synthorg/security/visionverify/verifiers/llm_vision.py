"""Multimodal LLM vision verifier.

Sends the captured screenshots (as ``image_parts``) plus the fenced
brief to a vision-capable model and parses a structured verdict from a
single tool call. Gated on ``ModelCapabilities.supports_vision`` so a
text-only model never silently drops the images.
"""

from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from synthorg.api.boundary import parse_typed
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.vision_verify import (
    VISION_LLM_CALL_COMPLETED,
    VISION_LLM_CALL_STARTED,
    VISION_LLM_UNSUPPORTED,
    VISION_VERIFIER_FAILED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import ImageMediaType, MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    ImagePart,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.security.visionverify.config import VisionVerifierKind
from synthorg.security.visionverify.errors import VisionModelUnsupportedError
from synthorg.security.visionverify.models import (
    VisionFinding,
    VisionFindingCategory,
    VisionReviewInput,
    VisionSeverity,
    VisionVerificationReport,
)
from synthorg.security.visionverify.prompt import build_user_text, system_prompt
from synthorg.security.visionverify.verifiers._image import (
    read_png_base64,
    resolve_screenshot,
)

logger = get_logger(__name__)

_VERDICT_TOOL_NAME: Final[str] = "record_vision_verdict"
_VERDICT_TOOL_DESCRIPTION: Final[str] = (
    "Record the vision verdict: a confidence in [0, 1], a one-paragraph "
    "summary, and a list of findings for any mismatch between the running "
    "UI and the brief."
)
_VERDICT_TOOL_SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "properties": {
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [c.value for c in VisionFindingCategory],
                    },
                    "severity": {
                        "type": "string",
                        "enum": [s.value for s in VisionSeverity],
                    },
                    "description": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "suggested_fix": {"type": "string"},
                },
                "required": ["category", "severity", "description", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["confidence", "summary", "findings"],
    "additionalProperties": False,
}

_DEFAULT_MAX_TOKENS: Final[int] = 2048
_DEFAULT_CONFIDENCE_ON_ERROR: Final[float] = 0.0
_DEGRADED_SUMMARY: Final[str] = (
    "Vision model returned an unparseable verdict; recorded a non-blocking "
    "informational finding so completion is not blocked by a model fault."
)
_DEGRADED_DESCRIPTION: Final[str] = (
    "The vision verifier could not parse a structured verdict from the model."
)

_VERDICT_ARGS_CONFIG = ConfigDict(frozen=True, extra="forbid")


class _FindingArgs(BaseModel):
    """Typed view of one finding entry in the verdict tool call."""

    model_config = _VERDICT_ARGS_CONFIG

    category: VisionFindingCategory
    severity: VisionSeverity
    description: NotBlankStr
    evidence: tuple[NotBlankStr, ...] = ()
    suggested_fix: NotBlankStr | None = None


class _VerdictArgs(BaseModel):
    """Typed view of the model's ``record_vision_verdict`` arguments."""

    model_config = _VERDICT_ARGS_CONFIG

    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    findings: tuple[_FindingArgs, ...] = ()


class LLMVisionVerifier:
    """Vision verifier backed by a multimodal completion provider."""

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model_id: NotBlankStr,
        workspace: Path,
        cost_tracker: CostTracker | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        """Wire the verifier to a provider, model, and workspace.

        Args:
            provider: Multimodal completion provider.
            model_id: Resolved vision-capable model identifier.
            workspace: Workspace root holding the screenshots to encode.
            cost_tracker: Optional cost tracker for the verification call.
            max_tokens: Generation cap for the verdict tool call.

        Raises:
            ValueError: If ``workspace`` is not an absolute path.
        """
        if not workspace.is_absolute():
            msg = f"workspace must be absolute, got {workspace!r}"
            raise ValueError(msg)
        self._provider = provider
        self._model_id = model_id
        self._workspace = workspace.resolve()
        self._cost_tracker = cost_tracker
        self._max_tokens = max_tokens

    @property
    def kind(self) -> str:
        """Return the ``llm_vision`` discriminator."""
        return VisionVerifierKind.LLM_VISION.value

    async def verify(
        self,
        review_input: VisionReviewInput,
    ) -> VisionVerificationReport:
        """Send screenshots + brief to the model and parse its verdict.

        Returns:
            The parsed verification report (a degraded INFO report when
            the model response is malformed).
        """
        await self._require_vision_support(review_input)
        image_parts = self._encode_screenshots(review_input)
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt()),
            ChatMessage(
                role=MessageRole.USER,
                content=build_user_text(review_input),
                image_parts=image_parts,
            ),
        ]
        logger.info(
            VISION_LLM_CALL_STARTED,
            task_id=review_input.task_id,
            execution_id=review_input.execution_id,
            model=self._model_id,
            screenshots=len(image_parts),
        )
        arguments = await self._call_verdict_tool(messages, review_input)
        report = self._parse_report(arguments, review_input)
        logger.info(
            VISION_LLM_CALL_COMPLETED,
            task_id=review_input.task_id,
            execution_id=review_input.execution_id,
            findings=len(report.findings),
            confidence=report.confidence,
        )
        return report

    async def _require_vision_support(self, review_input: VisionReviewInput) -> None:
        """Raise when the model does not accept image inputs.

        Raises:
            VisionModelUnsupportedError: If the model lacks vision
                support.
        """
        capabilities = await self._provider.get_model_capabilities(self._model_id)
        if not capabilities.supports_vision:
            logger.warning(
                VISION_LLM_UNSUPPORTED,
                task_id=review_input.task_id,
                model=self._model_id,
            )
            msg = f"Model {self._model_id!r} does not support image inputs"
            raise VisionModelUnsupportedError(msg, context={"model": self._model_id})

    def _encode_screenshots(
        self,
        review_input: VisionReviewInput,
    ) -> tuple[ImagePart, ...]:
        """Read each referenced screenshot into a PNG ``ImagePart``.

        Returns:
            One base64-encoded PNG ``ImagePart`` per referenced
            screenshot.
        """
        parts: list[ImagePart] = []
        for ref in review_input.screenshots:
            path = resolve_screenshot(self._workspace, ref.workspace_path)
            parts.append(
                ImagePart(
                    media_type=ImageMediaType.PNG,
                    base64_data=read_png_base64(path),
                ),
            )
        return tuple(parts)

    async def _call_verdict_tool(
        self,
        messages: list[ChatMessage],
        review_input: VisionReviewInput,
    ) -> dict[str, JsonValue]:
        """Invoke the provider with the verdict tool; return its arguments.

        Returns:
            The verdict tool call's arguments, or an empty dict when the
            model did not call the tool.
        """
        tool = ToolDefinition(
            name=_VERDICT_TOOL_NAME,
            description=_VERDICT_TOOL_DESCRIPTION,
            parameters_schema=_VERDICT_TOOL_SCHEMA,
        )
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=review_input.evaluator_agent_id,
            task_id=NotBlankStr(f"system:vision_verify:{review_input.task_id}"),
            call_category=LLMCallCategory.SYSTEM,
        ):
            response = await self._provider.complete(
                messages=messages,
                model=self._model_id,
                tools=[tool],
                config=CompletionConfig(
                    temperature=0.0,
                    max_tokens=self._max_tokens,
                ),
            )
        for call in response.tool_calls:
            if call.name == _VERDICT_TOOL_NAME:
                return dict(call.arguments)
        return {}

    def _parse_report(
        self,
        arguments: dict[str, JsonValue],
        review_input: VisionReviewInput,
    ) -> VisionVerificationReport:
        """Map the model's tool arguments to a structured report.

        A malformed response degrades to a non-blocking INFO finding
        rather than raising, so a model fault never blocks completion.

        Returns:
            The structured report, or a degraded INFO report when the
            arguments fail validation.
        """
        try:
            verdict = parse_typed("vision.verdict", arguments, _VerdictArgs)
            summary = NotBlankStr(verdict.summary.strip() or _DEGRADED_SUMMARY)
            return VisionVerificationReport(
                task_id=review_input.task_id,
                execution_id=review_input.execution_id,
                findings=tuple(self._to_finding(entry) for entry in verdict.findings),
                summary=summary,
                verifier_kind=VisionVerifierKind.LLM_VISION.value,
                model_id=self._model_id,
                confidence=verdict.confidence,
                generator_agent_id=review_input.generator_agent_id,
                evaluator_agent_id=review_input.evaluator_agent_id,
            )
        except ValidationError as exc:
            logger.warning(
                VISION_VERIFIER_FAILED,
                task_id=review_input.task_id,
                execution_id=review_input.execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                policy="degrade_to_info",
            )
            return self._degraded_report(review_input)

    @staticmethod
    def _to_finding(entry: _FindingArgs) -> VisionFinding:
        """Map one validated finding entry to a ``VisionFinding``.

        Returns:
            The ``VisionFinding`` built from the validated entry.
        """
        return VisionFinding(
            category=entry.category,
            severity=entry.severity,
            description=entry.description,
            evidence=entry.evidence,
            suggested_fix=entry.suggested_fix,
        )

    def _degraded_report(
        self,
        review_input: VisionReviewInput,
    ) -> VisionVerificationReport:
        """Build the non-blocking report returned on a model fault.

        Returns:
            A ``VisionVerificationReport`` carrying a single INFO
            degraded finding.
        """
        finding = VisionFinding(
            category=VisionFindingCategory.VISUAL_DEFECT,
            severity=VisionSeverity.INFO,
            description=_DEGRADED_DESCRIPTION,
            evidence=(),
        )
        return VisionVerificationReport(
            task_id=review_input.task_id,
            execution_id=review_input.execution_id,
            findings=(finding,),
            summary=_DEGRADED_SUMMARY,
            verifier_kind=VisionVerifierKind.LLM_VISION.value,
            model_id=self._model_id,
            confidence=_DEFAULT_CONFIDENCE_ON_ERROR,
            generator_agent_id=review_input.generator_agent_id,
            evaluator_agent_id=review_input.evaluator_agent_id,
        )
