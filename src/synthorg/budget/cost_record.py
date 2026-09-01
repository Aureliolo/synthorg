"""Cost record model for per-API-call tracking.

Implements the Cost Tracking section of ``docs/design/budget.md``:
every API call is tracked as an immutable cost record
(append-only pattern).
"""

import uuid
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.currency import CurrencyCode
from synthorg.core.billing_enums import BillingModel
from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.ontology.decorator import ontology_entity

_NON_TOKEN_BILLED_CATEGORIES: frozenset[LLMCallCategory] = frozenset(
    {LLMCallCategory.IMAGE_GENERATION},
)
"""Call categories billed per output rather than per token, for which a
positive cost with zero token counts is legitimate (not a mis-populated
record)."""


def _new_claim_id() -> NotBlankStr:
    """Generate a fresh per-record idempotency key (UUID4 string).

    Module-level so it can be patched in tests if a deterministic key
    is needed; production callers should accept the default.

    Returns:
        Result of type ``NotBlankStr``.
    """
    return NotBlankStr(str(uuid.uuid4()))


@ontology_entity
class CostRecord(BaseModel):
    """Immutable record of a single API call's cost.

    Once created, a ``CostRecord`` cannot be modified (frozen model).
    This enforces the append-only pattern: new records are created for
    each API call; existing records are never updated.

    Attributes:
        agent_id: Owning agent, or ``None`` for work no agent owns.
        task_id: Owning task, or ``None`` for work that is not a task.
            Both are real entity references: ``task_id`` is a foreign key
            into ``tasks``, so a subsystem call that belongs to no task
            leaves it unset rather than inventing an id. What such a call
            IS gets recorded in ``prompt_class_id`` when it wraps a system
            prompt, and in ``call_category`` when it does not: an embedding
            call has no prompt to classify.
        prompt_class_id: Prompt-class identifier for purpose attribution
            (``None`` when the call wraps no system prompt).
        provider: LLM provider name.
        billing_model: How the provider charged for this call, stamped at
            ingestion from the connection's own declaration. Carried on the
            row for the same reason ``currency`` is: a connection that later
            changes contract must not rewrite the history of what was
            measurable, and a connection since deleted must still be
            answerable. A ``cost`` of zero means two different things without
            it, and only one of them is headroom.
        model: Model identifier.
        input_tokens: Input token count.
        output_tokens: Output token count.
        cost: Numeric cost of the call, denominated in ``currency``.
            Every record carries its own currency so aggregators can
            enforce same-currency invariants without relying on a
            global configuration value; see ``currency`` for the
            accompanying ISO 4217 code.
        currency: ISO 4217 currency code for ``cost``.  See
            :class:`synthorg.budget.currency.CurrencyCode` and the
            ``_KNOWN_ISO4217`` allowlist in ``synthorg.budget.currency``
            for the accepted values; concrete code literals are
            deliberately not listed here so this docstring does not
            drift from the allowlist or privilege a specific region.
        timestamp: Timezone-aware timestamp of the API call.
        call_category: Optional LLM call category (productive,
            coordination, system, embedding, image_generation).
        accuracy_effort_ratio: Accuracy-effort ratio for the task
            this call belongs to (populated at task completion when
            quality signals are available, ``None`` otherwise).
        latency_ms: Round-trip latency in milliseconds (``None`` if not measured).
        cache_read_input_tokens: Input tokens served from a cached prompt
            prefix; zero when the provider reported no cache data.
        cache_write_input_tokens: Input tokens written into the prompt cache.
        retry_count: Number of retry attempts before success (0 = first try succeeded).
        retry_reason: Exception type name of the last retried error.
        finish_reason: LLM finish reason for this call.
        success: Whether the call completed without error or content filter.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Owning agent; None for work no agent owns",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Owning task; None for work that is not a task",
    )
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Project this cost belongs to",
    )
    prompt_class_id: NotBlankStr | None = Field(
        default=None,
        description=(
            "Prompt-class identifier for purpose attribution. When non-null "
            "the value is a PromptPurposeId (e.g. 'system:memory:rerank'); "
            "stored as a free-form string rather than the enum so reads stay "
            "valid across registry additions"
        ),
    )
    provider: NotBlankStr = Field(description="LLM provider name")
    billing_model: BillingModel = Field(
        default=BillingModel.UNKNOWN,
        description=(
            "How the provider charged for this call. Resolved from the "
            "connection's own declaration at ingestion, so a caller cannot "
            "make spend look measurable by asserting it"
        ),
    )
    model: NotBlankStr = Field(description="Model identifier")
    input_tokens: int = Field(ge=0, description="Input token count")
    output_tokens: int = Field(ge=0, description="Output token count")
    cost: float = Field(
        ge=0.0,
        description="Numeric cost of the call, denominated in ``currency``",
    )
    currency: CurrencyCode = Field(
        description="ISO 4217 currency code for ``cost``",
    )
    timestamp: AwareDatetime = Field(description="Timestamp of the API call")
    call_category: LLMCallCategory | None = Field(
        default=None,
        description=(
            "LLM call category (productive, coordination, system, "
            "embedding, image_generation)"
        ),
    )
    accuracy_effort_ratio: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Accuracy-effort ratio for the task this call belongs to "
            "(populated at task completion when quality signals are available)"
        ),
    )
    latency_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Round-trip latency in milliseconds",
    )
    cache_read_input_tokens: int = Field(
        default=0,
        ge=0,
        description=(
            "Input tokens the provider served from a cached prompt prefix. A "
            "count, because the bill is proportional to it; zero when the "
            "provider reported no cache data, which is also what a miss is"
        ),
    )
    cache_write_input_tokens: int = Field(
        default=0,
        ge=0,
        description="Input tokens the provider wrote into its prompt cache",
    )
    retry_count: int | None = Field(
        default=None,
        ge=0,
        description="Number of retry attempts before success",
    )
    retry_reason: str | None = Field(
        default=None,
        description="Exception type name of the last retried error",
    )
    finish_reason: FinishReason | None = Field(
        default=None,
        description="LLM finish reason for this call",
    )
    success: bool | None = Field(
        default=None,
        description="Whether the call completed without error or content filter",
    )
    claim_id: NotBlankStr = Field(
        default_factory=_new_claim_id,
        description=(
            "Idempotency key for this billing event. Generated once at "
            "construction (UUID4 by default) so retries / JetStream "
            "redelivery / in-process tracker double-submission cannot "
            "double-bill: ``CostTracker.record`` keeps a bounded LRU "
            "of seen ``claim_id`` values and treats repeats as no-ops. "
            "The durable key is ``(claim_id, timestamp)`` rather than "
            "``claim_id`` alone, because the Postgres table is a "
            "TimescaleDB hypertable whose unique index must include the "
            "partitioning column. That is equivalent only while "
            "``timestamp`` belongs to the event: a redelivery re-sends "
            "this same immutable record, so both halves repeat. A "
            "producer that re-stamped ``timestamp`` at send time would "
            "be minting a second billing event under one claim, and the "
            "index would let it through."
        ),
    )

    @model_validator(mode="after")
    def _validate_token_consistency(self) -> Self:
        """Ensure positive cost implies at least one non-zero token count.

        Exempts non-token-billed modalities (currently image generation),
        which legitimately charge per output (per image) with zero token
        counts; for token-priced calls a positive cost with no tokens is a
        mis-populated record.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if (
            self.cost > 0
            and self.input_tokens == 0
            and self.output_tokens == 0
            and self.call_category not in _NON_TOKEN_BILLED_CATEGORIES
        ):
            msg = "cost is positive but both token counts are zero"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_retry_consistency(self) -> Self:
        """Ensure retry_reason and retry_count are consistent.

        If a retry reason is set, at least one retry must have occurred.
        If retry_count is zero or unset, there can be no retry reason.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.retry_reason is not None and (
            self.retry_count is None or self.retry_count == 0
        ):
            msg = "retry_reason set implies retry_count must be >= 1"
            raise ValueError(msg)
        return self
