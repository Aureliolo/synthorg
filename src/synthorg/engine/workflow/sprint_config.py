"""Sprint configuration models -- ceremony definitions and sprint settings.

Integrates with the meeting protocol system via ``MeetingProtocolType``
and ``MeetingFrequency`` from the communication module, and with the
pluggable ceremony scheduling system via ``CeremonyPolicyConfig``.
"""

from collections import Counter
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.communication.meeting.config import MeetingProtocolConfig
from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,  # used in default_factory lambda
)


class SprintCeremonyConfig(BaseModel):
    """Configuration for a single sprint ceremony.

    Links a ceremony name to a meeting protocol type and an optional
    recurrence frequency from the communication subsystem.  Ceremonies
    may also declare a ``policy_override`` to override the project or
    department-level ceremony scheduling policy for this specific
    ceremony.

    At least one of ``frequency`` or ``policy_override`` must be set.
    When both are set, the ``frequency`` provides a calendar-based
    fallback while the policy strategy handles task-driven triggers.

    Attributes:
        name: Ceremony identifier (e.g. ``"sprint_planning"``).
        protocol: Meeting protocol type to use.
        frequency: How often the ceremony occurs (calendar-based).
            ``None`` when scheduling is fully strategy-driven.
        duration_tokens: Token budget for the meeting.
        participants: Department names or ``"all"``.
        policy_override: Optional per-ceremony override for the
            ceremony scheduling policy.
        protocol_config: Protocol settings for the meeting this ceremony
            runs.  Its ``protocol`` is filled from :attr:`protocol` when
            the author omits it, so a ceremony names its protocol once.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(
        description="Ceremony identifier",
        pattern=r"^[a-z0-9_-]+$",
    )
    protocol: MeetingProtocolType = Field(
        description="Meeting protocol type",
    )
    frequency: MeetingFrequency | None = Field(
        default=None,
        description="Recurrence frequency (calendar-based)",
    )
    duration_tokens: int = Field(
        default=5000,
        ge=100,
        le=50_000,
        description="Token budget for the meeting",
    )
    participants: tuple[NotBlankStr, ...] = Field(
        default=(),
        description='Department names or "all"',
    )
    policy_override: CeremonyPolicyConfig | None = Field(
        default=None,
        description="Per-ceremony scheduling policy override",
    )
    protocol_config: MeetingProtocolConfig = Field(
        default_factory=MeetingProtocolConfig,
        description="Protocol settings for this ceremony's meeting",
    )

    @model_validator(mode="before")
    @classmethod
    def _bind_protocol_config_to_protocol(cls, data: object) -> object:
        """Make ``protocol`` the one answer to which protocol runs.

        A ceremony names its protocol in ``protocol``; the sub-config it
        tunes lives in ``protocol_config``, which carries a ``protocol``
        of its own.  Left alone, the two can disagree and the meeting
        runs on whichever one the bridge happens to read.  So an omitted
        nested value is filled from the ceremony's, and a disagreeing one
        is refused: a silent override is how config comes to be accepted
        and discarded.

        Runs ``mode="before"`` because after validation the nested
        config already exists carrying its own default protocol, and
        ``model_fields_set`` cannot tell "the author omitted it" from
        "the author wrote the default" once a ceremony has round-tripped
        through ``model_dump()`` on the way to storage.  Reconciling
        before construction is the only point where the distinction
        still exists.

        Returns:
            The input with ``protocol_config.protocol`` supplied.

        Raises:
            ValueError: When a supplied ``protocol_config.protocol``
                names a different protocol from the ceremony's.
        """
        if not isinstance(data, dict) or "protocol" not in data:
            return data
        protocol = MeetingProtocolType(data["protocol"])
        if "protocol_config" not in data:
            return {**data, "protocol_config": {"protocol": protocol}}
        # Membership, not ``get``: an explicit ``protocol_config=None``
        # is a type error the field declaration already rejects, and
        # filling it in here would silently accept it instead.
        supplied = data["protocol_config"]

        nested: MeetingProtocolType | None = None
        if isinstance(supplied, MeetingProtocolConfig):
            nested = supplied.protocol
        elif isinstance(supplied, dict):
            raw = supplied.get("protocol")
            if raw is None:
                return {**data, "protocol_config": {**supplied, "protocol": protocol}}
            nested = MeetingProtocolType(raw)
        else:
            # Not a shape this validator can read; pydantic reports the
            # type error better than a guess here would.
            return data

        if nested is not protocol:
            msg = (
                f"Ceremony {data.get('name', '<unnamed>')!r}: protocol is "
                f"{protocol.value!r} but protocol_config.protocol is "
                f"{nested.value!r}; a ceremony names its protocol once"
            )
            raise ValueError(msg)
        return data

    @model_validator(mode="after")
    def _validate_scheduling_source(self) -> Self:
        """At least one of frequency or policy_override must be set.

        Returns:
            ``self`` unchanged when at least one scheduling source is
            configured.

        Raises:
            ValueError: When both ``frequency`` and
                ``policy_override`` are ``None``.
        """
        if self.frequency is None and self.policy_override is None:
            msg = (
                f"Ceremony {self.name!r}: at least one of "
                f"'frequency' or 'policy_override' must be set"
            )
            raise ValueError(msg)
        return self


class SprintConfig(BaseModel):
    """Agile sprint workflow configuration.

    Attributes:
        duration_days: Default sprint duration in days.
        max_tasks_per_sprint: Maximum tasks allowed in a sprint backlog.
        velocity_window: Number of recent sprints for rolling velocity
            average.
        ceremony_policy: Project-level ceremony scheduling policy.
        ceremonies: Sprint ceremony definitions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    duration_days: int = Field(
        default=14,
        ge=1,
        le=90,
        description="Default sprint duration in days",
    )
    max_tasks_per_sprint: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum tasks per sprint backlog",
    )
    velocity_window: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Sprints for rolling velocity average",
    )
    ceremony_policy: CeremonyPolicyConfig = Field(
        default_factory=lambda: CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
            auto_transition=True,
            transition_threshold=1.0,
        ),
        description="Project-level ceremony scheduling policy",
    )
    ceremonies: tuple[SprintCeremonyConfig, ...] = Field(
        default=(
            SprintCeremonyConfig(
                name="sprint_planning",
                protocol=MeetingProtocolType.STRUCTURED_PHASES,
                frequency=MeetingFrequency.BI_WEEKLY,
            ),
            SprintCeremonyConfig(
                name="daily_standup",
                protocol=MeetingProtocolType.ROUND_ROBIN,
                frequency=MeetingFrequency.PER_SPRINT_DAY,
                duration_tokens=2000,
            ),
            SprintCeremonyConfig(
                name="sprint_review",
                protocol=MeetingProtocolType.ROUND_ROBIN,
                frequency=MeetingFrequency.BI_WEEKLY,
            ),
            SprintCeremonyConfig(
                name="retrospective",
                protocol=MeetingProtocolType.POSITION_PAPERS,
                frequency=MeetingFrequency.BI_WEEKLY,
                duration_tokens=3000,
            ),
        ),
        description="Sprint ceremony definitions",
    )

    @model_validator(mode="after")
    def _validate_unique_ceremony_names(self) -> Self:
        """Reject duplicate ceremony names.

        Returns:
            ``self`` unchanged when every ceremony name is unique.

        Raises:
            ValueError: When ceremony names duplicate.
        """
        names = [c.name for c in self.ceremonies]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, count in Counter(names).items() if count > 1)
            msg = f"Duplicate ceremony names: {dupes}"
            raise ValueError(msg)
        return self
