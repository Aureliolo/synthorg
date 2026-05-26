# module-kind: adapter
"""LLM-based criteria decomposer.

Decomposes acceptance criteria into atomic binary probes by invoking a
structured tool call on a ``CompletionProvider``.  The provider is
expected to invoke the ``emit_atomic_probes`` tool, whose arguments are
strictly validated before being materialized as ``AtomicProbe`` tuples.

Tool-call output is used instead of free-text JSON parsing because every
current provider supports function calling natively, eliminating a whole
class of parse errors, retries on malformed JSON, and prompt-injection
exposure via model-supplied identifiers.  Probe IDs are generated
server-side (never taken from the model).
"""

import json
from collections.abc import Mapping
from typing import Any, ClassVar, Final, NoReturn

from synthorg.budget.call_category import LLMCallCategory

# ``CostTracker`` is part of ``LLMCriteriaDecomposer.__init__``'s public
# annotation, so it must resolve at runtime when downstream tooling
# evaluates type hints (DI containers, doc generators).
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.task import AcceptanceCriterion  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.prompt_safety import (
    TAG_CRITERIA_JSON,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.engine.quality.verification import AtomicProbe
from synthorg.observability import get_logger
from synthorg.observability.events.verification import (
    VERIFICATION_CRITERIA_DECOMPOSED,
    VERIFICATION_DECOMPOSER_CRITERIA_TRUNCATED,
    VERIFICATION_DECOMPOSER_PROBE_REJECTED,
    VERIFICATION_DECOMPOSER_RESPONSE_INVALID,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider  # noqa: TC001

logger = get_logger(__name__)
_DEFAULT_MAX_PROBES_PER_CRITERION: Final[int] = 5

_DECOMPOSER_TOOL_NAME: Final[str] = "emit_atomic_probes"
_DECOMPOSER_TOOL_DESCRIPTION: Final[str] = (
    "Emit a list of atomic binary (yes/no) probes that together verify "
    "whether the given acceptance criteria have been satisfied.  Each "
    "probe must target exactly one criterion by zero-based index."
)
_DECOMPOSER_TOOL_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "probes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_criterion_index": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "probe_text": {"type": "string"},
                },
                "required": ["source_criterion_index", "probe_text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["probes"],
    "additionalProperties": False,
}
_DECOMPOSER_SYSTEM_PROMPT: Final[str] = (
    "You are a verification analyst.  Your job is to break acceptance "
    "criteria into atomic binary probes -- each a single yes/no question "
    "whose answer can be determined by inspecting the produced artifact.  "
    "Favor multiple specific probes over one vague probe.  Do not invent "
    "new requirements; every probe must trace to a given criterion.\n\n"
    + untrusted_content_directive((TAG_CRITERIA_JSON,))
)
_MAX_PROMPT_CRITERIA_CHARS: Final[int] = 8_000
_MIN_CRITERION_DESC_CHARS: Final[int] = 16
_DECOMPOSER_TOOL_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"probes"})


class LLMDecompositionError(DomainError):
    """Raised when the LLM decomposer cannot obtain a valid probe list."""

    default_message: ClassVar[str] = "LLM decomposition failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


def _decomposer_instructions(max_probes: int) -> str:
    """Build the instruction block passed in the user prompt.

    Returns:
        The instruction text constraining the model to call
        ``emit_atomic_probes`` once with at most ``max_probes`` probes
        per criterion.
    """
    return (
        "Call emit_atomic_probes with one array of probe "
        "objects.  Each probe references a criterion by its "
        "zero-based index and asks one yes/no question.  Emit "
        "at least one probe per criterion and at most "
        f"{max_probes} probes per criterion."
    )


def _encode_decomposer_payload(
    descriptions: list[str],
    *,
    max_probes: int,
    instructions: str,
) -> str:
    """Serialize the decomposer payload as a JSON string.

    Each criterion description is wrapped in a ``<criteria-json>``
    fence before JSON encoding. This is the tag explicitly reserved
    for the decomposer-criteria surface in
    ``synthorg.engine.prompt_safety`` -- using the site-specific tag
    (rather than the generic ``<task-data>``) keeps the
    untrusted-content inventory unambiguous for the model. A
    description that contains structural JSON metacharacters
    (``{"description": "...","injected":"true"}``) still serialises
    cleanly through ``json.dumps``, but the fence means the model sees
    an explicit boundary between trusted envelope and untrusted
    criterion text, so a well-crafted breakout string cannot read as
    plausible schema content.

    Returns:
        The JSON-encoded payload sent as the user message body.
    """
    payload = {
        "criteria": [
            {"index": i, "description": wrap_untrusted(TAG_CRITERIA_JSON, d)}
            for i, d in enumerate(descriptions)
        ],
        "max_probes_per_criterion": max_probes,
        "instructions": instructions,
    }
    return json.dumps(payload, ensure_ascii=False)


