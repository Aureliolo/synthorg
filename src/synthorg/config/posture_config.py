# module-kind: code
"""Resolved operating-posture feature flags carried on ``RootConfig``.

A template declares a named posture; the template layer
(``synthorg.templates.postures``) expands it into this flat, frozen flag set
and stamps it onto ``RootConfig.posture``. The setup-completion seeder then
translates the settings-resident flags (chat modes, steering) into the
settings service, while ``_config_assembly`` sets the config-resident
flags (``security.red_team`` / ``budget.auto_downgrade`` / ``memory``)
directly on the rendered config.

This model deliberately imports nothing from ``synthorg.templates`` /
``synthorg.meta`` so the central config hub stays cold-import safe; the
posture *name* is a plain string here and is validated against the known
catalogue at the template layer where it is declared.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

# Grounding checker for the red-team completion gate (mirrors the
# ``security.red_team.grounding_checker_kind`` knob).
GroundingChecker = Literal["heuristic", "knowledge_substrate"]


class PostureConfig(BaseModel):
    """Resolved runtime feature flags for a template's operating posture.

    Defaults are all-off / least-capable, so a default-constructed
    ``PostureConfig`` is the neutral "no posture" baseline that leaves every
    optional subsystem at its own default.

    Attributes:
        name: Declared posture name (``None`` = no posture).
        knowledge_substrate: Ground work in the shared knowledge base.
        chat_propose: Clarify-or-park proposal chat mode.
        chat_routing: Per-turn concern routing in front of proposals.
        group_chat: Multi-party group chat with stakeholders.
        agent_invite: Agent-initiated invites into group chat.
        direct_mcp: Direct MCP acting (fail-closed; needs governance).
        steering: Mid-flight steering proposer.
        red_team: Red-team completion gate.
        red_team_grounding: Grounding checker for the red-team gate.
        auto_downgrade: Budget-driven model auto-downgrade.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr | None = Field(default=None)
    knowledge_substrate: bool = Field(default=False)
    chat_propose: bool = Field(default=False)
    chat_routing: bool = Field(default=False)
    group_chat: bool = Field(default=False)
    agent_invite: bool = Field(default=False)
    direct_mcp: bool = Field(default=False)
    steering: bool = Field(default=False)
    red_team: bool = Field(default=False)
    red_team_grounding: GroundingChecker = Field(default="heuristic")
    auto_downgrade: bool = Field(default=False)

    @model_validator(mode="after")
    def _grounding_requires_red_team(self) -> Self:
        """Reject a non-default grounding checker without the red-team gate.

        ``red_team_grounding`` only has meaning when ``red_team`` is on; a
        non-``heuristic`` checker with the gate off is an incoherent state.

        Returns:
            The validated config.

        Raises:
            ValueError: When grounding is set but ``red_team`` is ``False``.
        """
        if self.red_team_grounding != "heuristic" and not self.red_team:
            msg = "red_team_grounding requires red_team=True"
            raise ValueError(msg)
        return self

    def merge(self, other: Self) -> PostureConfig:
        """Union *self* with *other*, taking the more-capable value per flag.

        Boolean flags OR together; the grounding checker upgrades to
        ``knowledge_substrate`` when either side requests it; ``self.name`` is
        kept (the host posture). Used to fold a pack's posture contribution
        into the host template's posture.

        Returns:
            A new ``PostureConfig`` with the combined, more-capable flags.
        """
        groundings = (self.red_team_grounding, other.red_team_grounding)
        grounding: GroundingChecker = (
            "knowledge_substrate"
            if "knowledge_substrate" in groundings
            else "heuristic"
        )
        return PostureConfig(
            name=self.name,
            knowledge_substrate=self.knowledge_substrate or other.knowledge_substrate,
            chat_propose=self.chat_propose or other.chat_propose,
            chat_routing=self.chat_routing or other.chat_routing,
            group_chat=self.group_chat or other.group_chat,
            agent_invite=self.agent_invite or other.agent_invite,
            direct_mcp=self.direct_mcp or other.direct_mcp,
            steering=self.steering or other.steering,
            red_team=self.red_team or other.red_team,
            red_team_grounding=grounding,
            auto_downgrade=self.auto_downgrade or other.auto_downgrade,
        )
