# module-kind: declarative
"""Frozen domain + config models for the output-style policy subsystem.

Two layers share one pluggable pack:

* the **soft** house-style directive (:class:`HouseStyleDirective`) injected
  into agent prompts, scoped org-wide / per-role / per-department via the same
  :class:`~synthorg.engine.strategy.active_principle.ScopeKind` used by
  constitutional principles;
* the **hard** deterministic rule pack (:class:`OutputStyleRule`) enforced at
  every agent-output boundary, with three per-rule enforcement modes and an
  operator-sanctioned exemption model (:class:`SanctionedExemption`).

The hard path never calls an LLM. Banned literals (the em-dash U+2014 and its
HTML-entity forms) are expressed in the YAML packs by integer codepoint and a
convenience flag, expanded to literals in the loader, so the repo's
``check_no_em_dashes.py`` gate never sees a literal in committed source.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.active_principle import ALL_SCOPE, ScopeKind

# ── Enums ──────────────────────────────────────────────────────


class RuleType(StrEnum):
    """How a hard rule matches offending output."""

    LITERAL_BAN = "literal_ban"
    REGEX_BAN = "regex_ban"


class EnforcementMode(StrEnum):
    """What happens when a hard rule matches.

    ``REJECT_REWORK`` fails closed and routes the output back to the producing
    agent. ``SHADOW`` computes and surfaces the finding but never blocks (for
    fuzzy heuristics). ``AUTO_REWRITE`` applies a deterministic safe transform
    in prose spans only; a match inside a code span downgrades to
    ``REJECT_REWORK`` because a punctuation swap could corrupt code.
    """

    REJECT_REWORK = "reject_rework"
    SHADOW = "shadow"
    AUTO_REWRITE = "auto_rewrite"


class RuleSeverity(StrEnum):
    """Audit / display severity of a hard rule (does not drive blocking)."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ExemptionScopeKind(StrEnum):
    """Dimension a sanctioned exemption is keyed on.

    Operator-authored; an agent is granted an exemption only when its output
    context matches one of these scopes for the offending rule.
    """

    PATH = "path"
    TASK_TYPE = "task_type"
    PROJECT = "project"
    DEPARTMENT = "department"
    ROLE = "role"
    DELIVERABLE_TAG = "deliverable_tag"


class OutputChannel(StrEnum):
    """The agent-output boundary being evaluated.

    ``COMMIT_MESSAGE`` and ``CODE_FILE`` are treated as code (strict reject, no
    auto-rewrite); the prose channels embed code only inside fences / inline
    spans, which the segmenter isolates.
    """

    DELIVERABLE = "deliverable"
    MESSAGE = "message"
    COMMIT_MESSAGE = "commit_message"
    PR_BODY = "pr_body"
    CODE_FILE = "code_file"


class SegmentKind(StrEnum):
    """Whether a span of output is natural-language prose or code/data."""

    PROSE = "prose"
    CODE = "code"


#: Channels whose entire content is treated as code (no auto-rewrite).
CODE_CHANNELS: frozenset[OutputChannel] = frozenset(
    {OutputChannel.COMMIT_MESSAGE, OutputChannel.CODE_FILE}
)

#: Sentinel rule id in an exemption meaning "every rule".
ALL_RULES: NotBlankStr = NotBlankStr("*")


# ── Soft layer ─────────────────────────────────────────────────