def _proportionally_truncate(
    descriptions: list[str],
    *,
    overflow: int,
    min_chars: int,
) -> list[str]:
    """Trim descriptions proportionally to their length, respecting min_chars.

    Returns:
        The list of trimmed descriptions in the same order as the
        input, each shortened by a length-proportional amount but
        never below ``min_chars``.
    """
    total_desc_chars = sum(len(d) for d in descriptions) or 1
    per_desc_cuts = [
        max(0, round(len(d) * overflow / total_desc_chars)) for d in descriptions
    ]
    return [
        (d[: max(min_chars, len(d) - cut)] if cut else d)
        for d, cut in zip(descriptions, per_desc_cuts, strict=True)
    ]


def accepted_index(raw: Any) -> int:
    """Return the validated ``source_criterion_index`` from a raw probe.

    Only called after ``_probe_rejection_reason`` returned ``None``,
    so the index is guaranteed to be a valid int in range.
    """
    return int(raw["source_criterion_index"])


def _probe_rejection_reason(
    raw: Any,
    *,
    criteria: tuple[AcceptanceCriterion, ...],
    per_criterion_counts: dict[int, int],
    cap: int,
) -> str | None:
    """Return a short rejection reason or ``None`` if the probe is valid."""
    if not isinstance(raw, dict):
        return "not a dict"
    index = raw.get("source_criterion_index")
    if not isinstance(index, int) or not (0 <= index < len(criteria)):
        return "index out of range"
    probe_text = raw.get("probe_text")
    if not isinstance(probe_text, str) or not probe_text.strip():
        return "blank probe_text"
    if per_criterion_counts[index] >= cap:
        return "per-criterion cap reached"
    return None


