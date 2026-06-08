# module-kind: adapter
"""LLM-based rubric grader.

Grades a ``HandoffArtifact`` against a ``VerificationRubric`` by
invoking a structured tool call on a ``CompletionProvider``.  The
provider is expected to invoke the ``emit_rubric_verdict`` tool with
per-criterion grades, an overall verdict, a confidence, and optional
findings.

The grader favors *safe* behavior when the model misbehaves: any
malformed response, missing criterion grade, or out-of-range value is
mapped to a ``REFER`` verdict with ``confidence=0.0``.  Per the
verification design, ``REFER`` routes to human review, so the grader
never silently passes on a broken model response.
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_UNTRUSTED_ARTIFACT,
    wrap_untrusted,
)
from synthorg.engine.quality.graders._llm_parser import (
    parse_confidence,
    parse_findings,
    parse_grades,
    parse_verdict,
)
from synthorg.engine.quality.graders._llm_prompt import (
    _DEFAULT_MAX_TOKENS,
    _GRADER_SYSTEM_PROMPT,
    _GRADER_TOOL_DESCRIPTION,
    _GRADER_TOOL_NAME,
    _GRADER_TOOL_REQUIRED_KEYS,
    _GRADER_TOOL_SCHEMA,
    _MAX_PAYLOAD_CHARS,
    build_instructions,
    render_artifact_block,
    render_probes_block,
    render_rubric_block,
)
from synthorg.engine.quality.verification import (
    AtomicProbe,
    VerificationResult,
    VerificationRubric,
    VerificationVerdict,
)
from synthorg.engine.workflow.handoff import HandoffArtifact
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.verification import (
    VERIFICATION_GRADER_CONFIG_INVALID,
    VERIFICATION_GRADER_FAILED,
    VERIFICATION_GRADER_PAYLOAD_TRUNCATED,
    VERIFICATION_GRADER_RESPONSE_INVALID,
    VERIFICATION_GRADING_COMPLETED,
    VERIFICATION_GRADING_STARTED,
    VERIFICATION_VERDICT_OVERRIDDEN_TO_REFER,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.resilience.errors import RetryExhaustedError

if TYPE_CHECKING:
    from synthorg.budget.tracker import CostTracker

logger = get_logger(__name__)


class LLMRubricGrader:
    """Grade handoff artifacts against rubrics via a provider tool call.

    Args:
        provider: Completion provider used for the grading call.
        model_id: Resolved model identifier for the configured tier.
        min_confidence_override: Optional floor on confidence; when set
            (and greater than the rubric's ``min_confidence``), any
            response with lower confidence is downgraded to REFER.
    """

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model_id: NotBlankStr,
        min_confidence_override: float | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        """Store dependencies and validate override bounds.

        Raises:
            ValueError: If ``min_confidence_override`` is outside
                ``[0, 1]``.
        """
        if min_confidence_override is not None and not (
            0.0 <= min_confidence_override <= 1.0
        ):
            logger.error(
                VERIFICATION_GRADER_CONFIG_INVALID,
                min_confidence_override=min_confidence_override,
                reason="out of [0, 1] range",
            )
            msg = "min_confidence_override must be in [0, 1]"
            raise ValueError(msg)
        self._provider = provider
        self._model_id = model_id
        self._min_confidence_override = min_confidence_override
        self._cost_tracker = cost_tracker

    @property
    def name(self) -> str:
        """Strategy name."""
        return "llm"

    async def grade(
        self,
        *,
        artifact: HandoffArtifact,
        rubric: VerificationRubric,
        probes: tuple[AtomicProbe, ...],
        generator_agent_id: NotBlankStr,
        evaluator_agent_id: NotBlankStr,
    ) -> VerificationResult:
        """Grade *artifact* against *rubric* using the LLM tool schema.

        Args:
            artifact: The handoff artifact to evaluate.
            rubric: Rubric with criteria, weights, and calibration examples.
            probes: Atomic probes derived from the acceptance criteria.
            generator_agent_id: Agent that produced the artifact.
            evaluator_agent_id: Agent performing the evaluation.

        Returns:
            Structured ``VerificationResult``.  Returns ``REFER`` with
            ``confidence=0.0`` on any malformed model response; callers
            route REFER to human review per the spec.
        """
        logger.info(
            VERIFICATION_GRADING_STARTED,
            rubric_name=rubric.name,
            grader=self.name,
            probe_count=len(probes),
        )
        messages = self._prepare_messages(
            artifact=artifact,
            rubric=rubric,
            probes=probes,
            generator_agent_id=generator_agent_id,
            evaluator_agent_id=evaluator_agent_id,
        )
        response = await self._call_grader_tool(
            messages=messages,
            rubric=rubric,
            probes=probes,
            generator_agent_id=generator_agent_id,
            evaluator_agent_id=evaluator_agent_id,
        )
        tool_call_or_reason = self._locate_tool_call(response)
        if isinstance(tool_call_or_reason, str):
            return self._refer(
                rubric=rubric,
                generator_agent_id=generator_agent_id,
                evaluator_agent_id=evaluator_agent_id,
                reason=tool_call_or_reason,
            )
        interpreted = self._interpret_tool_call(tool_call_or_reason, rubric)
        if isinstance(interpreted, str):
            return self._refer(
                rubric=rubric,
                generator_agent_id=generator_agent_id,
                evaluator_agent_id=evaluator_agent_id,
                reason=interpreted,
            )
        return self._assemble_result(
            interpreted,
            rubric=rubric,
            generator_agent_id=generator_agent_id,
            evaluator_agent_id=evaluator_agent_id,
        )

    def _prepare_messages(
        self,
        *,
        artifact: HandoffArtifact,
        rubric: VerificationRubric,
        probes: tuple[AtomicProbe, ...],
        generator_agent_id: NotBlankStr,
        evaluator_agent_id: NotBlankStr,
    ) -> list[ChatMessage]:
        """Build grader messages; log + re-raise on envelope failure.

        Returns:
            The list of ``ChatMessage`` instances (system + user) passed
            to the provider's ``complete()`` call.
        """
        try:
            return self._build_messages(
                artifact=artifact,
                rubric=rubric,
                probes=probes,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                VERIFICATION_GRADER_FAILED,
                exc,
                rubric_name=rubric.name,
                grader=self.name,
                model_id=self._model_id,
                tool_name=_GRADER_TOOL_NAME,
                probe_count=len(probes),
                generator_agent_id=generator_agent_id,
                evaluator_agent_id=evaluator_agent_id,
                stage="build_messages",
            )
            raise

    def _assemble_result(
        self,
        interpreted: tuple[
            Mapping[str, float],
            VerificationVerdict,
            float,
            tuple[str, ...],
        ],
        *,
        rubric: VerificationRubric,
        generator_agent_id: NotBlankStr,
        evaluator_agent_id: NotBlankStr,
    ) -> VerificationResult:
        """Build the ``VerificationResult`` + emit the completion event.

        Returns:
            A :class:`VerificationResult` carrying the LLM-decided
            verdict, confidence, per-criterion grades, findings, and
            grading metadata for downstream routing.
        """
        per_criterion_grades, verdict, confidence, findings = interpreted
        result = VerificationResult(
            verdict=verdict,
            confidence=confidence,
            per_criterion_grades=per_criterion_grades,
            findings=findings,
            evaluator_agent_id=evaluator_agent_id,
            generator_agent_id=generator_agent_id,
            rubric_name=rubric.name,
            timestamp=datetime.now(UTC),
        )
        logger.info(
            VERIFICATION_GRADING_COMPLETED,
            rubric_name=rubric.name,
            verdict=result.verdict.value,
            confidence=result.confidence,
            grader=self.name,
        )
        return result

    def _build_messages(
        self,
        *,
        artifact: HandoffArtifact,
        rubric: VerificationRubric,
        probes: tuple[AtomicProbe, ...],
    ) -> list[ChatMessage]:
        """Build the system + user message list for the grader call.

        Returns:
            A two-message list (system prompt + JSON-rendered grader
            envelope) ready for ``CompletionProvider.complete()``.
        """
        user_prompt = self._build_user_prompt(
            artifact=artifact,
            rubric=rubric,
            probes=probes,
        )
        return [
            ChatMessage(role=MessageRole.SYSTEM, content=_GRADER_SYSTEM_PROMPT),
            ChatMessage(role=MessageRole.USER, content=user_prompt),
        ]

    async def _call_grader_tool(
        self,
        *,
        messages: list[ChatMessage],
        rubric: VerificationRubric,
        probes: tuple[AtomicProbe, ...],
        generator_agent_id: NotBlankStr,
        evaluator_agent_id: NotBlankStr,
    ) -> object:
        """Invoke the provider with the grader tool; log/re-raise on failure.

        Infrastructure errors propagate unchanged:

        * ``MemoryError`` / ``RecursionError`` are fatal interpreter
          signals and always re-raise (via :func:`reraise_critical`
          at the head of the broad-except block).
        * ``RetryExhaustedError`` logs a
          ``VERIFICATION_GRADER_FAILED`` event with full grading
          context then re-raises so the engine fallback chain takes
          over.
        * Any other exception also logs and re-raises.

        Returns:
            The provider's raw completion response (an object whose
            ``tool_calls`` field is inspected by
            :meth:`_locate_tool_call`).

        Raises:
            RetryExhaustedError: When the provider's retry budget for
                this call is exhausted.
        """
        tool = ToolDefinition(
            name=_GRADER_TOOL_NAME,
            description=_GRADER_TOOL_DESCRIPTION,
            parameters_schema=_GRADER_TOOL_SCHEMA,
        )
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                # The grading LLM call is verification work performed
                # by the evaluator on the generator's artifact.  Charge
                # it to the evaluator (the agent doing the grading)
                # rather than to the generator (the artifact producer);
                # the task_id already encodes the evaluator identity
                # for cross-reference.
                agent_id=evaluator_agent_id,
                task_id=NotBlankStr(f"system:verification:{evaluator_agent_id}"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                return await self._provider.complete(
                    messages=messages,
                    model=self._model_id,
                    tools=[tool],
                    config=CompletionConfig(
                        temperature=0.0,
                        max_tokens=_DEFAULT_MAX_TOKENS,
                    ),
                )
        except RetryExhaustedError as exc:
            log_exception_redacted(
                logger,
                VERIFICATION_GRADER_FAILED,
                exc,
                rubric_name=rubric.name,
                grader=self.name,
                model_id=self._model_id,
                tool_name=_GRADER_TOOL_NAME,
                probe_count=len(probes),
                generator_agent_id=generator_agent_id,
                evaluator_agent_id=evaluator_agent_id,
                reason="retry_exhausted",
            )
            raise
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                VERIFICATION_GRADER_FAILED,
                exc,
                rubric_name=rubric.name,
                grader=self.name,
                model_id=self._model_id,
                tool_name=_GRADER_TOOL_NAME,
                probe_count=len(probes),
                generator_agent_id=generator_agent_id,
                evaluator_agent_id=evaluator_agent_id,
            )
            raise

    def _locate_tool_call(self, response: object) -> object:
        """Return the sole ``emit_rubric_verdict`` tool call or a reason string.

        Validates the response shape defensively so a misbehaving
        provider stub / bad payload fails closed to ``REFER`` with a
        logged warning instead of crashing the grader.
        """
        tool_calls = getattr(response, "tool_calls", None)
        if not isinstance(tool_calls, list | tuple):
            logger.warning(
                VERIFICATION_GRADER_RESPONSE_INVALID,
                grader=self.name,
                reason="tool_calls is not a list/tuple",
                tool_calls_type=type(tool_calls).__name__,
            )
            return "tool_calls field missing or not iterable"
        for index, tc in enumerate(tool_calls):
            if not isinstance(getattr(tc, "name", None), str):
                logger.warning(
                    VERIFICATION_GRADER_RESPONSE_INVALID,
                    grader=self.name,
                    reason="tool_call entry has non-string .name",
                    index=index,
                )
                return "tool_call entry missing string .name"
        matches = [tc for tc in tool_calls if tc.name == _GRADER_TOOL_NAME]
        if len(tool_calls) != 1 or len(matches) != 1:
            return (
                "ambiguous or unexpected tool_call(s) in response "
                f"(total={len(tool_calls)}, matches={len(matches)})"
            )
        return matches[0]

    def _interpret_tool_call(
        self,
        tool_call: object,
        rubric: VerificationRubric,
    ) -> (
        tuple[
            Mapping[str, float],
            VerificationVerdict,
            float,
            tuple[str, ...],
        ]
        | str
    ):
        """Parse tool arguments and apply the min-confidence downgrade.

        Returns:
            The parsed ``(grades, verdict, confidence, findings)`` tuple
            on success, or a reason string describing why the response
            is unusable (callers route the latter to ``REFER``).
        """
        arguments = getattr(tool_call, "arguments", None)
        if not isinstance(arguments, Mapping):
            logger.warning(
                VERIFICATION_GRADER_RESPONSE_INVALID,
                grader=self.name,
                reason="tool_call.arguments is not a mapping",
                arguments_type=type(arguments).__name__,
            )
            return "tool_call.arguments is not a mapping"
        parsed = self._parse_tool_arguments(arguments, rubric=rubric)
        if isinstance(parsed, str):
            return parsed
        per_criterion_grades, verdict, confidence, findings = parsed
        applied_min_conf = self._applied_min_confidence(rubric)
        if confidence < applied_min_conf:
            # State transition: the parsed verdict is about to be
            # downgraded to REFER.  Log at INFO so operators see the
            # override in telemetry alongside the grading.completed
            # event that follows.
            logger.info(
                VERIFICATION_VERDICT_OVERRIDDEN_TO_REFER,
                grader=self.name,
                rubric_name=rubric.name,
                previous_verdict=verdict.value,
                confidence=confidence,
                applied_min_confidence=applied_min_conf,
            )
            verdict = VerificationVerdict.REFER
            findings = (
                *findings,
                f"Confidence {confidence:.2f} below minimum "
                f"{applied_min_conf:.2f}; downgraded to REFER.",
            )
        return per_criterion_grades, verdict, confidence, findings

    def _applied_min_confidence(self, rubric: VerificationRubric) -> float:
        """Return the stricter of rubric min_confidence and override."""
        if self._min_confidence_override is None:
            return rubric.min_confidence
        return max(rubric.min_confidence, self._min_confidence_override)

    def _build_user_prompt(
        self,
        *,
        artifact: HandoffArtifact,
        rubric: VerificationRubric,
        probes: tuple[AtomicProbe, ...],
    ) -> str:
        """Serialize the grader envelope to JSON.

        Returns:
            The JSON-encoded grader envelope sent as the user message.
        """
        envelope = self._render_envelope(
            artifact=artifact,
            rubric=rubric,
            probes=probes,
        )
        return json.dumps(envelope, ensure_ascii=False)

    def _render_envelope(
        self,
        *,
        artifact: HandoffArtifact,
        rubric: VerificationRubric,
        probes: tuple[AtomicProbe, ...],
    ) -> dict[str, object]:
        """Render the prompt envelope (rubric / calibration / probes / artifact).

        Returns:
            The envelope dict (``rubric`` / ``probes`` / ``artifact`` /
            ``instructions``) that becomes the JSON user-message body.
        """
        payload_text, payload_truncated, original_len = self._prepare_payload_text(
            artifact=artifact,
            rubric=rubric,
        )
        return {
            "rubric": render_rubric_block(rubric),
            "probes": render_probes_block(probes),
            "artifact": render_artifact_block(artifact, payload_text=payload_text),
            "instructions": build_instructions(
                payload_truncated=payload_truncated,
                original_len=original_len,
            ),
        }

    def _prepare_payload_text(
        self,
        *,
        artifact: HandoffArtifact,
        rubric: VerificationRubric,
    ) -> tuple[str, bool, int]:
        """Serialize + truncate the artifact payload; log when truncation fires.

        The serialized payload is wrapped in an ``<untrusted-artifact>``
        fence so the model cannot mistake attacker-controlled
        artifact content for instructions.

        Returns:
            ``(payload_text, payload_truncated, original_len)``:
            ``payload_text`` is the wrapped (and possibly truncated)
            JSON body, ``payload_truncated`` is ``True`` when truncation
            fired, and ``original_len`` is the pre-truncation character
            count for telemetry.
        """
        raw_payload = json.dumps(dict(artifact.payload), ensure_ascii=False)
        original_len = len(raw_payload)
        payload_truncated = original_len > _MAX_PAYLOAD_CHARS
        if payload_truncated:
            logger.warning(
                VERIFICATION_GRADER_PAYLOAD_TRUNCATED,
                rubric_name=rubric.name,
                grader=self.name,
                original_chars=original_len,
                truncated_chars=_MAX_PAYLOAD_CHARS,
            )
            raw_payload = raw_payload[:_MAX_PAYLOAD_CHARS]
        payload_text = wrap_untrusted(TAG_UNTRUSTED_ARTIFACT, raw_payload)
        return payload_text, payload_truncated, original_len

    def _parse_tool_arguments(
        self,
        arguments: Mapping[str, object],
        *,
        rubric: VerificationRubric,
    ) -> tuple[dict[str, float], VerificationVerdict, float, tuple[str, ...]] | str:
        """Parse and validate the tool call arguments.

        Enforces ``_GRADER_TOOL_SCHEMA`` at the system boundary:

        * every ``required`` key must be present (no implicit defaults),
        * ``additionalProperties=False`` is enforced (reject unknown keys),

        before delegating to the per-field parsers.

        Returns:
            The parsed ``(grades, verdict, confidence, findings)`` tuple
            on success, or a reason string describing the validation
            failure on any malformed key, type, or out-of-range value.
        """
        actual = set(arguments.keys())
        extra = actual - _GRADER_TOOL_REQUIRED_KEYS
        if extra:
            return f"unexpected keys in tool arguments: {sorted(extra)!r}"
        missing = _GRADER_TOOL_REQUIRED_KEYS - actual
        if missing:
            return f"missing required keys in tool arguments: {sorted(missing)!r}"

        grades_or_reason = parse_grades(
            arguments["per_criterion_grades"],
            rubric=rubric,
        )
        if not isinstance(grades_or_reason, dict):
            return grades_or_reason
        grades = grades_or_reason

        verdict_or_reason = parse_verdict(arguments["verdict"])
        if not isinstance(verdict_or_reason, VerificationVerdict):
            return verdict_or_reason
        verdict = verdict_or_reason

        confidence_or_reason = parse_confidence(arguments["confidence"])
        if isinstance(confidence_or_reason, str):
            return confidence_or_reason
        confidence = float(confidence_or_reason)

        findings_or_reason = parse_findings(arguments["findings"])
        if not isinstance(findings_or_reason, tuple):
            return findings_or_reason
        findings = findings_or_reason

        return grades, verdict, confidence, findings

    def _refer(
        self,
        *,
        rubric: VerificationRubric,
        generator_agent_id: NotBlankStr,
        evaluator_agent_id: NotBlankStr,
        reason: str,
    ) -> VerificationResult:
        """Build a safe REFER result when the model response is unusable.

        Returns:
            A :class:`VerificationResult` with verdict ``REFER``,
            ``confidence=0.0``, zeroed per-criterion grades, and a
            findings entry naming the failure reason; the caller emits
            the ``grading.completed`` event.
        """
        logger.error(
            VERIFICATION_GRADER_RESPONSE_INVALID,
            rubric_name=rubric.name,
            grader=self.name,
            reason=reason,
        )
        result = VerificationResult(
            verdict=VerificationVerdict.REFER,
            confidence=0.0,
            per_criterion_grades={c.name: 0.0 for c in rubric.criteria},
            findings=(f"LLM grader response invalid: {reason}",),
            evaluator_agent_id=evaluator_agent_id,
            generator_agent_id=generator_agent_id,
            rubric_name=rubric.name,
            timestamp=datetime.now(UTC),
        )
        logger.info(
            VERIFICATION_GRADING_COMPLETED,
            rubric_name=rubric.name,
            verdict=result.verdict.value,
            confidence=result.confidence,
            grader=self.name,
        )
        return result
