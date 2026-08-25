"""LLM-based task decomposition strategy.

Uses an LLM provider with tool calling to break a task into subtasks.
Falls back to parsing JSON from content when tool calls are absent.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

# ``CostTrackerProtocol``, ``CompletionProvider``, ``Task``,
# ``DecompositionContext``, ``DecompositionPlan``, and
# ``CompletionResponse`` appear in public annotations of
# ``LlmDecompositionStrategy`` (constructor + ``decompose``), so they
# must resolve at runtime when downstream tooling evaluates type hints
# (DI containers, doc generators).
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._atomicity_gate import describe_unsplittable
from synthorg.engine.decomposition._llm_retry import (
    ask_for_plan,
    mangled_reply_hint,
    with_retry_context,
)
from synthorg.engine.decomposition.context import (
    DecompositionContext,
    depth_budget,
    width_budget,
)
from synthorg.engine.decomposition.llm_parse import (
    parse_content_response,
    parse_tool_call_response,
)
from synthorg.engine.decomposition.llm_prompt import (
    build_decomposition_tool,
    build_system_message,
    build_task_message,
)
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.errors import (
    DecompositionBudgetExhaustedError,
    DecompositionDepthError,
    DecompositionError,
    DecompositionSubtaskLimitError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_ATOMICITY_CORRECTION_REQUESTED,
    DECOMPOSITION_COMPLETED,
    DECOMPOSITION_FAILED,
    DECOMPOSITION_LLM_ARGUMENTS_MANGLED,
    DECOMPOSITION_LLM_CALL_COMPLETE,
    DECOMPOSITION_LLM_CALL_START,
    DECOMPOSITION_LLM_PARSE_ERROR,
    DECOMPOSITION_LLM_RETRY,
    DECOMPOSITION_VALIDATION_ERROR,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.kill_switch import resolve_int_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_DECOMPOSITION_NS: Final[str] = "coordination"
_MAX_OUTPUT_TOKENS_KEY: Final[str] = "decomposition_max_output_tokens"
_MAX_RETRIES_KEY: Final[str] = "decomposition_max_retries"

#: How many transport-mangled replies are re-asked for without spending one of
#: the operator's planning attempts. Small and fixed rather than settings-backed
#: because it bounds a fault nobody configures: a provider mangling every reply
#: is a broken provider, and paying the whole retry ladder to establish that
#: would cost exactly what this exists to save.
_MAX_MANGLED_ROUNDS: Final[int] = 2


def _reject_unsplittable(
    plan: DecompositionPlan, context: DecompositionContext, *, task_id: str
) -> None:
    """Hand a too-coarse last level back to the session to widen.

    Args:
        plan: The plan as submitted.
        context: The level it was planned under, carrying the size signal
            only where there is no depth left to split into.
        task_id: The task being decomposed, for the log line.

    Raises:
        DecompositionError: Some unit is still more than one agent's work and
            no further level is available, so the correction is to produce
            more units at this one.
    """
    oversized = describe_unsplittable(plan.subtasks, policy=context.atomicity)
    if oversized is None:
        return
    logger.info(
        DECOMPOSITION_ATOMICITY_CORRECTION_REQUESTED,
        task_id=task_id,
        current_depth=context.current_depth,
    )
    raise DecompositionError(oversized)


class LlmDecompositionConfig(BaseModel):
    """Configuration for the LLM decomposition strategy.

    Attributes:
        max_retries: Maximum retry attempts on parse failure.
        temperature: Sampling temperature for the LLM call.
        max_output_tokens: Maximum tokens for the LLM response.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    # Default AND ceiling match `coordination.decomposition_max_retries`,
    # because the live setting is what actually decides the count: a ceiling
    # that disagrees lets an operator write a value this refuses, and a default
    # that disagrees plans one number of attempts when the settings store is
    # reachable and another when it is not.
    max_retries: int = Field(default=5, ge=0, le=8, description="Max retry attempts")
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    max_output_tokens: int = Field(
        # Matches `coordination.decomposition_max_output_tokens`. The fallback
        # a resolver-down read lands on has to be the value the registry would
        # have answered, or the same deployment plans against two different
        # ceilings depending on whether the settings store was reachable.
        default=32_768,
        gt=0,
        description="Max output tokens",
    )


