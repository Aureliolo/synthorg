"""Agent-driven intake strategy using a completion provider."""

import json
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.client.models import (
    ClientRequest,
    TaskRequirement,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.review_pipeline import (
    INTAKE_AGENT_EMPTY_RESPONSE,
    INTAKE_AGENT_PARSE_FAILED,
    INTAKE_AGENT_REFINED_INVALID,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

if TYPE_CHECKING:
    from synthorg.budget.tracker import CostTracker
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)
_DEFAULT_MAX_TOKENS: Final[int] = 512


_DEFAULT_PERSONA = (
    "You are an intake manager evaluating client requests. For each "
    "request, decide whether it is ready to become a task. Respond "
    "with JSON only.\n\n" + untrusted_content_directive((TAG_TASK_DATA,))
)


class AgentIntake:
    """Intake strategy that routes each request through an LLM.

    Builds a structured triage prompt, asks the provider for an
    accept/reject decision with an optional refined title and
    description, and either creates a task or returns a rejection
    with the model's reason. On parse or validation failure, the
    request is rejected with an explanatory reason so downstream
    state transitions remain deterministic.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        task_engine: TaskEngine,
        provider: CompletionProvider,
        model: NotBlankStr,
        project: NotBlankStr = "simulation",
        requested_by: NotBlankStr = "intake-agent",
        persona: str = _DEFAULT_PERSONA,
        temperature: float = 0.0,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        """Initialize the agent intake strategy.

        Args:
            task_engine: Task engine used on acceptance.
            provider: Vendor-agnostic completion provider for the
                triage agent.
            model: Model identifier passed to the provider.
            project: Project stamped on created tasks.
            requested_by: Identity recorded as the task creator.
            persona: System prompt persona for the triage agent. The
                default ``_DEFAULT_PERSONA`` already carries the
                :func:`untrusted_content_directive`. If a caller
                supplies a custom persona that lacks the directive,
                the directive is appended automatically so
                ``<task-data>`` fences keep their enforced-untrusted
                semantics.
            temperature: Sampling temperature (default 0.0 -- triage
                is classification, determinism wins over diversity).
            max_tokens: Maximum tokens in the triage response.
            cost_tracker: Optional cost tracker; when wired the
                chokepoint emits a CostRecord for each LLM call.
        """
        self._task_engine = task_engine
        self._provider = provider
        self._model = model
        self._project = project
        self._requested_by = requested_by
        self._cost_tracker = cost_tracker
        # Normalize the persona to always carry the untrusted-content
        # directive. A caller-supplied persona without it would
        # silently weaken fence semantics.
        directive = untrusted_content_directive((TAG_TASK_DATA,))
        self._persona = (
            persona if directive in persona else f"{persona.rstrip()}\n\n{directive}"
        )
        # Pin temperature + max_tokens at construction so downstream
        # tests can assert a stable call shape.
        self._completion_config = CompletionConfig(
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def process(self, request: ClientRequest) -> IntakeResult:
        """Invoke the triage agent and create a task on acceptance.

        Returns:
            An :class:`IntakeResult` carrying the triage verdict and
            (on acceptance) the created task's id; rejection reasons
            surface here when the agent declines.
        """
        messages = self._build_prompt(request.requirement)
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=NotBlankStr("system"),
                task_id=NotBlankStr(f"system:intake:{request.request_id}"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages=messages,
                    model=self._model,
                    config=self._completion_config,
                )
        except Exception as exc:
            log_exception_redacted(
                logger,
                INTAKE_AGENT_PARSE_FAILED,
                exc,
                request_id=request.request_id,
                client_id=request.client_id,
                stage="provider_call",
            )
            raise
        content = response.content
        if not content:
            logger.warning(
                INTAKE_AGENT_EMPTY_RESPONSE,
                request_id=request.request_id,
            )
            return IntakeResult.rejected_result(
                request_id=request.request_id,
                reason="intake agent returned empty response",
            )
        decision = self._parse_decision(content)
        if decision is None:
            reason = "intake agent returned malformed response"
            logger.warning(
                INTAKE_AGENT_PARSE_FAILED,
                request_id=request.request_id,
            )
            return IntakeResult.rejected_result(
                request_id=request.request_id,
                reason=reason,
            )
        accepted = decision.get("accepted")
        if not isinstance(accepted, bool):
            logger.warning(
                INTAKE_AGENT_PARSE_FAILED,
                request_id=request.request_id,
            )
            return IntakeResult.rejected_result(
                request_id=request.request_id,
                reason="intake agent returned malformed 'accepted' field",
            )
        if not accepted:
            reason = str(decision.get("reason") or "intake agent rejected request")
            return IntakeResult.rejected_result(
                request_id=request.request_id,
                reason=reason,
            )

        try:
            refined = self._refine_requirement(request.requirement, decision)
            data = self._build_task_data(refined)
        except ValidationError:
            logger.warning(
                INTAKE_AGENT_REFINED_INVALID,
                request_id=request.request_id,
            )
            return IntakeResult.rejected_result(
                request_id=request.request_id,
                reason="refined requirement failed validation",
            )
        task = await self._task_engine.create_task(
            data,
            requested_by=self._requested_by,
        )
        return IntakeResult.accepted_result(
            request_id=request.request_id,
            task_id=str(task.id),
        )

    def _build_prompt(self, requirement: TaskRequirement) -> list[ChatMessage]:
        # ``title`` and ``description`` are user-supplied free-form
        # strings that reach the model verbatim. Wrap both in the
        # ``<task-data>`` fence declared by ``_DEFAULT_PERSONA``.
        # ``task_type`` / ``priority`` / ``estimated_complexity`` are
        # typed enums that Pydantic validates at the model boundary,
        # so their ``.value`` access yields a known-safe string (no
        # wrap needed).
        fenced_title = wrap_untrusted(TAG_TASK_DATA, requirement.title)
        fenced_description = wrap_untrusted(
            TAG_TASK_DATA,
            requirement.description,
        )
        user = (
            "Review this client request and decide if it should "
            "become a task.\n\n"
            f"Title: {fenced_title}\n"
            f"Description: {fenced_description}\n"
            f"Type: {requirement.task_type.value}\n"
            f"Priority: {requirement.priority.value}\n"
            f"Complexity: {requirement.estimated_complexity.value}\n\n"
            "Return JSON only, with these keys:\n"
            "  accepted: boolean\n"
            "  reason: string (required when rejected)\n"
            "  refined_title: optional string\n"
            "  refined_description: optional string"
        )
        return [
            ChatMessage(role=MessageRole.SYSTEM, content=self._persona),
            ChatMessage(role=MessageRole.USER, content=user),
        ]

    @staticmethod
    def _parse_decision(content: str) -> dict[str, Any] | None:
        stripped = content.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start == -1 or end == -1 or end < start:
                return None
            try:
                parsed = json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    @staticmethod
    def _refine_requirement(
        original: TaskRequirement,
        decision: dict[str, Any],
    ) -> TaskRequirement:
        refined_title = decision.get("refined_title")
        refined_description = decision.get("refined_description")
        if not refined_title and not refined_description:
            return original
        payload = original.model_dump()
        payload.update(
            {
                "title": refined_title or original.title,
                "description": (refined_description or original.description),
            },
        )
        return type(original).model_validate(payload)

    def _build_task_data(self, requirement: TaskRequirement) -> CreateTaskData:
        return CreateTaskData(
            title=requirement.title,
            description=requirement.description,
            type=requirement.task_type,
            priority=requirement.priority,
            project=self._project,
            created_by=self._requested_by,
            estimated_complexity=requirement.estimated_complexity,
        )