class LLMCriteriaDecomposer:
    """Decompose acceptance criteria into probes via a provider tool call.

    The decomposer sends the rubric's acceptance criteria to the model
    along with a strict tool schema.  The model is expected to reply
    with a single ``emit_atomic_probes`` tool invocation; any other
    response shape raises ``LLMDecompositionError``.

    Args:
        provider: Completion provider used for the decomposition call.
            The base class already applies retry and rate limiting.
        model_id: Resolved model identifier for the configured tier.
        max_probes_per_criterion: Upper bound on probes per criterion.
            Extra probes from the model are dropped with a log line.
    """

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model_id: NotBlankStr,
        max_probes_per_criterion: int = _DEFAULT_MAX_PROBES_PER_CRITERION,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        """Store dependencies and enforce a positive cap.

        Raises:
            ValueError: If ``max_probes_per_criterion`` is less than 1.
        """
        if max_probes_per_criterion < 1:
            msg = "max_probes_per_criterion must be >= 1"
            raise ValueError(msg)
        self._provider = provider
        self._model_id = model_id
        self._max_probes_per_criterion = max_probes_per_criterion
        self._cost_tracker = cost_tracker

    @property
    def name(self) -> str:
        """Strategy name."""
        return "llm"

    async def decompose(
        self,
        criteria: tuple[AcceptanceCriterion, ...],
        *,
        task_id: NotBlankStr,
        agent_id: NotBlankStr,
    ) -> tuple[AtomicProbe, ...]:
        """Decompose criteria into atomic probes via the configured LLM.

        Args:
            criteria: Acceptance criteria to decompose.
            task_id: Task identifier (used to stamp deterministic probe IDs).
            agent_id: Agent identifier for logging context.

        Returns:
            Tuple of validated atomic probes.  Empty when ``criteria``
            is empty.

        Raises:
            LLMDecompositionError: If the model does not emit a valid
                ``emit_atomic_probes`` tool call or the returned probes
                do not pass structural validation.
        """
        if not criteria:
            logger.info(
                VERIFICATION_CRITERIA_DECOMPOSED,
                task_id=task_id,
                agent_id=agent_id,
                probe_count=0,
                decomposer=self.name,
                reason="empty criteria",
            )
            return ()

        tool, messages = self._prepare_tool_and_messages(criteria)
        response = await self._invoke_provider(
            messages, tool, agent_id=agent_id, task_id=task_id
        )
        raw_probes = self._extract_raw_probes(
            response,
            task_id=task_id,
            agent_id=agent_id,
        )
        probes = self._materialize_probes(
            raw_probes,
            criteria=criteria,
            task_id=task_id,
            agent_id=agent_id,
        )

        logger.info(
            VERIFICATION_CRITERIA_DECOMPOSED,
            task_id=task_id,
            agent_id=agent_id,
            probe_count=len(probes),
            decomposer=self.name,
        )
        return probes

    def _prepare_tool_and_messages(
        self,
        criteria: tuple[AcceptanceCriterion, ...],
    ) -> tuple[ToolDefinition, list[ChatMessage]]:
        """Build the ``emit_atomic_probes`` tool + system/user messages.

        Returns:
            ``(tool, messages)`` where ``tool`` is the
            :class:`ToolDefinition` registered for the call and
            ``messages`` is the two-message system+user prompt list.
        """
        tool = ToolDefinition(
            name=_DECOMPOSER_TOOL_NAME,
            description=_DECOMPOSER_TOOL_DESCRIPTION,
            parameters_schema=_DECOMPOSER_TOOL_SCHEMA,
        )
        messages = [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=_DECOMPOSER_SYSTEM_PROMPT,
            ),
            ChatMessage(
                role=MessageRole.USER,
                content=self._build_user_prompt(criteria),
            ),
        ]
        return tool, messages

    async def _invoke_provider(
        self,
        messages: list[ChatMessage],
        tool: ToolDefinition,
        *,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
    ) -> Any:
        """Invoke ``self._provider.complete`` with the decomposer config.

        Returns:
            The provider's raw completion response (its ``tool_calls``
            attribute is consumed by :meth:`_extract_raw_probes`).
        """
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=agent_id,
            task_id=task_id,
            call_category=LLMCallCategory.SYSTEM,
        ):
            return await self._provider.complete(
                messages=messages,
                model=self._model_id,
                tools=[tool],
                config=CompletionConfig(temperature=0.0, top_p=1.0, max_tokens=2048),
            )

    def _extract_raw_probes(
        self,
        response: Any,
        *,
        task_id: NotBlankStr,
        agent_id: NotBlankStr,
    ) -> list[Any]:
        """Pull the ``probes`` list from the tool-call response or raise.

        Enforces the full response shape at the system boundary: exactly
        one tool call named ``emit_atomic_probes``, object-typed
        arguments with *only* the ``probes`` key, and a list value.
        Any deviation logs a structured
        ``VERIFICATION_DECOMPOSER_RESPONSE_INVALID`` event with a
        precise reason and raises ``LLMDecompositionError``.

        Returns:
            The raw ``probes`` list from the validated tool call.

        Raises:
            LLMDecompositionError: When the response shape does not
                match the schema (zero / multiple tool calls, missing
                or non-mapping arguments, unexpected keys, or
                non-list ``probes`` value).
        """
        tool_calls = getattr(response, "tool_calls", None) or []
        matches = [tc for tc in tool_calls if tc.name == _DECOMPOSER_TOOL_NAME]
        if len(tool_calls) != 1 or len(matches) != 1:
            reason = "no tool call" if not matches else "multiple tool calls"
            self._raise_invalid_response(
                task_id=task_id,
                agent_id=agent_id,
                reason=reason,
                finish_reason=getattr(
                    getattr(response, "finish_reason", None),
                    "value",
                    None,
                ),
                tool_call_count=len(tool_calls),
                matches=len(matches),
            )

        tool_call = matches[0]
        raw_args = getattr(tool_call, "arguments", None)
        if not isinstance(raw_args, Mapping):
            self._raise_invalid_response(
                task_id=task_id,
                agent_id=agent_id,
                reason="arguments not object",
            )
        arguments: Mapping[str, Any] = raw_args
        extra = set(arguments.keys()) - _DECOMPOSER_TOOL_REQUIRED_KEYS
        if extra:
            self._raise_invalid_response(
                task_id=task_id,
                agent_id=agent_id,
                reason="extra keys in arguments",
                extra_keys=sorted(extra),
            )
        raw_probes = arguments.get("probes")
        if not isinstance(raw_probes, list):
            self._raise_invalid_response(
                task_id=task_id,
                agent_id=agent_id,
                reason="probes not list",
            )
        return raw_probes

    def _raise_invalid_response(
        self,
        *,
        task_id: NotBlankStr,
        agent_id: NotBlankStr,
        reason: str,
        **extra_context: Any,
    ) -> NoReturn:
        """Log ``VERIFICATION_DECOMPOSER_RESPONSE_INVALID`` and raise.

        Raises:
            LLMDecompositionError: Always raised; the function returns
                only via the exception (``NoReturn``).
        """
        logger.error(
            VERIFICATION_DECOMPOSER_RESPONSE_INVALID,
            task_id=task_id,
            agent_id=agent_id,
            decomposer=self.name,
            reason=reason,
            **extra_context,
        )
        msg = f"LLM decomposer response invalid: {reason}"
        raise LLMDecompositionError(msg)

    def _build_user_prompt(
        self,
        criteria: tuple[AcceptanceCriterion, ...],
    ) -> str:
        """Render criteria as an indexed JSON envelope within the size cap.

        Individual criterion descriptions are truncated before JSON
        encoding so the envelope is always syntactically valid and the
        instructions block is preserved.  When truncation happens a
        ``VERIFICATION_DECOMPOSER_CRITERIA_TRUNCATED`` warning is
        logged; when the payload is irreducible even at the per-item
        floor we surface ``LLMDecompositionError`` instead of
        returning oversized text.

        Returns:
            The JSON-encoded user prompt body, post-truncation.

        Raises:
            LLMDecompositionError: If the encoded payload cannot be
                shrunk to fit ``_MAX_PROMPT_CRITERIA_CHARS`` even at
                the per-item floor.
        """
        instructions = _decomposer_instructions(self._max_probes_per_criterion)
        descriptions = [c.description for c in criteria]
        text = _encode_decomposer_payload(
            descriptions,
            max_probes=self._max_probes_per_criterion,
            instructions=instructions,
        )
        if len(text) <= _MAX_PROMPT_CRITERIA_CHARS:
            return text

        truncated = _proportionally_truncate(
            descriptions,
            overflow=len(text) - _MAX_PROMPT_CRITERIA_CHARS,
            min_chars=_MIN_CRITERION_DESC_CHARS,
        )
        text = _encode_decomposer_payload(
            truncated,
            max_probes=self._max_probes_per_criterion,
            instructions=instructions,
        )
        truncated, text = self._iteratively_shrink(
            truncated,
            text,
            instructions=instructions,
            criteria_count=len(criteria),
        )
        truncated_indices = tuple(
            i
            for i, (orig, new) in enumerate(zip(descriptions, truncated, strict=True))
            if orig != new
        )
        logger.warning(
            VERIFICATION_DECOMPOSER_CRITERIA_TRUNCATED,
            decomposer=self.name,
            original_chars=len("".join(descriptions)),
            final_prompt_chars=len(text),
            max_prompt_chars=_MAX_PROMPT_CRITERIA_CHARS,
            truncated_criteria_indices=truncated_indices,
        )
        return text

    def _iteratively_shrink(
        self,
        descriptions: list[str],
        text: str,
        *,
        instructions: str,
        criteria_count: int,
    ) -> tuple[list[str], str]:
        """Shrink descriptions until the encoded payload fits or raise.

        Returns:
            ``(descriptions, text)`` where ``descriptions`` is the
            list shrunk in place per iteration and ``text`` is the
            re-encoded payload that fits ``_MAX_PROMPT_CRITERIA_CHARS``.

        Raises:
            LLMDecompositionError: When every description is already at
                ``_MIN_CRITERION_DESC_CHARS`` and the payload still
                does not fit (the prompt is irreducible).
        """
        while len(text) > _MAX_PROMPT_CRITERIA_CHARS:
            if all(len(d) <= _MIN_CRITERION_DESC_CHARS for d in descriptions):
                logger.error(
                    VERIFICATION_DECOMPOSER_CRITERIA_TRUNCATED,
                    decomposer=self.name,
                    reason="irreducible prompt",
                    criteria_count=criteria_count,
                    final_prompt_chars=len(text),
                    max_prompt_chars=_MAX_PROMPT_CRITERIA_CHARS,
                )
                msg = (
                    "LLM decomposer prompt cannot fit within "
                    f"{_MAX_PROMPT_CRITERIA_CHARS} chars even after "
                    "shrinking every criterion to "
                    f"{_MIN_CRITERION_DESC_CHARS} chars "
                    f"({criteria_count} criteria)"
                )
                raise LLMDecompositionError(msg)
            remaining = max(
                0,
                _MAX_PROMPT_CRITERIA_CHARS
                - (len(text) - sum(len(d) for d in descriptions)),
            )
            per_item_cap = max(
                _MIN_CRITERION_DESC_CHARS,
                remaining // max(1, len(descriptions)),
            )
            descriptions = [d[:per_item_cap] for d in descriptions]
            text = _encode_decomposer_payload(
                descriptions,
                max_probes=self._max_probes_per_criterion,
                instructions=instructions,
            )
        return descriptions, text

    def _materialize_probes(
        self,
        raw_probes: list[Any],
        *,
        criteria: tuple[AcceptanceCriterion, ...],
        task_id: NotBlankStr,
        agent_id: NotBlankStr,
    ) -> tuple[AtomicProbe, ...]:
        """Validate probe dicts and build ``AtomicProbe`` instances.

        Args:
            raw_probes: Probe dicts as returned by the model.
            criteria: Original criteria (for index bounds and source text).
            task_id: Task identifier for deterministic probe IDs.
            agent_id: Agent identifier for log context.

        Returns:
            Validated tuple of ``AtomicProbe`` instances.

        Raises:
            LLMDecompositionError: If no valid probes remain after validation.
        """
        kept: list[AtomicProbe] = []
        per_criterion_counts: dict[int, int] = dict.fromkeys(range(len(criteria)), 0)
        for raw in raw_probes:
            accepted = self._accept_probe(
                raw,
                criteria=criteria,
                per_criterion_counts=per_criterion_counts,
                kept=kept,
                task_id=task_id,
                agent_id=agent_id,
            )
            if accepted is not None:
                kept.append(accepted)
                per_criterion_counts[accepted_index(raw)] += 1

        if not kept:
            self._raise_invalid_response(
                task_id=task_id,
                agent_id=agent_id,
                reason="no valid probes after validation",
            )
        missing_indices = tuple(
            index for index, count in per_criterion_counts.items() if count == 0
        )
        if missing_indices:
            missing_descriptions = tuple(
                criteria[i].description for i in missing_indices
            )
            self._raise_invalid_response(
                task_id=task_id,
                agent_id=agent_id,
                reason="criterion missing probes",
                missing_indices=missing_indices,
                missing_descriptions=missing_descriptions,
            )
        return tuple(kept)

    def _accept_probe(  # noqa: PLR0913
        self,
        raw: Any,
        *,
        criteria: tuple[AcceptanceCriterion, ...],
        per_criterion_counts: dict[int, int],
        kept: list[AtomicProbe],
        task_id: NotBlankStr,
        agent_id: NotBlankStr,
    ) -> AtomicProbe | None:
        """Validate one raw probe dict; return the built probe or ``None``.

        Returns:
            The constructed :class:`AtomicProbe` when the raw entry
            passes :func:`_probe_rejection_reason`, or ``None`` when the
            entry is rejected (logged via
            :data:`VERIFICATION_DECOMPOSER_PROBE_REJECTED`).
        """
        reason = _probe_rejection_reason(
            raw,
            criteria=criteria,
            per_criterion_counts=per_criterion_counts,
            cap=self._max_probes_per_criterion,
        )
        if reason is not None:
            logger.warning(
                VERIFICATION_DECOMPOSER_PROBE_REJECTED,
                task_id=task_id,
                agent_id=agent_id,
                reason=reason,
                index=raw.get("source_criterion_index")
                if isinstance(raw, dict)
                else None,
            )
            return None
        index = int(raw["source_criterion_index"])
        probe_text = str(raw["probe_text"]).strip()
        return AtomicProbe(
            id=f"{task_id}-probe-{len(kept)}",
            probe_text=probe_text,
            source_criterion=criteria[index].description,
        )
