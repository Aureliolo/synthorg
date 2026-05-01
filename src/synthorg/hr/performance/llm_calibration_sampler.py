"""LLM-based calibration sampling for collaboration scoring.

Periodically samples a configurable fraction (default 1%) of collaboration
interactions and has an LLM evaluate them independently.  Results are stored
as calibration records for drift analysis against the behavioral strategy.
"""

import json
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.hr.performance.models import LlmCalibrationRecord
from synthorg.observability import get_logger
from synthorg.observability.events.performance import (
    PERF_LLM_SAMPLE_COMPLETED,
    PERF_LLM_SAMPLE_FAILED,
    PERF_LLM_SAMPLE_STARTED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.budget.tracker import CostTracker
    from synthorg.hr.performance.models import CollaborationMetricRecord
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

#: Static head of the calibration prompt (no user-controlled data).
#:
#: The prompt body is composed at message-build time from this header,
#: a metrics block (bounded numeric fields), and a ``wrap_untrusted``
#: fence around the free-form ``interaction_summary``. Keeping the
#: header constant lets prompt-fingerprint tests pin it.
_SYSTEM_PROMPT_HEADER = (
    "You are evaluating the quality of collaboration in an AI agent "
    "interaction.\n\n"
    "Given the interaction summary and behavioral metrics below, rate "
    "the overall collaboration quality on a scale of 0.0 to 10.0.\n\n"
    "Respond with JSON only: "
    '{"score": <float>, "rationale": "<brief explanation>"}\n\n'
    + untrusted_content_directive((TAG_TASK_DATA,))
)

_COMPLETION_CONFIG = CompletionConfig(temperature=0.3, max_tokens=256)


class LlmCalibrationSampler:
    """Periodic LLM sampling of collaboration interactions for calibration.

    Samples a configurable fraction of collaboration events and has an
    LLM evaluate them independently.  Results are stored as calibration
    records for drift analysis against the behavioral strategy.

    Args:
        provider: Completion provider for LLM calls.
        model: Model identifier to use for sampling.
        sampling_rate: Fraction of events to sample (0.0-1.0).
        retention_days: Days to retain calibration records.

    Raises:
        ValueError: If sampling_rate or retention_days are out of bounds.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        provider: CompletionProvider,
        model: NotBlankStr,
        sampling_rate: float = 0.01,
        retention_days: int = 90,
        currency: CurrencyCode = DEFAULT_CURRENCY,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        if not (0.0 <= sampling_rate <= 1.0):
            msg = f"sampling_rate must be in [0.0, 1.0], got {sampling_rate}"
            raise ValueError(msg)
        if retention_days < 1:
            msg = f"retention_days must be >= 1, got {retention_days}"
            raise ValueError(msg)
        self._provider = provider
        self._model = str(model)
        self._sampling_rate = sampling_rate
        self._retention_days = retention_days
        self._currency = currency
        self._cost_tracker = cost_tracker
        self._records: dict[str, list[LlmCalibrationRecord]] = {}

    def should_sample(self) -> bool:
        """Determine whether to sample the current event.

        Returns:
            ``True`` if a random draw falls below the sampling rate.
        """
        return random.random() < self._sampling_rate  # noqa: S311

    async def sample(
        self,
        *,
        record: CollaborationMetricRecord,
        behavioral_score: float,
    ) -> LlmCalibrationRecord | None:
        """Sample and evaluate a collaboration interaction via LLM.

        Skips records without ``interaction_summary``.  Provider failures
        are caught and logged -- this is best-effort calibration.

        Args:
            record: The collaboration metric record to evaluate.
            behavioral_score: The behavioral strategy's score for context.

        Returns:
            A calibration record, or ``None`` on skip/failure.
        """
        if record.interaction_summary is None:
            return None

        self._prune_expired()

        logger.debug(
            PERF_LLM_SAMPLE_STARTED,
            agent_id=record.agent_id,
            record_id=record.id,
        )

        try:
            llm_score, rationale, cost = await self._call_llm(record)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                PERF_LLM_SAMPLE_FAILED,
                agent_id=record.agent_id,
                record_id=record.id,
                exc_info=True,
            )
            return None

        calibration_record = LlmCalibrationRecord(
            agent_id=record.agent_id,
            sampled_at=datetime.now(UTC),
            interaction_record_id=record.id,
            llm_score=llm_score,
            behavioral_score=behavioral_score,
            rationale=NotBlankStr(rationale),
            model_used=NotBlankStr(self._model),
            cost=cost,
            currency=self._currency,
        )

        agent_key = str(record.agent_id)
        if agent_key not in self._records:
            self._records[agent_key] = []
        self._records[agent_key].append(calibration_record)

        logger.info(
            PERF_LLM_SAMPLE_COMPLETED,
            agent_id=record.agent_id,
            llm_score=llm_score,
            behavioral_score=behavioral_score,
            drift=calibration_record.drift,
        )
        return calibration_record

    def get_calibration_records(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: AwareDatetime | None = None,
    ) -> tuple[LlmCalibrationRecord, ...]:
        """Query stored calibration records.

        Expired records (older than ``retention_days``) are pruned
        before filtering.

        Args:
            agent_id: Filter by agent (``None`` = all agents).
            since: Include records after this time.

        Returns:
            Matching calibration records.
        """
        self._prune_expired()

        if agent_id is not None:
            records = list(self._records.get(str(agent_id), []))
        else:
            records = [r for recs in self._records.values() for r in recs]

        if since is not None:
            records = [r for r in records if r.sampled_at >= since]

        return tuple(records)

    def get_drift_summary(
        self,
        agent_id: NotBlankStr,
    ) -> float | None:
        """Compute average drift for an agent.

        Expired records (older than ``retention_days``) are pruned
        before aggregation.

        Args:
            agent_id: Agent to compute drift for.

        Returns:
            Average drift, or ``None`` if no calibration records exist.
        """
        self._prune_expired()

        records = self._records.get(str(agent_id), [])
        if not records:
            return None
        return round(sum(r.drift for r in records) / len(records), 4)

    def _build_user_prompt(self, record: CollaborationMetricRecord) -> str:
        """Build the user-message body for the LLM evaluation call.

        Bounded behavioural metrics (numeric scores, booleans) are
        rendered as a metadata block; the free-form
        ``interaction_summary`` (the only attacker-controllable field)
        is wrapped via :func:`wrap_untrusted` under
        :data:`TAG_TASK_DATA`. The SEC-1 instructional directive
        lives in the SYSTEM message (see :data:`_SYSTEM_PROMPT_HEADER`)
        rather than the user payload so an attacker-controlled
        summary cannot dilute the directive's authority.

        Pre-PR review finding (#1682, CodeRabbit critical at
        ``llm_calibration_sampler.py:55``): the prior implementation
        prepended ``_SYSTEM_PROMPT_HEADER`` to the user message,
        sending the SEC-1 directive at user-priority instead of
        system-priority -- which left the call site prompt-injectable
        even though the summary was fenced.
        """

        def _display(val: object) -> str:
            return "not observed" if val is None else str(val)

        metrics_block = (
            "Behavioral metrics (for reference, not the sole basis for "
            "your score):\n"
            f"- delegation_success: {_display(record.delegation_success)}\n"
            f"- delegation_response_seconds: "
            f"{_display(record.delegation_response_seconds)}\n"
            f"- conflict_constructiveness: "
            f"{_display(record.conflict_constructiveness)}\n"
            f"- meeting_contribution: "
            f"{_display(record.meeting_contribution)}\n"
            f"- loop_triggered: {record.loop_triggered}\n"
            f"- handoff_completeness: "
            f"{_display(record.handoff_completeness)}"
        )
        wrapped_summary = wrap_untrusted(
            TAG_TASK_DATA,
            str(record.interaction_summary),
        )
        return f"{metrics_block}\n\nInteraction summary:\n{wrapped_summary}"

    def _parse_llm_response(
        self,
        raw_content: str,
        record: CollaborationMetricRecord,
    ) -> tuple[float, str]:
        """Parse and validate the LLM JSON response.

        Args:
            raw_content: Raw LLM response text.
            record: Source record (for log context on failure).

        Returns:
            Tuple of (score, rationale).

        Raises:
            ValueError: On parse failure, out-of-range score, or
                blank rationale.
        """
        try:
            parsed = json.loads(raw_content)
            score = float(parsed["score"])
            rationale = str(parsed["rationale"])[:2048].strip()
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(
                PERF_LLM_SAMPLE_FAILED,
                agent_id=record.agent_id,
                record_id=record.id,
                reason="parse_error",
                raw_content=raw_content[:500],
            )
            msg = f"Failed to parse LLM response: {exc}"
            raise ValueError(msg) from exc

        max_score = 10.0
        if not (0.0 <= score <= max_score):
            logger.warning(
                PERF_LLM_SAMPLE_FAILED,
                agent_id=record.agent_id,
                record_id=record.id,
                reason="out_of_range",
                llm_score=score,
                raw_content=raw_content[:500],
            )
            msg = f"LLM score {score} outside valid range [0, 10]"
            raise ValueError(msg)

        if not rationale:
            logger.warning(
                PERF_LLM_SAMPLE_FAILED,
                agent_id=record.agent_id,
                record_id=record.id,
                reason="blank_rationale",
                raw_content=raw_content[:500],
            )
            msg = "LLM returned blank rationale"
            raise ValueError(msg)

        return score, rationale

    async def _call_llm(
        self,
        record: CollaborationMetricRecord,
    ) -> tuple[float, str, float]:
        """Call the LLM and return parsed evaluation results.

        Returns:
            Tuple of (score, rationale, cost).

        Raises:
            ValueError: If the LLM response is empty, cannot be parsed
                (missing keys, malformed JSON), contains an
                out-of-range score, or has a blank rationale.
        """
        user_prompt = self._build_user_prompt(record)

        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=record.agent_id,
            task_id=NotBlankStr(f"system:hr:calibration:{record.id}"),
            call_category=LLMCallCategory.SYSTEM,
            # Pin the scope to the same currency the
            # ``LlmCalibrationRecord`` is stamped with so the chokepoint's
            # ``CostRecord`` and the calibration record never disagree
            # under the same-currency invariant.
            currency=self._currency,
        ):
            response = await self._provider.complete(
                messages=[
                    # SEC-1 (#1682): the untrusted-content directive
                    # belongs in a SYSTEM-role message so the model
                    # treats it as instruction with higher priority
                    # than the USER-role payload that carries the
                    # attacker-controllable interaction summary.
                    ChatMessage(
                        role=MessageRole.SYSTEM,
                        content=_SYSTEM_PROMPT_HEADER,
                    ),
                    ChatMessage(
                        role=MessageRole.USER,
                        content=user_prompt,
                    ),
                ],
                model=self._model,
                config=_COMPLETION_CONFIG,
            )

        if response.content is None:
            logger.warning(
                PERF_LLM_SAMPLE_FAILED,
                agent_id=record.agent_id,
                record_id=record.id,
                reason="LLM returned no content",
            )
            msg = "LLM returned no content"
            raise ValueError(msg)

        score, rationale = self._parse_llm_response(
            response.content,
            record,
        )
        return score, rationale, response.usage.cost

    def _prune_expired(self) -> None:
        """Remove calibration records older than the retention period."""
        cutoff = datetime.now(UTC) - timedelta(days=self._retention_days)
        for agent_key in list(self._records):
            self._records[agent_key] = [
                r for r in self._records[agent_key] if r.sampled_at >= cutoff
            ]
            if not self._records[agent_key]:
                del self._records[agent_key]
