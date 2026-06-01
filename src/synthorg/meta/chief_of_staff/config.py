"""Configuration for Chief of Staff advanced capabilities.

Defines frozen Pydantic config for proposal outcome learning,
proactive alerts, and the chat interface. All capabilities are
opt-in with safe defaults (disabled).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import RuleSeverity

# Sampling temperature stays low (0.3) so the clarify/propose path
# emits deterministic JSON structure rather than discursive text; the
# 0.0/2.0 bounds mirror the provider-agnostic sampler range every
# runtime integration passes through.
_PROPOSE_TEMPERATURE_DEFAULT: float = 0.3
_PROPOSE_TEMPERATURE_MIN: float = 0.0
_PROPOSE_TEMPERATURE_MAX: float = 2.0
# 2000 tokens fits a JSON payload of up to ~5 clarify+propose items
# (the per-turn fan-out cap below) without truncation on typical
# large-capability models; 100 is the floor below which even a minimal
# clarifying question would not fit.
_PROPOSE_MAX_TOKENS_DEFAULT: int = 2000
_PROPOSE_MAX_TOKENS_MIN: int = 100
# Five proposals per turn bounds the approval-queue fan-out a single
# conversation turn can create; the 1..20 envelope is the same range
# the model_validator on ProposeDecision enforces for the model's own
# JSON output.
_PROPOSE_MAX_PROPOSALS_DEFAULT: int = 5
_PROPOSE_MAX_PROPOSALS_MIN: int = 1
_PROPOSE_MAX_PROPOSALS_MAX: int = 20
# Five clarifying turns is the cap before the conversation force-closes
# with _CAP_MESSAGE; 1..20 is the same envelope as the per-turn cap so
# operators tuning one routinely tune the other in tandem.
_PROPOSE_MAX_CLARIFICATION_DEFAULT: int = 5
_PROPOSE_MAX_CLARIFICATION_MIN: int = 1
_PROPOSE_MAX_CLARIFICATION_MAX: int = 20
# Concern routing runs a deterministic classification pass (temperature
# 0.0) so the topic/role/confidence JSON is stable; the 0.0/2.0 bounds
# mirror the same provider-agnostic sampler range as the propose path.
_ROUTING_TEMPERATURE_DEFAULT: float = 0.0
# A classification reply is a single small JSON object (topic + role +
# confidence); 200 tokens fits it comfortably and 50 is the floor below
# which even that minimal object risks truncation.
_ROUTING_MAX_TOKENS_DEFAULT: int = 200
_ROUTING_MAX_TOKENS_MIN: int = 50
# Below 0.6 classifier confidence the request falls back to the generic
# Chief of Staff rather than routing to a possibly-wrong role; the
# 0.0/1.0 envelope is the natural probability range.
_ROUTING_CONFIDENCE_FLOOR_DEFAULT: float = 0.6
# Group chat: one human, several agents, round-robin turns. The
# defaults below bound a single human turn so it cannot drive unbounded
# fan-out cost; all are operator-tunable.
# Five participants is a sensible default fan-out for a working group; 2
# is the floor for a meaningful multi-agent round and 10 caps the
# per-round LLM call count.
_GROUP_MAX_PARTICIPANTS_DEFAULT: int = 5
_GROUP_MAX_PARTICIPANTS_MIN: int = 2
_GROUP_MAX_PARTICIPANTS_MAX: int = 10
# 12000 tokens covers a five-agent round (prompt + reply per agent) with
# headroom; 1000 is the floor below which even a single contribution
# plus its reserve would not fit.
_GROUP_ROUND_TOKEN_BUDGET_DEFAULT: int = 12000
_GROUP_ROUND_TOKEN_BUDGET_MIN: int = 1000
# A round keeps a 20% reserve so the budget check trips before the
# tracker is fully drained, leaving margin for the in-flight call's
# input tokens; 0.0..0.9 is the usable reserve envelope.
_GROUP_TOKEN_RESERVE_RATIO_DEFAULT: float = 0.2
_GROUP_TOKEN_RESERVE_RATIO_MAX: float = 0.9
# 1500 output tokens per contribution keeps one verbose agent from
# starving the rest of the round; 100 is the floor for a usable reply.
_GROUP_PER_AGENT_MAX_TOKENS_DEFAULT: int = 1500
_GROUP_PER_AGENT_MAX_TOKENS_MIN: int = 100
# 60 total turns bounds the conversation's lifetime growth (one human
# turn plus several agent turns per round, over several rounds); 2 is
# the floor (one human + one agent) and 500 a generous ceiling.
_GROUP_MAX_TOTAL_TURNS_DEFAULT: int = 60
_GROUP_MAX_TOTAL_TURNS_MIN: int = 2
_GROUP_MAX_TOTAL_TURNS_MAX: int = 500
# A single conversational agent LLM call (a group-chat contribution or a
# clarify/propose decision) runs while the per-conversation lock is held,
# so a hung provider connection (TCP alive, no bytes) would stall every
# queued turn on that conversation indefinitely. This wall-clock bound is
# the backstop the provider's own (optional) timeout may not supply: 120s
# covers a slow large-model reply plus the provider retry budget; 5s is
# the floor below which a legitimate slow reply would be cut off.
_AGENT_CALL_TIMEOUT_SECONDS_DEFAULT: float = 120.0
_AGENT_CALL_TIMEOUT_SECONDS_MIN: float = 5.0
_AGENT_CALL_TIMEOUT_SECONDS_MAX: float = 600.0
# Agent-initiated invites: an agent may request to bring another
# agent in, gated by human consent. Two invites parked per round bounds
# the consent-queue storm a single round can create; 1 is the floor and
# 5 a generous ceiling that still stays well under the participant cap.
_INVITE_MAX_PER_ROUND_DEFAULT: int = 2
_INVITE_MAX_PER_ROUND_MIN: int = 1
_INVITE_MAX_PER_ROUND_MAX: int = 5
# Direct MCP acting: a chat instruction drives a real MCP action
# under the agent's trust level. Six turns bounds a short act/observe
# loop (request approval -> on grant perform the action -> confirm); 1
# is the floor and 20 a generous ceiling past which the loop is clearly
# stuck rather than working.
_DIRECT_MCP_MAX_TURNS_DEFAULT: int = 6
_DIRECT_MCP_MAX_TURNS_MIN: int = 1
_DIRECT_MCP_MAX_TURNS_MAX: int = 20


class ChiefOfStaffConfig(BaseModel):
    """Configuration for Chief of Staff advanced capabilities.

    Three capability groups, all opt-in:

    - **Learning**: Track proposal approval/rejection patterns,
      adjust future proposal confidence scores.
    - **Alerts**: Detect org-level signal inflections between
      scheduled cycles, emit proactive alerts.
    - **Chat**: LLM-powered natural language explanations of
      proposals, alerts, and signal interactions.

    Attributes:
        learning_enabled: Enable proposal outcome learning.
        adjuster_strategy: Confidence adjustment algorithm.
        ema_alpha: Blend factor for EMA adjuster (0 = full
            history, 1 = full base confidence).
        min_outcomes: Minimum decision count before adjusting.
        alerts_enabled: Enable proactive org-level alerts.
        inflection_check_interval_minutes: Minutes between
            inflection detection checks.
        inflection_severity_threshold: Minimum severity to
            emit an alert.
        chat_enabled: Enable the chat explanation interface.
        chat_model: LLM model identifier for chat responses.
        chat_temperature: Sampling temperature for chat.
        chat_max_tokens: Token budget for chat responses.
        propose_enabled: Enable the clarify-and-propose interface
            (``/meta/chat/propose``). Independent of ``chat_enabled``.
        propose_model: LLM model identifier for clarify/propose turns.
        propose_temperature: Sampling temperature for propose turns.
        propose_max_tokens: Token budget for a propose turn.
        propose_max_proposals_per_turn: Upper bound on work items a
            single turn may emit (bounds approval-queue fan-out).
        propose_max_clarification_turns: Maximum clarifying questions
            before the model must either propose or yield a terminal
            turn (prevents an unbounded clarify loop).
        propose_default_risk_level: Risk level stamped on the approval
            item created for each proposed work item.
        routing_enabled: Enable concern-routing in front of the
            clarify-and-propose loop. When off, every turn is answered by
            the generic Chief of Staff persona (v1 behaviour).
        routing_strategy: Which ``RoleRouter`` strategy to build:
            ``"llm"`` (a concern classifier) or ``"keyword"`` (a static
            keyword-to-role map).
        routing_model: LLM model identifier for the concern classifier.
        routing_temperature: Sampling temperature for the classifier.
        routing_max_tokens: Token budget for one classification reply.
        routing_confidence_floor: Minimum classifier confidence (0-1) to
            route to a role; below it the turn falls back to the generic
            Chief of Staff.
        routing_default_role: Role to try when the classifier is
            confident but names a role with no active agent; falls back
            to the generic persona when that role is also absent.
        group_chat_enabled: Enable the multi-agent group chat
            (``/meta/chat/group``). When off, the controller 503s.
        group_chat_max_participants: Maximum agents in one group
            conversation (bounds per-round fan-out).
        group_chat_round_token_budget: Total token budget for one
            round-robin round across all participants.
        group_chat_token_reserve_ratio: Fraction of the round budget
            held back so the budget check trips before the tracker is
            fully drained.
        group_chat_per_agent_max_tokens: Output-token cap for a single
            participant's contribution (stops one agent starving the
            round).
        group_chat_max_total_turns: Maximum total turns a single group
            conversation may accumulate over its lifetime.
        agent_call_timeout_seconds: Wall-clock cap for a single
            conversational agent LLM call (a group-chat contribution or a
            clarify/propose decision). The call runs while the
            per-conversation lock is held, so this bound stops a hung
            provider from stalling the conversation indefinitely.
        invite_enabled: Enable agent-initiated invites in group chat
            (an agent may request to bring another agent in, gated by
            human consent). When off, contributions stay plain text.
        invite_max_per_round: Maximum invites an agent may park behind
            consent in a single round (storm/loop bound).
        invite_default_risk_level: Risk level stamped on the consent
            approval item raised for an agent-initiated invite.
        direct_mcp_enabled: Enable direct MCP acting under trust
            (``/meta/chat/act``): a chat instruction drives a real MCP
            action under the acting agent's trust level, with sensitive
            actions gated to the approval queue. When off, the
            controller 503s.
        direct_mcp_max_turns: Hard turn cap for one chat-driven action
            loop (bounds the act/observe fan-out a single instruction
            can drive).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    # ── Learning ──────────────────────────────────────────────────

    learning_enabled: bool = False
    adjuster_strategy: Literal["ema", "bayesian"] = "ema"
    ema_alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    min_outcomes: int = Field(default=3, ge=1)

    # ── Proactive alerts ──────────────────────────────────────────

    alerts_enabled: bool = False
    inflection_check_interval_minutes: int = Field(default=15, ge=5)
    inflection_severity_threshold: RuleSeverity = RuleSeverity.WARNING

    # ── Chat ──────────────────────────────────────────────────────

    chat_enabled: bool = False
    chat_model: NotBlankStr = Field(
        default=NotBlankStr("example-small-001"),
        description="Model for chat explanation LLM calls",
    )
    chat_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    chat_max_tokens: int = Field(default=2000, ge=100)

    # ── Clarify + propose ─────────────────────────────────────────

    propose_enabled: bool = False
    propose_model: NotBlankStr = Field(
        default=NotBlankStr("example-small-001"),
        description="Model for clarify-and-propose LLM calls",
    )
    propose_temperature: float = Field(
        default=_PROPOSE_TEMPERATURE_DEFAULT,
        ge=_PROPOSE_TEMPERATURE_MIN,
        le=_PROPOSE_TEMPERATURE_MAX,
    )
    propose_max_tokens: int = Field(
        default=_PROPOSE_MAX_TOKENS_DEFAULT,
        ge=_PROPOSE_MAX_TOKENS_MIN,
    )
    propose_max_proposals_per_turn: int = Field(
        default=_PROPOSE_MAX_PROPOSALS_DEFAULT,
        ge=_PROPOSE_MAX_PROPOSALS_MIN,
        le=_PROPOSE_MAX_PROPOSALS_MAX,
    )
    propose_max_clarification_turns: int = Field(
        default=_PROPOSE_MAX_CLARIFICATION_DEFAULT,
        ge=_PROPOSE_MAX_CLARIFICATION_MIN,
        le=_PROPOSE_MAX_CLARIFICATION_MAX,
    )
    propose_default_risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM

    # ── Concern routing ───────────────────────────────────

    routing_enabled: bool = False
    routing_strategy: Literal["llm", "keyword"] = "llm"
    routing_model: NotBlankStr = Field(
        default=NotBlankStr("example-small-001"),
        description="Model for the concern-routing classifier LLM calls",
    )
    routing_temperature: float = Field(
        default=_ROUTING_TEMPERATURE_DEFAULT,
        ge=_PROPOSE_TEMPERATURE_MIN,
        le=_PROPOSE_TEMPERATURE_MAX,
    )
    routing_max_tokens: int = Field(
        default=_ROUTING_MAX_TOKENS_DEFAULT,
        ge=_ROUTING_MAX_TOKENS_MIN,
    )
    routing_confidence_floor: float = Field(
        default=_ROUTING_CONFIDENCE_FLOOR_DEFAULT,
        ge=0.0,
        le=1.0,
    )
    routing_default_role: NotBlankStr = Field(
        default=NotBlankStr("CEO"),
        description="Role to try when a confident classification names "
        "a role with no active agent",
    )

    # ── Multi-agent group chat ────────────────────────────

    group_chat_enabled: bool = False
    group_chat_max_participants: int = Field(
        default=_GROUP_MAX_PARTICIPANTS_DEFAULT,
        ge=_GROUP_MAX_PARTICIPANTS_MIN,
        le=_GROUP_MAX_PARTICIPANTS_MAX,
    )
    group_chat_round_token_budget: int = Field(
        default=_GROUP_ROUND_TOKEN_BUDGET_DEFAULT,
        ge=_GROUP_ROUND_TOKEN_BUDGET_MIN,
    )
    group_chat_token_reserve_ratio: float = Field(
        default=_GROUP_TOKEN_RESERVE_RATIO_DEFAULT,
        ge=0.0,
        le=_GROUP_TOKEN_RESERVE_RATIO_MAX,
    )
    group_chat_per_agent_max_tokens: int = Field(
        default=_GROUP_PER_AGENT_MAX_TOKENS_DEFAULT,
        ge=_GROUP_PER_AGENT_MAX_TOKENS_MIN,
    )
    group_chat_max_total_turns: int = Field(
        default=_GROUP_MAX_TOTAL_TURNS_DEFAULT,
        ge=_GROUP_MAX_TOTAL_TURNS_MIN,
        le=_GROUP_MAX_TOTAL_TURNS_MAX,
    )
    agent_call_timeout_seconds: float = Field(
        default=_AGENT_CALL_TIMEOUT_SECONDS_DEFAULT,
        ge=_AGENT_CALL_TIMEOUT_SECONDS_MIN,
        le=_AGENT_CALL_TIMEOUT_SECONDS_MAX,
    )

    # ── Agent-initiated invite ────────────────────────────

    invite_enabled: bool = False
    invite_max_per_round: int = Field(
        default=_INVITE_MAX_PER_ROUND_DEFAULT,
        ge=_INVITE_MAX_PER_ROUND_MIN,
        le=_INVITE_MAX_PER_ROUND_MAX,
    )
    invite_default_risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM

    # ── Direct MCP acting under trust ─────────────────────

    direct_mcp_enabled: bool = False
    direct_mcp_max_turns: int = Field(
        default=_DIRECT_MCP_MAX_TURNS_DEFAULT,
        ge=_DIRECT_MCP_MAX_TURNS_MIN,
        le=_DIRECT_MCP_MAX_TURNS_MAX,
    )
