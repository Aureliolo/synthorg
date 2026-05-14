"""Autonomy data models -- presets, config, effective resolution, overrides."""

from types import MappingProxyType
from typing import Any, ClassVar, Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import AutonomyLevel, DowngradeReason, compare_autonomy
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import MirrorField, apply_settings_mirrors

# Minimum length (after whitespace strip) required for an autonomy
# change reason.  Pulled into a module constant so the validator below
# stays compliant with the project's "no magic numbers" lint rule.
_MIN_REASON_LENGTH: Final[int] = 3


class AutonomyPreset(BaseModel):
    """A named autonomy preset defining action routing rules.

    Actions listed in ``auto_approve`` are executed without human
    review. Actions in ``human_approval`` require a human decision.
    The two sets must be disjoint -- an action cannot be both
    auto-approved and human-approval.

    Attributes:
        level: The autonomy level this preset represents.
        description: Human-readable description.
        auto_approve: Action type patterns that are auto-approved.
            The special value ``"all"`` means every action type.
            Category shortcuts (e.g. ``"code"``) are expanded via
            :class:`~synthorg.security.action_types.ActionTypeRegistry`.
        human_approval: Action type patterns requiring human approval.
            Same expansion rules as ``auto_approve``.
        security_agent: Whether a security agent reviews escalated
            actions before they reach a human.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    level: AutonomyLevel = Field(description="Autonomy level")
    description: NotBlankStr = Field(description="Human-readable description")
    auto_approve: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Action patterns that are auto-approved",
    )
    human_approval: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Action patterns requiring human approval",
    )
    security_agent: bool = Field(
        default=True,
        description="Whether security agent reviews escalations",
    )

    @model_validator(mode="after")
    def _validate_disjoint(self) -> Self:
        """Ensure auto_approve and human_approval are disjoint."""
        overlap = set(self.auto_approve) & set(self.human_approval)
        if overlap:
            msg = (
                f"auto_approve and human_approval must be disjoint, "
                f"overlapping entries: {sorted(overlap)}"
            )
            raise ValueError(msg)
        return self


BUILTIN_PRESETS: Final[MappingProxyType[str, AutonomyPreset]] = MappingProxyType(
    {
        AutonomyLevel.FULL: AutonomyPreset(
            level=AutonomyLevel.FULL,
            description="Fully autonomous -- all actions auto-approved",
            auto_approve=("all",),
            human_approval=(),
            security_agent=False,
        ),
        # SEMI extends the Operations design page with vcs and db:query auto-approve
        # (safe read/commit operations) and broader human_approval categories.
        AutonomyLevel.SEMI: AutonomyPreset(
            level=AutonomyLevel.SEMI,
            description=(
                "Semi-autonomous -- code, test, docs, vcs auto-approved; "
                "deploy, org, budget require human approval"
            ),
            auto_approve=("code", "test", "docs", "vcs", "comms:internal", "db:query"),
            human_approval=("deploy", "org", "budget", "comms:external"),
            security_agent=True,
        ),
        AutonomyLevel.SUPERVISED: AutonomyPreset(
            level=AutonomyLevel.SUPERVISED,
            description=(
                "Supervised -- read-only and test actions auto-approved; "
                "all mutations require human approval"
            ),
            auto_approve=("code:read", "vcs:read", "test:run", "db:query"),
            human_approval=(
                "code:write",
                "code:create",
                "code:delete",
                "code:refactor",
                "test:write",
                "docs:write",
                "vcs:commit",
                "vcs:push",
                "vcs:branch",
                "deploy",
                "comms",
                "budget",
                "org",
                "db:mutate",
                "db:admin",
                "arch:decide",
            ),
            security_agent=True,
        ),
        AutonomyLevel.LOCKED: AutonomyPreset(
            level=AutonomyLevel.LOCKED,
            description="Locked -- all actions require human approval",
            auto_approve=(),
            human_approval=("all",),
            security_agent=True,
        ),
    }
)


class AutonomyConfig(BaseModel):
    """Company-level autonomy configuration.

    Attributes:
        level: Default autonomy level for the company.
        presets: Available autonomy presets keyed by level name.
            Defaults to ``BUILTIN_PRESETS``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="level",
            namespace=SettingNamespace.COMPANY,
            key="autonomy_level",
        ),
    )

    level: AutonomyLevel = Field(
        default=AutonomyLevel.SUPERVISED,
        description=(
            "Default company autonomy level. Ships as 'supervised' so"
            " most state-mutating agent actions queue for approval;"
            " raise to 'semi' or 'full' once operators trust the"
            " organization. Kept in sync with the"
            " ``company.autonomy_level`` SettingDefinition default."
        ),
    )
    presets: dict[str, AutonomyPreset] = Field(
        default_factory=lambda: dict(BUILTIN_PRESETS),
        description="Available autonomy presets",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: Any) -> Any:
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

    @model_validator(mode="after")
    def _validate_level_in_presets(self) -> Self:
        """Ensure the configured level has a matching preset."""
        if self.level not in self.presets:
            msg = (
                f"Autonomy level {self.level!r} not found in presets "
                f"(available: {sorted(self.presets)})"
            )
            raise ValueError(msg)
        return self