class LlmDecompositionStrategy:
    """Decomposition strategy that uses an LLM to generate plans.

    Sends the task details to an LLM provider with a tool
    definition for structured output. Falls back to parsing
    JSON from content if tool calls are absent. Retries on
    parse/validation failures up to ``max_retries`` times.
    """

    __slots__ = ("_config", "_cost_tracker", "_model", "_provider", "_resolver")

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: str,
        config: LlmDecompositionConfig | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        """Initialize the LLM decomposition strategy.

        Args:
            provider: LLM completion provider for making calls.
            model: Model identifier to use for decomposition.
            config: Optional strategy configuration. Uses defaults
                if not provided.
            cost_tracker: Optional CostTracker reference; when wired
                each LLM call records via the chokepoint.
            config_resolver: Optional resolver read once per decomposition for
                the output-token ceiling. Read per call rather than baked in
                at construction, because raising the ceiling is what an
                operator does IN RESPONSE to a truncation, and a value fixed
                at assembly would not apply until the next rebuild.

        Raises:
            ValueError: If model is blank.
        """
        if not model or not model.strip():
            msg = "model must be a non-blank string"
            logger.warning(DECOMPOSITION_FAILED, error=msg)
            raise ValueError(msg)
        self._provider = provider
        self._model = model
        self._config = config or LlmDecompositionConfig()
        self._cost_tracker = cost_tracker
        self._resolver = config_resolver

    async def _max_retries(self) -> int:
        """How many times a refused plan may be re-asked for.

        Read live for the same reason the ceiling is: the right number depends
        on the bound model, which an operator changes without a restart. Each
        attempt is a SELF-CORRECTION, not a repeat of the same request (the
        prior error is fed back), so attempts are not interchangeable: a run
        that failed three times on three DIFFERENT faults was converging and
        ran out of budget, which is not the same as a model that cannot do it.

        Returns:
            The operator's current setting, or the configured default when no
            resolver is wired (the harnesses and tests construct without one)
            and when a resolver read fails: this runs BEFORE the attempt loop
            and outside the handler that promises every failure leaves as a
            ``DecompositionError``, so a settings-store blip would otherwise
            escape untyped and spend none of the attempts it was sizing.
        """
        return await resolve_int_with_fallback(
            resolver=self._resolver,
            namespace=_DECOMPOSITION_NS,
            key=_MAX_RETRIES_KEY,
            fallback=self._config.max_retries,
        )

    async def _max_output_tokens(self) -> int:
        """The output-token ceiling this decomposition runs under.

        Returns:
            The operator's current setting, or the configured default when no
            resolver is wired and when a resolver read fails, for the reason
            :meth:`_max_retries` gives.
        """
        return await resolve_int_with_fallback(
            resolver=self._resolver,
            namespace=_DECOMPOSITION_NS,
            key=_MAX_OUTPUT_TOKENS_KEY,
            fallback=self._config.max_output_tokens,
        )

    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        """Decompose a task into subtasks using an LLM.

        Args:
            task: The parent task to decompose.
            context: Decomposition constraints.

        Returns:
            A decomposition plan with subtask definitions.

        Raises:
            DecompositionDepthError: If current depth meets or
                exceeds max depth.
            DecompositionSubtaskLimitError: If a planned plan exceeds
                ``max_subtasks``; raised on the first attempt that does,
                so the produced count and the ceiling reach the caller.
            DecompositionBudgetExhaustedError: The model stopped at its token
                ceiling before writing content; raised on the first attempt,
                because every later one truncates at the same place.
            DecompositionError: If all retries are exhausted or
                the plan violates constraints.
        """
        self._check_depth(context)

        messages = self._build_initial_messages(task, context)
        tool_def = build_decomposition_tool(context.available_roles)
        comp_config = CompletionConfig(
            temperature=self._config.temperature,
            max_tokens=await self._max_output_tokens(),
        )

        last_error: str | None = None
        last_response: CompletionResponse | None = None
        attempts = 1 + await self._max_retries()
        attempt = 0
        mangled_rounds = 0

        # See docs/reference/retry-patterns.md: Pattern B -- semantic
        # self-correction. Each attempt re-prompts the LLM with prior-
        # attempt context; no temporal backoff between iterations.
        #
        # A while loop rather than a range, because not every round is an
        # attempt: a reply the transport mangled (see `_mangled_arguments`)
        # never carried a plan to judge, so spending one of the operator's
        # planning attempts on it charges the model for a fault upstream of it.
        # Bounded separately below so a provider mangling every reply cannot
        # loop.
        while attempt < attempts:
            # `last_error is not None` IS "this is not the first round": it
            # starts unset and every continue above sets it, so a separate
            # round counter would carry no information this does not.
            if last_error is not None:
                logger.info(
                    DECOMPOSITION_LLM_RETRY,
                    task_id=str(task.id),
                    attempt=attempt,
                    error=last_error,
                )
                messages = with_retry_context(messages, last_response, last_error)

            logger.debug(
                DECOMPOSITION_LLM_CALL_START,
                task_id=str(task.id),
                model=self._model,
                attempt=attempt,
            )

            response = await ask_for_plan(
                provider=self._provider,
                model=self._model,
                cost_tracker=self._cost_tracker,
                task=task,
                messages=messages,
                tool_def=tool_def,
                config=comp_config,
            )
            last_response = response

            logger.debug(
                DECOMPOSITION_LLM_CALL_COMPLETE,
                task_id=str(task.id),
                finish_reason=response.finish_reason.value,
            )

            try:
                plan = self._parse_response(
                    response,
                    str(task.id),
                    context.available_roles,
                    tuple(
                        NotBlankStr(criterion.description)
                        for criterion in task.acceptance_criteria
                    ),
                )
                # Asked here rather than after the loop, so a level that came
                # back too coarse is corrected on the channel that already
                # re-prompts, spending breadth where depth has run out.
                _reject_unsplittable(plan, context, task_id=str(task.id))
            except DecompositionBudgetExhaustedError:
                # Not retried: the ceiling is the same on the next attempt, so
                # every further call truncates at the same place and the run
                # pays the full retry ladder to learn nothing. Raised with the
                # condition it names, rather than collapsed into a
                # retries-exhausted error that says only that parsing failed.
                raise
            except DecompositionError as exc:
                mangled = mangled_reply_hint(response)
                if mangled is not None and mangled_rounds < _MAX_MANGLED_ROUNDS:
                    # Not an attempt: the reply never carried a plan, and the
                    # correction it needs is about serialisation rather than
                    # about the plan, so `last_error` becomes the re-issue
                    # instruction instead of a schema error naming a field the
                    # model filled in correctly.
                    mangled_rounds += 1
                    last_error = mangled
                    logger.warning(
                        DECOMPOSITION_LLM_ARGUMENTS_MANGLED,
                        task_id=str(task.id),
                        attempt=attempt,
                        mangled_rounds=mangled_rounds,
                    )
                    continue
                attempt += 1
                last_error = safe_error_description(exc)
                logger.warning(
                    DECOMPOSITION_LLM_PARSE_ERROR,
                    task_id=str(task.id),
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    error=last_error,
                )
                continue

            # Propagates rather than retrying. The one condition this refuses
            # carries the produced count and the ceiling as attributes, so a
            # caller can offer to raise the limit to the number actually
            # planned; another attempt would replace that with a bare
            # retries-exhausted error and lose both numbers.
            self._validate_plan(plan, context)

            # INFO, like every other state transition: a plan existing where
            # none did is the outcome, and logging it below the retry that
            # preceded it puts the attempt above the result.
            logger.info(
                DECOMPOSITION_COMPLETED,
                task_id=str(task.id),
                strategy="llm",
                subtask_count=len(plan.subtasks),
            )
            return plan

        # Carries the final cause, because the attempt count alone cannot
        # separate the two failures this path now serves: a model that kept
        # planning badly and a transport that kept mangling replies are fixed
        # in different places, and this message is the only artefact a caller
        # sees. Each intermediate attempt logs its own reason, but nobody
        # reading a raised error has those to hand.
        cause = f": {last_error}" if last_error else ""
        mangled = (
            f", plus {mangled_rounds} mangled transport "
            f"{'reply' if mangled_rounds == 1 else 'replies'}"
            if mangled_rounds
            else ""
        )
        msg = (
            f"LLM decomposition retries exhausted after "
            f"{attempts} attempts for task {task.id!r}{mangled}{cause}"
        )
        # Structured rather than the assembled message: the three facts a
        # reader needs are separately queryable, and ``last_error`` is already
        # the redacted description its own handler produced.
        logger.warning(
            DECOMPOSITION_FAILED,
            task_id=str(task.id),
            attempts=attempts,
            mangled_rounds=mangled_rounds,
            error=last_error,
        )
        raise DecompositionError(msg)

    def get_strategy_name(self) -> str:
        """Return the strategy name."""
        return "llm"

    def plans_any_task(self) -> bool:
        """Plan any task: every call sends the task it was handed.

        Returns:
            ``True``, always.
        """
        return True

    @staticmethod
    def _check_depth(context: DecompositionContext) -> None:
        """Raise if depth limit is reached.

        Args:
            context: Decomposition constraints.

        Raises:
            DecompositionDepthError: If current depth meets or
                exceeds max depth.
        """
        if context.current_depth >= depth_budget(context):
            msg = (
                f"Decomposition depth {context.current_depth} "
                f"meets or exceeds max depth {depth_budget(context)}"
            )
            logger.warning(DECOMPOSITION_VALIDATION_ERROR, error=msg)
            raise DecompositionDepthError(msg)

    @staticmethod
    def _build_initial_messages(
        task: Task,
        context: DecompositionContext,
    ) -> list[ChatMessage]:
        """Build the initial system + task messages.

        Args:
            task: The parent task.
            context: Decomposition constraints.

        Returns:
            List of initial chat messages.
        """
        return [
            build_system_message(context.available_roles),
            build_task_message(task, context),
        ]

    @staticmethod
    def _parse_response(
        response: CompletionResponse,
        parent_task_id: str,
        available_roles: tuple[NotBlankStr, ...],
        objective_criteria: tuple[NotBlankStr, ...] = (),
    ) -> DecompositionPlan:
        """Parse a plan from tool calls, content fallback, or raise.

        Args:
            response: The LLM completion response.
            parent_task_id: ID of the parent task.
            available_roles: The roles the org staffs, which every owner must
                be drawn from.
            objective_criteria: The acceptance criteria of the task being
                decomposed, which the plan must advance at least one of.

        Returns:
            A parsed ``DecompositionPlan``.

        Raises:
            DecompositionBudgetExhaustedError: The model stopped at its token
                ceiling before writing any content.
            DecompositionError: If both parsing paths fail.
        """
        if response.tool_calls:
            return parse_tool_call_response(
                response, parent_task_id, available_roles, objective_criteria
            )
        # Checked BEFORE the content path, because a reasoning model that ran
        # out of budget returns an empty string rather than nothing, which
        # reaches the JSON parser and is reported as malformed JSON. The two
        # have opposite fixes (a larger budget against a better prompt) and the
        # parse error names the wrong one. Observed on a hosted reasoning model
        # that spent all 300 completion tokens on `reasoning` and returned
        # content of length zero.
        if response.finish_reason is FinishReason.MAX_TOKENS and not response.content:
            msg = (
                "the model stopped at its token ceiling before writing any "
                "content, which a reasoning model does when its budget is "
                "spent on reasoning; raise the decomposition model's "
                "max_output_tokens rather than rewording the prompt"
            )
            logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
            raise DecompositionBudgetExhaustedError(msg)
        if response.content is not None:
            return parse_content_response(
                response, parent_task_id, available_roles, objective_criteria
            )
        msg = "Response has no tool calls and no content"
        logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
        raise DecompositionError(msg)

    @staticmethod
    def _validate_plan(
        plan: DecompositionPlan,
        context: DecompositionContext,
    ) -> None:
        """Validate plan against context constraints.

        Args:
            plan: The parsed decomposition plan.
            context: Decomposition constraints.

        Raises:
            DecompositionSubtaskLimitError: If subtask count exceeds limit.
        """
        if len(plan.subtasks) > width_budget(context):
            over_limit = DecompositionSubtaskLimitError(
                produced=len(plan.subtasks), limit=width_budget(context)
            )
            logger.warning(
                DECOMPOSITION_VALIDATION_ERROR,
                subtask_count=over_limit.produced,
                max_subtasks=over_limit.limit,
                error=safe_error_description(over_limit),
            )
            raise over_limit
