"""Chief of Staff chat interface for natural language explanations.

Provides LLM-powered explanations of proposals, alerts, and
free-form signal questions. Uses ``CompletionProvider`` for
LLM calls (retry + rate limiting handled by the provider).
"""

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.currency import DEFAULT_CURRENCY

# These types appear in public annotations of ``ChiefOfStaffChat``
# (constructor + ``explain_proposal`` / ``explain_alert`` / ``ask``)
# so they must resolve at runtime when downstream tooling evaluates
# type hints (DI containers, doc generators).
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_CONFIG_VALUE,
    TAG_TASK_DATA,
    wrap_untrusted,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig  # noqa: TC001
from synthorg.meta.chief_of_staff.models import (
    Alert,
    ChatQuery,
    ChatResponse,
)
from synthorg.meta.chief_of_staff.prompts import (
    ALERT_EXPLANATION_PROMPT,
    CHAT_QUERY_PROMPT,
    PROPOSAL_EXPLANATION_PROMPT,
)
from synthorg.meta.chief_of_staff.protocol import OutcomeStore  # noqa: TC001
from synthorg.meta.models import (  # noqa: TC001
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
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider  # noqa: TC001

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

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        config: ChiefOfStaffConfig,
        outcome_store: OutcomeStore | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._outcome_store = outcome_store
        self._cost_tracker = cost_tracker

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
        prompt = PROPOSAL_EXPLANATION_PROMPT.format(
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
        return await self._call_llm(prompt, sources=("performance", "budget"))

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
        prompt = ALERT_EXPLANATION_PROMPT.format(
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
        return await self._call_llm(prompt, sources=sources)

    async def ask(
        self,
        query: ChatQuery,
        snapshot: OrgSignalSnapshot,
    ) -> ChatResponse:
        """Answer a free-form question about signals/proposals.

        Args:
            query: The user's question.
            snapshot: Current signal snapshot for context.

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
        recent_context = "No recent proposals or alerts."
        if self._outcome_store is not None:
            try:
                recent = await self._outcome_store.recent_outcomes(limit=5)
            except Exception:
                logger.warning(
                    COS_CHAT_FAILED,
                    reason="outcome_store_read_failed",
                )
                recent = ()
            if recent:
                lines = [
                    f"- {o.title} ({o.decision}, {o.decided_at:%Y-%m-%d})"
                    for o in recent
                ]
                recent_context = "Recent outcomes:\n" + "\n".join(lines)
        prompt = CHAT_QUERY_PROMPT.format(
            snapshot_summary=wrap_untrusted(
                TAG_TASK_DATA,
                _format_snapshot(snapshot),
            ),
            recent_context=wrap_untrusted(TAG_TASK_DATA, recent_context),
            user_question=wrap_untrusted(TAG_TASK_DATA, query.question),
        )
        return await self._call_llm(prompt, sources=())

    async def _call_llm(
        self,
        prompt: str,
        *,
        sources: tuple[str, ...],
    ) -> ChatResponse:
        """Call the LLM and wrap the response.

        Args:
            prompt: Formatted prompt string.
            sources: Signal domains referenced.

        Returns:
            Wrapped ChatResponse.
        """
        messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
        config = CompletionConfig(
            temperature=self._config.chat_temperature,
            max_tokens=self._config.chat_max_tokens,
        )
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=NotBlankStr("system"),
                task_id=NotBlankStr("system:cos:chat"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._config.chat_model,
                    config=config,
                )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
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
    """Format a snapshot into a readable summary string."""
    perf = snapshot.performance
    budget = snapshot.budget
    coord = snapshot.coordination
    lines = [
        f"Quality: {perf.avg_quality_score:.1f}/10",
        f"Success Rate: {perf.avg_success_rate:.0%}",
        f"Collaboration: {perf.avg_collaboration_score:.1f}/10",
        f"Active Agents: {perf.agent_count}",
        f"Total Spend: {budget.total_spend:.2f} {DEFAULT_CURRENCY}",
        f"Orchestration Overhead: {budget.orchestration_overhead:.2f}",
        f"Error Findings: {snapshot.errors.total_findings}",
    ]
    if coord.coordination_overhead_pct is not None:
        lines.append(
            f"Coordination Overhead: {coord.coordination_overhead_pct:.0%}",
        )
    return "\n".join(lines)


def _format_signal_context(ctx: dict[str, object]) -> str:
    """Format a signal context dict into readable lines."""
    return "\n".join(f"{k}: {v}" for k, v in ctx.items())