class HouseStyleDirective(BaseModel):
    """A single house-style directive line injected into the system prompt.

    Attributes:
        id: Unique directive identifier within a pack.
        text: The directive text rendered into the prompt.
        scope: ``"all"`` or a role / department name (per ``scope_kind``).
        scope_kind: Whether ``scope`` names the whole org, a role, or a dept.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique directive identifier")
    text: NotBlankStr = Field(description="Directive text injected into prompts")
    scope: NotBlankStr = Field(default=ALL_SCOPE, description="'all' or role/dept name")
    scope_kind: ScopeKind = Field(
        default=ScopeKind.ALL,
        description="Whether scope targets the org, a role, or a department",
    )

    @model_validator(mode="after")
    def _validate_scope_consistency(self) -> Self:
        """Reject a ``scope`` / ``scope_kind`` pairing that cannot match.

        Returns:
            The validated instance.

        Raises:
            ValueError: When ``ALL`` is not paired with the ``all`` sentinel.
        """
        is_all_kind = self.scope_kind is ScopeKind.ALL
        is_all_scope = self.scope == ALL_SCOPE
        if is_all_kind != is_all_scope:
            msg = (
                f"scope_kind={self.scope_kind.value!r} and scope={self.scope!r} "
                "are inconsistent: ALL requires scope='all' and vice versa"
            )
            raise ValueError(msg)
        return self


# ── Hard layer ─────────────────────────────────────────────────


class OutputStyleRule(BaseModel):
    """A single deterministic hard rule.

    Attributes:
        id: Unique rule identifier within a pack.
        type: Literal-substring or regex matching.
        patterns: One or more literals (LITERAL_BAN) or regexes (REGEX_BAN);
            a match of any pattern is a violation.
        message: Agent-facing reason surfaced on rejection.
        mode: Enforcement mode for this rule.
        severity: Audit / display severity (does not drive blocking).
        rewrite: Deterministic replacement used only in ``AUTO_REWRITE`` mode.
        scan_code: Whether this rule fires inside code segments (fuzzy prose
            tells set this false to avoid false positives on identifiers).
        case_insensitive: Whether matching ignores case.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique rule identifier")
    type: RuleType = Field(description="Literal or regex matching")
    patterns: tuple[NotBlankStr, ...] = Field(description="Literals or regexes")
    message: NotBlankStr = Field(description="Agent-facing rejection reason")
    mode: EnforcementMode = Field(
        default=EnforcementMode.REJECT_REWORK,
        description="Enforcement mode for this rule",
    )
    severity: RuleSeverity = Field(
        default=RuleSeverity.WARNING,
        description="Audit / display severity",
    )
    rewrite: str | None = Field(
        default=None,
        description="Deterministic replacement used only in AUTO_REWRITE mode",
    )
    scan_code: bool = Field(
        default=True,
        description="Whether the rule fires inside code segments",
    )
    case_insensitive: bool = Field(
        default=True,
        description="Whether matching ignores case",
    )

    @model_validator(mode="after")
    def _validate_patterns_and_mode(self) -> Self:
        """Require at least one pattern, and a rewrite string in rewrite mode.

        Returns:
            The validated instance.

        Raises:
            ValueError: If ``patterns`` is empty, or ``AUTO_REWRITE`` mode is
                declared without a ``rewrite`` replacement.
        """
        if not self.patterns:
            msg = f"Rule {self.id!r} must declare at least one pattern"
            raise ValueError(msg)
        if self.mode is EnforcementMode.AUTO_REWRITE and self.rewrite is None:
            msg = f"Rule {self.id!r} is AUTO_REWRITE but declares no rewrite value"
            raise ValueError(msg)
        return self