class EffectiveAutonomy(BaseModel):
    """Resolved, expanded autonomy for an agent's execution run.

    Produced by :class:`~synthorg.security.autonomy.resolver.AutonomyResolver`
    by resolving the three-level chain (agent → department → company)
    and expanding category shortcuts into concrete action types.

    Attributes:
        level: Resolved autonomy level.
        auto_approve_actions: Concrete action types that are auto-approved.
        human_approval_actions: Concrete action types requiring human approval.
        security_agent: Whether the security agent reviews escalations.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    level: AutonomyLevel = Field(description="Resolved autonomy level")
    auto_approve_actions: frozenset[str] = Field(
        description="Expanded auto-approve action types",
    )
    human_approval_actions: frozenset[str] = Field(
        description="Expanded human-approval action types",
    )
    security_agent: bool = Field(
        description="Whether security agent reviews escalations",
    )

    @model_validator(mode="after")
    def _validate_disjoint(self) -> Self:
        """Ensure expanded action sets are disjoint."""
        overlap = self.auto_approve_actions & self.human_approval_actions
        if overlap:
            msg = (
                f"auto_approve_actions and human_approval_actions must be "
                f"disjoint, overlapping: {sorted(overlap)}"
            )
            raise ValueError(msg)
        return self


class AutonomyUpdate(BaseModel):
    """Request payload for changing an agent's autonomy level.

    Used by the MCP write facade and any future direct service caller.
    The mutation does not happen here -- the request is logged and, when
    an approval store is wired, enqueued for human approval before any
    runtime effect.

    Attributes:
        requested_level: The desired autonomy level for the agent.
        reason: Human-readable justification for the change. Required so
            audit logs and approval reviewers have context.
        requested_by: Identifier of the actor requesting the change.
            ``None`` lets the registry infer the actor from the
            invocation context.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    requested_level: AutonomyLevel = Field(description="Requested autonomy level")
    reason: NotBlankStr = Field(
        max_length=2048,
        description=(
            "Why the change is requested. Must contain at least 3 "
            "non-whitespace characters after stripping (raw whitespace "
            "padding does not count toward the floor)."
        ),
    )
    requested_by: NotBlankStr | None = Field(
        default=None,
        description="Identifier of the requesting actor",
    )

    @model_validator(mode="after")
    def _validate_reason_length(self) -> Self:
        """Reject reasons that are shorter than the minimum non-blank length."""
        if len(self.reason.strip()) < _MIN_REASON_LENGTH:
            msg = (
                f"reason must contain at least {_MIN_REASON_LENGTH} "
                "non-whitespace characters"
            )
            raise ValueError(msg)
        return self


class AutonomyUpdateResult(BaseModel):
    """Outcome of an autonomy update request.

    All autonomy changes route through human approval, so the result
    advertises the agent's *current* level alongside the level that was
    requested. ``promotion_pending`` is ``True`` whenever a human review
    is still required; ``approval_enqueued`` distinguishes "successfully
    queued for review" from "no approval store was wired so the request
    is effectively dropped" -- both formerly looked identical because
    ``promotion_pending`` stayed ``True`` either way and only
    ``approval_id`` differed.

    Note:
        ``promotion_pending`` defaults to ``True`` because every change
        currently pends approval -- the registry never returns
        ``False`` from this path. Callers reading the model in the
        future should not assume the default flips.

    Attributes:
        agent_id: The agent whose autonomy was requested to change.
        current_level: The agent's effective autonomy level today.
        requested_level: The level that was asked for.
        promotion_pending: Whether human approval is still required.
        approval_enqueued: Whether an approval item was successfully
            persisted to a wired approval store. ``False`` means the
            request reached the registry but no approval store was
            available; the change is effectively dropped and the
            caller should retry once the approval pipeline is wired.
        approval_id: Identifier of the enqueued approval item, or
            ``None`` when no approval store was wired
            (``approval_enqueued=False``) -- always paired.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent identifier")
    current_level: AutonomyLevel = Field(description="Current autonomy level")
    requested_level: AutonomyLevel = Field(description="Requested autonomy level")
    promotion_pending: bool = Field(
        default=True,
        description="Whether the change is awaiting human approval",
    )
    approval_enqueued: bool = Field(
        default=False,
        description=(
            "Whether the request was persisted to an approval store; "
            "False means no approval store was wired"
        ),
    )
    approval_id: NotBlankStr | None = Field(
        default=None,
        description="Approval-item identifier when an approval was enqueued",
    )


class AutonomyOverride(BaseModel):
    """Record of a runtime autonomy downgrade for an agent.

    Attributes:
        agent_id: The agent whose autonomy was changed.
        original_level: Level before the downgrade.
        current_level: Level after the downgrade.
        reason: Why the downgrade occurred.
        downgraded_at: When the downgrade happened.
        requires_human_recovery: Whether a human must restore the level.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent identifier")
    original_level: AutonomyLevel = Field(description="Level before downgrade")
    current_level: AutonomyLevel = Field(description="Level after downgrade")
    reason: DowngradeReason = Field(description="Reason for downgrade")
    downgraded_at: AwareDatetime = Field(description="Timestamp of downgrade")
    requires_human_recovery: bool = Field(
        default=True,
        description="Whether human approval is needed to restore level",
    )

    @model_validator(mode="after")
    def _validate_downgrade(self) -> Self:
        """Ensure current_level is not higher than original_level."""
        if compare_autonomy(self.current_level, self.original_level) > 0:
            msg = (
                f"current_level {self.current_level.value!r} is higher than "
                f"original_level {self.original_level.value!r} -- "
                f"downgrades must not increase autonomy"
            )
            raise ValueError(msg)
        return self
