# module-kind: service
"""Chief of Staff chat interface for natural language explanations.

Provides LLM-powered explanations of proposals, alerts, and
free-form signal questions. Uses ``CompletionProvider`` for
LLM calls (retry + rate limiting handled by the provider).
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import ClassVar

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.currency import format_cost

# These types appear in public annotations of ``ChiefOfStaffChat``
# (constructor + ``explain_proposal`` / ``explain_alert`` / ``ask``)
# so they must resolve at runtime when downstream tooling evaluates
# type hints (DI containers, doc generators).
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_CONFIG_VALUE,
    TAG_TASK_DATA,
    wrap_untrusted,
)
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import (
    Alert,
    ChatAnswerComplete,
    ChatAnswerDelta,
    ChatQuery,
    ChatResponse,
)
from synthorg.meta.chief_of_staff.prompts import (
    ALERT_EXPLANATION_SYSTEM,
    ALERT_EXPLANATION_USER,
    CHAT_QUERY_SYSTEM,
    CHAT_QUERY_USER,
    PROPOSAL_EXPLANATION_SYSTEM,
    PROPOSAL_EXPLANATION_USER,
)
from synthorg.meta.chief_of_staff.protocol import OutcomeStore
from synthorg.meta.models import (
    ImprovementProposal,
    OrgSignalSnapshot,
)
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.chief_of_staff import (
    COS_CHAT_FAILED,
    COS_CHAT_QUERY,
    COS_CHAT_RESPONSE,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole, StreamEventType
from synthorg.providers.errors import ProviderTimeoutError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import (
    require_configured_model,
    resolve_model_with_fallback,
)
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


class ChiefOfStaffChat:
    """Natural language interface for proposal/alert explanations.

    Formats context from proposals, alerts, and signal snapshots
    into prompt templates and calls an LLM for conversational
    explanations.

    Args:
        provider: LLM completion provider.
        config: Chief of Staff configuration.
        outcome_store: Outcome store for historical context (optional).
    """

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.COS_CHAT

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        config: ChiefOfStaffConfig,
        outcome_store: OutcomeStore | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._outcome_store = outcome_store
        self._cost_tracker = cost_tracker
        self._config_resolver = config_resolver

    async def explain_proposal(
        self,
        proposal: ImprovementProposal,
        snapshot: OrgSignalSnapshot,
    ) -> ChatResponse:
        """Explain why a proposal was generated.

        Args:
            proposal: The proposal to explain.
            snapshot: Current signal snapshot for context.

        Returns:
            Natural language explanation.
        """
        logger.info(
            COS_CHAT_QUERY,
            query_type="proposal_explanation",
            proposal_id=str(proposal.id),
        )
        approval_ctx = "No historical data available."
        if self._outcome_store is not None and proposal.source_rule is not None:
            stats = await self._outcome_store.get_stats(
                proposal.source_rule,
                proposal.altitude,
            )
            if stats is not None:
                approval_ctx = (
                    f"Historical approval rate for rule "
                    f"'{stats.rule_name}': {stats.approval_rate:.0%} "
                    f"({stats.approved_count}/{stats.total_proposals} "
                    f"proposals approved)"
                )
        user = PROPOSAL_EXPLANATION_USER.format(
            proposal_title=wrap_untrusted(TAG_CONFIG_VALUE, proposal.title),
            proposal_description=wrap_untrusted(
                TAG_CONFIG_VALUE,
                proposal.description,
            ),
            proposal_rationale=wrap_untrusted(
                TAG_CONFIG_VALUE,
                proposal.rationale.signal_summary,
            ),
            # Confidence is a model-produced float; trusted numeric field.
            proposal_confidence=f"{proposal.confidence:.2f}",
            rule_name=wrap_untrusted(
                TAG_CONFIG_VALUE,
                proposal.source_rule or "manual",
            ),
            # Severity is a rule match property, not carried on proposals.
            rule_severity="N/A",
            signal_context=wrap_untrusted(
                TAG_TASK_DATA,
                _format_snapshot(snapshot),
            ),
            approval_context=wrap_untrusted(TAG_TASK_DATA, approval_ctx),
        )
        return await self._call_llm(
            PROPOSAL_EXPLANATION_SYSTEM,
            user,
            sources=("performance", "budget"),
        )

    async def explain_alert(
        self,
        alert: Alert,
        snapshot: OrgSignalSnapshot,  # noqa: ARG002
    ) -> ChatResponse:
        """Explain what triggered an alert.

        Args:
            alert: The alert to explain.
            snapshot: Accepted for API consistency with
                ``explain_proposal``; not used because the alert
                already carries its own signal context.

        Returns:
            Natural language explanation.
        """
        logger.info(
            COS_CHAT_QUERY,
            query_type="alert_explanation",
            alert_id=str(alert.id),
        )
        user = ALERT_EXPLANATION_USER.format(
            alert_type=wrap_untrusted(TAG_CONFIG_VALUE, alert.alert_type),
            # severity is a typed enum -- trusted.
            alert_severity=alert.severity.value,
            affected_domains=wrap_untrusted(
                TAG_CONFIG_VALUE,
                ", ".join(alert.affected_domains),
            ),
            signal_context=wrap_untrusted(
                TAG_TASK_DATA,
                _format_signal_context(alert.signal_context),
            ),
        )
        sources = tuple(alert.affected_domains)
        return await self._call_llm(
            ALERT_EXPLANATION_SYSTEM,
            user,
            sources=sources,
        )

    async def ask(
        self,
        query: ChatQuery,
        snapshot: OrgSignalSnapshot,
        *,
        scoped_proposal: ApprovalItem | None = None,
    ) -> ChatResponse:
        """Answer a free-form question about signals/proposals.

        A full :class:`ImprovementProposal` cannot be reconstructed from
        the approval queue (rationale / rollback plan / change tuples
        don't survive into it), so a ``proposal_id``-scoped question
        does not route to :meth:`explain_proposal`; instead the resolved
        :class:`ApprovalItem` is folded into this free-form answer's
        context.

        Args:
            query: The user's question.
            snapshot: Current signal snapshot for context.
            scoped_proposal: The approval-queue item the question is
                scoped to, when ``query.proposal_id`` resolved to one.

        Returns:
            Natural language response.
        """
        logger.info(
            COS_CHAT_QUERY,
            query_type="free_form",
            question_length=len(query.question),
            has_proposal_id=query.proposal_id is not None,
            has_alert_id=query.alert_id is not None,
        )
        user = await self._build_query_user(query, snapshot, scoped_proposal)
        return await self._call_llm(CHAT_QUERY_SYSTEM, user, sources=())

    async def ask_stream(
        self,
        query: ChatQuery,
        snapshot: OrgSignalSnapshot,
    ) -> AsyncGenerator[ChatAnswerDelta | ChatAnswerComplete]:
        """Stream a free-form answer token-by-token, then a terminal event.

        The streamed path mirrors :meth:`ask`'s free-form prompt (proposal
        / alert deep-explain stays on the buffered endpoint, since those
        produce short structured answers where streaming buys nothing). It
        yields a :class:`ChatAnswerDelta` per content chunk in arrival
        order, then one :class:`ChatAnswerComplete` carrying the assembled
        answer. The free-form path attributes no ``sources`` and computes
        no ``confidence``, so the terminal event carries the empty-sources /
        default-confidence defaults (the buffered free-form ``ask`` behaves
        identically; only the scoped deep-explain paths populate them).

        Yields:
            Zero or more deltas, then exactly one terminal complete event.

        Raises:
            ProviderTimeoutError: When the provider stalls past
                ``agent_call_timeout_seconds`` opening the stream or
                between two content chunks (retryable 504).
            Exception: Propagated from the provider stream (criticals
                re-raised; others redacted-logged before re-raise).
        """
        logger.info(
            COS_CHAT_QUERY,
            query_type="free_form_stream",
            question_length=len(query.question),
        )
        user = await self._build_query_user(query, snapshot, scoped_proposal=None)
        messages, config, model = await self._prepare_messages(CHAT_QUERY_SYSTEM, user)
        timeout = self._config.agent_call_timeout_seconds
        parts: list[str] = []
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=NotBlankStr("system"),
                task_id=NotBlankStr("system:cos:chat"),
                purpose=self.metadata.prompt_class_id,
                call_category=LLMCallCategory.SYSTEM,
            ):
                stream = await asyncio.wait_for(
                    self._provider.stream(messages, model, config=config),
                    timeout=timeout,
                )
                # Per-chunk (inter-token) timeout, not one bound on the whole
                # stream: a slow-but-live provider must not trip, only a stall.
                # lint-allow: long-running-loop-kill-switch -- bounded by StopAsyncIteration; the per-chunk asyncio.wait_for trips on a stall and aclosing cancels the generator on client disconnect  # noqa: E501
                while True:
                    try:
                        chunk = await asyncio.wait_for(anext(stream), timeout=timeout)
                    except StopAsyncIteration:
                        break
                    if (
                        chunk.event_type == StreamEventType.CONTENT_DELTA
                        and chunk.content
                    ):
                        parts.append(chunk.content)
                        yield ChatAnswerDelta(delta=chunk.content)
        except TimeoutError as exc:
            # asyncio.wait_for raises the builtin TimeoutError, not a
            # DomainError: type it so the client sees a retryable 504 rather
            # than an opaque in-stream fault while the stream stays open.
            log_exception_redacted(logger, COS_CHAT_FAILED, exc)
            msg = "Chief of Staff chat stream timed out"
            raise ProviderTimeoutError(msg) from exc
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(logger, COS_CHAT_FAILED, exc)
            raise
        answer = "".join(parts).strip()
        if not answer:
            logger.warning(COS_CHAT_FAILED, reason="provider_returned_empty_content")
            answer = "Unable to generate explanation."
        logger.info(COS_CHAT_RESPONSE, answer_length=len(answer), sources=[])
        yield ChatAnswerComplete(answer=NotBlankStr(answer))

    async def _build_query_user(
        self,
        query: ChatQuery,
        snapshot: OrgSignalSnapshot,
        scoped_proposal: ApprovalItem | None,
    ) -> str:
        """Render the fenced USER message for a free-form question.

        Reads recent outcomes for context (degrading to a placeholder on
        a store read failure) and folds a resolved ``scoped_proposal``
        summary ahead of them, then fences every attacker-controllable
        field in a ``<task-data>`` envelope.

        Returns:
            The rendered, fully fenced USER-role message.
        """
        recent_context = "No recent proposals or alerts."
        if self._outcome_store is not None:
            try:
                recent = await self._outcome_store.recent_outcomes(limit=5)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(COS_CHAT_FAILED, reason="outcome_store_read_failed")
                recent = ()
            if recent:
                lines = [
                    f"- {o.title} ({o.decision}, {o.decided_at:%Y-%m-%d})"
                    for o in recent
                ]
                recent_context = "Recent outcomes:\n" + "\n".join(lines)
        if scoped_proposal is not None:
            recent_context = (
                f"{_format_scoped_proposal(scoped_proposal)}\n\n{recent_context}"
            )
        return CHAT_QUERY_USER.format(
            snapshot_summary=wrap_untrusted(TAG_TASK_DATA, _format_snapshot(snapshot)),
            recent_context=wrap_untrusted(TAG_TASK_DATA, recent_context),
            user_question=wrap_untrusted(TAG_TASK_DATA, query.question),
        )

    async def _prepare_messages(
        self,
        system: str,
        user: str,
    ) -> tuple[list[ChatMessage], CompletionConfig, str]:
        """Assemble the messages, completion config, and resolved model.

        Shared by the buffered (:meth:`_call_llm`) and streaming
        (:meth:`ask_stream`) paths so both resolve the ``chat_model``
        kill-switch identically.

        Returns:
            A ``(messages, config, model)`` triple.
        """
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system),
            ChatMessage(role=MessageRole.USER, content=user),
        ]
        config = CompletionConfig(
            temperature=self._config.chat_temperature,
            max_tokens=self._config.chat_max_tokens,
        )
        model = require_configured_model(
            await resolve_model_with_fallback(
                resolver=self._config_resolver,
                namespace=SettingNamespace.CHIEF_OF_STAFF,
                key="chat_model",
                fallback=self._config.chat_model or "",
            ),
            namespace=SettingNamespace.CHIEF_OF_STAFF,
            key="chat_model",
            feature_label="Chief of Staff chat",
        )
        return messages, config, model

    async def _call_llm(
        self,
        system: str,
        user: str,
        *,
        sources: tuple[str, ...],
    ) -> ChatResponse:
        """Call the LLM and wrap the response.

        The untrusted-content directive is carried by ``system`` so it runs
        at system priority; ``user`` holds only the fenced attacker-controllable
        fields, keeping the directive's authority above the fenced data.

        Args:
            system: SYSTEM-role framing + untrusted-content directive.
            user: USER-role message with the fenced data fields.
            sources: Signal domains referenced.

        Returns:
            Wrapped ChatResponse.

        Raises:
            ProviderTimeoutError: When the provider call exceeds
                ``agent_call_timeout_seconds`` (retryable 504).
            Exception: Raised on the corresponding failure path.
        """
        messages, config, model = await self._prepare_messages(system, user)
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=NotBlankStr("system"),
                task_id=NotBlankStr("system:cos:chat"),
                purpose=self.metadata.prompt_class_id,
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await asyncio.wait_for(
                    self._provider.complete(
                        messages,
                        model,
                        config=config,
                    ),
                    timeout=self._config.agent_call_timeout_seconds,
                )
        except TimeoutError as exc:
            # asyncio.wait_for raises the builtin TimeoutError, not a
            # DomainError: type it so the client sees a retryable 504 rather
            # than an opaque 500.
            log_exception_redacted(logger, COS_CHAT_FAILED, exc)
            msg = "Chief of Staff chat call timed out"
            raise ProviderTimeoutError(msg) from exc
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(logger, COS_CHAT_FAILED, exc)
            raise
        answer = (response.content or "").strip()
        if not answer:
            logger.warning(
                COS_CHAT_FAILED,
                reason="provider_returned_empty_content",
            )
            answer = "Unable to generate explanation."
        result = ChatResponse(
            answer=answer,
            sources=tuple(sources),
        )
        logger.info(
            COS_CHAT_RESPONSE,
            answer_length=len(answer),
            sources=list(sources),
        )
        return result


def _format_snapshot(snapshot: OrgSignalSnapshot) -> str:
    """Format a snapshot into a readable summary string.

    Returns:
        Resulting string.
    """
    perf = snapshot.performance
    budget = snapshot.budget
    coord = snapshot.coordination
    lines = [
        f"Quality: {perf.avg_quality_score:.1f}/10",
        f"Success Rate: {perf.avg_success_rate:.0%}",
        f"Collaboration: {perf.avg_collaboration_score:.1f}/10",
        f"Active Agents: {perf.agent_count}",
        f"Total Spend: {format_cost(budget.total_spend)}",
        f"Orchestration Overhead: {budget.orchestration_overhead:.2f}",
        f"Error Findings: {snapshot.errors.total_findings}",
    ]
    if coord.coordination_overhead_pct is not None:
        lines.append(
            f"Coordination Overhead: {coord.coordination_overhead_pct:.0%}",
        )
    return "\n".join(lines)


def _format_scoped_proposal(item: ApprovalItem) -> str:
    """Format a scoped approval-queue item into readable context.

    ``ApprovalItem`` is the durable projection a proposal survives into
    once parked for human review; it carries title/description plus
    whatever the submitting path recorded in ``metadata`` (altitude,
    source rule), not the full ``ImprovementProposal``.

    Returns:
        Formatted context lines describing the scoped proposal.
    """
    lines = [
        "The user's question is scoped to this pending proposal:",
        f"Title: {item.title}",
        f"Description: {item.description}",
        f"Status: {item.status.value}",
    ]
    altitude = item.metadata.get("altitude")
    if altitude:
        lines.append(f"Altitude: {altitude}")
    source_rule = item.metadata.get("source_rule")
    if source_rule:
        lines.append(f"Source rule: {source_rule}")
    return "\n".join(lines)


def _format_signal_context(ctx: dict[str, object]) -> str:
    """Format a signal context dict into readable lines.

    Returns:
        Resulting string.
    """
    return "\n".join(f"{k}: {v}" for k, v in ctx.items())