class SanctionedExemption(BaseModel):
    """An operator-authored scope where a rule is legitimately exempt.

    An agent is granted an exemption only when its output context matches one
    of these scopes for the offending rule; an agent never self-grants.

    Attributes:
        rule_id: The rule this exempts, or ``"*"`` for every rule.
        scope_kind: Which context dimension ``match`` is compared against.
        match: A glob compared against the context value for ``scope_kind``.
        reason: Why this exemption is sanctioned (audited).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    rule_id: NotBlankStr = Field(description="Rule id, or '*' for all rules")
    scope_kind: ExemptionScopeKind = Field(description="Context dimension matched")
    match: NotBlankStr = Field(description="Glob compared against the context value")
    reason: NotBlankStr = Field(description="Why this exemption is sanctioned")


class RulePack(BaseModel):
    """A pluggable output-style pack: soft directives + hard rules.

    Attributes:
        name: Pack identifier.
        version: Semantic version string.
        description: Human-readable pack description.
        house_style: Soft directives injected into agent prompts.
        rules: Hard deterministic rules enforced at output boundaries.
        exemptions: Default sanctioned exemptions shipped with the pack
            (operator exemptions from settings merge on top).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Pack identifier")
    version: NotBlankStr = Field(description="Semantic version string")
    description: str = Field(default="", description="Pack description")
    house_style: tuple[HouseStyleDirective, ...] = Field(
        default=(),
        description="Soft directives injected into prompts",
    )
    rules: tuple[OutputStyleRule, ...] = Field(
        default=(),
        description="Hard rules enforced at output boundaries",
    )
    exemptions: tuple[SanctionedExemption, ...] = Field(
        default=(),
        description="Default sanctioned exemptions",
    )

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> Self:
        """Ensure rule ids and directive ids are each unique within the pack.

        Returns:
            The validated instance.

        Raises:
            ValueError: If any rule id or directive id repeats.
        """
        rule_ids = [r.id for r in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            dupes = sorted({rid for rid in rule_ids if rule_ids.count(rid) > 1})
            msg = f"Duplicate rule ids in pack {self.name!r}: {dupes}"
            raise ValueError(msg)
        directive_ids = [d.id for d in self.house_style]
        if len(directive_ids) != len(set(directive_ids)):
            dupes = sorted(
                {did for did in directive_ids if directive_ids.count(did) > 1}
            )
            msg = f"Duplicate directive ids in pack {self.name!r}: {dupes}"
            raise ValueError(msg)
        return self


# ── Operator config ────────────────────────────────────────────


class OutputStyleConfig(BaseModel):
    """Operator-tunable behaviour of the output-style policy.

    Attributes:
        enabled: Master switch for the whole subsystem.
        shadow_mode: When true, every rule is forced to SHADOW so the policy
            surfaces verdicts without blocking (an observation period).
        pack: Active rule pack name (built-in or user pack).
        house_style_enabled: Whether the soft house-style prompt block is
            injected.
        exemptions: Operator-authored sanctioned exemptions, merged on top of
            the pack's own default exemptions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    shadow_mode: bool = False
    pack: NotBlankStr = "default"
    house_style_enabled: bool = True
    exemptions: tuple[SanctionedExemption, ...] = ()


# ── Verdict ────────────────────────────────────────────────────


class OutputPolicyFinding(BaseModel):
    """One rule match against a span of agent output.

    Attributes:
        rule_id: The rule that matched.
        rule_type: Whether the match was a literal or a regex.
        severity: The rule's audit severity.
        mode: The effective enforcement mode after channel/segment downgrade
            and any global shadow override.
        message: The rule's agent-facing reason.
        match_text: The offending snippet (bounded).
        segment_kind: Whether the match landed in prose or code.
        exempted: Whether a sanctioned exemption covered this match.
        exemption_reason: The sanctioned exemption's reason, when exempted.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    rule_id: NotBlankStr
    rule_type: RuleType
    severity: RuleSeverity
    mode: EnforcementMode
    message: NotBlankStr
    match_text: str = Field(max_length=200)
    segment_kind: SegmentKind
    exempted: bool = False
    exemption_reason: str | None = None

    @computed_field
    @property
    def blocks(self) -> bool:
        """Whether this finding forces a reject.

        A finding blocks only when it is not exempt and its effective mode is
        ``REJECT_REWORK`` (an ``AUTO_REWRITE`` match in a code segment has
        already been downgraded to ``REJECT_REWORK`` by the evaluator).
        """
        return not self.exempted and self.mode is EnforcementMode.REJECT_REWORK


class OutputPolicyVerdict(BaseModel):
    """Result of evaluating one piece of agent output.

    Attributes:
        channel: The output boundary evaluated.
        findings: Every rule match (blocking, shadowed, or exempt).
        rewritten_text: The auto-rewritten text when an AUTO_REWRITE rule
            resolved a prose match, else ``None``.
        summary: Aggregated agent-facing reason for a blocked verdict.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    channel: OutputChannel
    findings: tuple[OutputPolicyFinding, ...] = ()
    rewritten_text: str | None = None
    summary: str = ""

    @computed_field
    @property
    def blocked(self) -> bool:
        """Whether any non-exempt REJECT_REWORK finding forces a rework."""
        return any(f.blocks for f in self.findings)

    @computed_field
    @property
    def clean(self) -> bool:
        """Whether the output has no findings at all."""
        return not self.findings


__all__ = [
    "ALL_RULES",
    "ALL_SCOPE",
    "CODE_CHANNELS",
    "EnforcementMode",
    "ExemptionScopeKind",
    "HouseStyleDirective",
    "OutputChannel",
    "OutputPolicyFinding",
    "OutputPolicyVerdict",
    "OutputStyleConfig",
    "OutputStyleRule",
    "RulePack",
    "RuleSeverity",
    "RuleType",
    "SanctionedExemption",
    "ScopeKind",
    "SegmentKind",
]
