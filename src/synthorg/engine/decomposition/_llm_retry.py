# module-kind: code
"""What the single-shot strategy does between one refused reply and the next.

Pattern B (see ``docs/reference/retry-patterns.md``) is semantic
self-correction: no backoff, and each round re-prompts with what went wrong.
Two decisions live here rather than inline in the retry loop, because both are
about the REPLY rather than about the loop's own accounting.
"""

import json

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.engine.decomposition._mangled_arguments import (
    mangled_serialisation_hint,
)
from synthorg.engine.decomposition.llm_prompt import build_retry_message
from synthorg.engine.errors import DecompositionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import DECOMPOSITION_FAILED
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


def mangled_reply_hint(response: CompletionResponse) -> str | None:
    """Say how to re-issue *response*, or nothing when it arrived intact.

    Both channels, because this strategy reads both: a reply carrying no tool
    call is parsed from its content, and the same collapse there would fall
    through to the ordinary schema error and spend one of the operator's
    planning attempts on a fault upstream of the model. The content is decoded
    first, since the artefact is structural and a string mentioning the key is
    prose rather than a mangled call.

    Args:
        response: What the provider answered.

    Returns:
        The re-serialisation instruction, or ``None``.
    """
    for call in response.tool_calls:
        hint = mangled_serialisation_hint(call.arguments)
        if hint is not None:
            return hint
    if not response.content:
        return None
    try:
        decoded = json.loads(response.content)
    except ValueError:
        return None
    return mangled_serialisation_hint(decoded)


def with_retry_context(
    messages: list[ChatMessage],
    last_response: CompletionResponse | None,
    last_error: str,
) -> list[ChatMessage]:
    """Append the failed exchange and the correction to *messages*.

    Self-correction means the model has to see what it said as well as what was
    wrong with it: handing back only the error asks it to correct a reply it
    can no longer read.

    Args:
        messages: The conversation so far.
        last_response: What the provider answered last, if anything.
        last_error: Why that answer was refused, already redacted.

    Returns:
        A new message list; the caller's is left alone.
    """
    return [
        *messages,
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content=(last_response.content or "") if last_response else "",
            tool_calls=last_response.tool_calls if last_response else (),
        ),
        build_retry_message(last_error),
    ]


async def ask_for_plan(
    *,
    provider: CompletionProvider,
    model: str,
    cost_tracker: CostTrackerProtocol | None,
    task: Task,
    messages: list[ChatMessage],
    tool_def: ToolDefinition,
    config: CompletionConfig,
) -> CompletionResponse:
    """Make one planning call, under this task's cost scope.

    Args:
        provider: The completion driver this strategy dispatches through.
        model: The model id it was bound to.
        cost_tracker: Where the call's spend is recorded, if anywhere.
        task: What is being decomposed, for the cost scope and the message.
        messages: The conversation so far, corrections included.
        tool_def: The plan-submission tool this round offers.
        config: Temperature and the output ceiling.

    Returns:
        The provider's reply, unread.

    Raises:
        DecompositionError: The call failed for a reason re-prompting cannot
            fix.
    """
    try:
        async with cost_recording_scope(
            cost_tracker=cost_tracker,
            task_id=str(task.id),
            # Per-task decomposition, not a system prompt class.
            purpose=None,
            call_category=LLMCallCategory.SYSTEM,
        ):
            return await provider.complete(
                messages, model, tools=[tool_def], config=config
            )
    except DecompositionError:
        raise
    except Exception as exc:
        # A provider/infrastructure failure (network error, exhausted provider
        # retries, malformed response) is not a semantic parse error the
        # self-correction loop can fix by re-prompting. Surface it as a typed
        # DecompositionError so decomposition always terminates inside the
        # domain hierarchy rather than letting a raw provider exception escape
        # to the caller.
        reraise_critical(exc)
        msg = f"LLM decomposition provider call failed for task {task.id!r}"
        logger.warning(
            DECOMPOSITION_FAILED,
            task_id=str(task.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise DecompositionError(msg) from exc


__all__ = ["ask_for_plan", "mangled_reply_hint", "with_retry_context"]
