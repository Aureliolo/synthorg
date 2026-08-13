# module-kind: code
"""Request / response DTOs for the capability-source API.

Every response carries the provenance an operator needs before trusting a
rung: which source, when it last worked, how stale that makes the evidence,
and how much of the feed reached a configured model. A number with no
visible origin is not admissible evidence, and a source that quietly
stopped answering is indistinguishable from one with nothing to say unless
the failure is on the screen next to it.
"""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field

from synthorg.core.types import NotBlankStr
from synthorg.providers.capability_sources.registry import CapabilitySourceSpec
from synthorg.providers.capability_sources.status import CapabilitySourceStatus

#: Seconds in a day, for rendering an age the dashboard shows in days.
_SECONDS_PER_DAY = 86_400.0


class CapabilitySourceDTO(BaseModel):
    """One declared source, its operator setting, and its last outcome."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    label: NotBlankStr = Field(description="Stable registry key")
    display_name: NotBlankStr = Field(description="Name shown in the dashboard")
    enabled: bool = Field(description="Whether it contributes evidence")
    feed_url: NotBlankStr = Field(description="Where it is fetched from")
    is_custom_url: bool = Field(description="Whether an operator set the URL")
    axes: tuple[str, ...] = Field(description="Axes this source can measure")
    licence_note: NotBlankStr = Field(description="Terms under which we read it")
    attribution: str = Field(description="Credit the licence requires")
    cadence_note: NotBlankStr = Field(description="How often the feed moves")
    last_attempted_at: AwareDatetime | None = Field(
        default=None,
        description="When a refresh was last tried",
    )
    last_succeeded_at: AwareDatetime | None = Field(
        default=None,
        description="When a refresh last produced rows",
    )
    last_error: str = Field(default="", description="Why the last attempt failed")
    rows_read: int = Field(default=0, ge=0, description="Rows the parse saw")
    rows_skipped: int = Field(default=0, ge=0, description="Rows it could not use")
    scores_written: int = Field(default=0, ge=0, description="Measurements persisted")
    evidence_age_days: float | None = Field(
        default=None,
        ge=0.0,
        description="How old the evidence still grading is, in days",
    )

    @computed_field(description="Whether the last attempt produced evidence")
    @property
    def is_healthy(self) -> bool:
        """Whether this source is currently answering.

        Returns:
            ``True`` when the most recent attempt succeeded.
        """
        return not self.last_error and self.last_succeeded_at is not None

    @computed_field(description="Whether rows from a past success still grade")
    @property
    def has_stale_evidence(self) -> bool:
        """Whether the source is failing but its old rows still count.

        This is the state that is otherwise invisible: grading continues,
        correctly, on evidence nobody refreshed.

        Returns:
            ``True`` when the last attempt failed but an earlier one
            succeeded.
        """
        return bool(self.last_error) and self.last_succeeded_at is not None


def to_capability_source_dto(
    spec: CapabilitySourceSpec,
    status: CapabilitySourceStatus,
    *,
    enabled: bool,
    feed_url: str,
    now: datetime,
) -> CapabilitySourceDTO:
    """Map a spec plus its status onto the dashboard DTO.

    Returns:
        The :class:`CapabilitySourceDTO` for one source.
    """
    age_days: float | None = None
    if status.last_succeeded_at is not None:
        age_days = max(
            0.0,
            (now - status.last_succeeded_at).total_seconds() / _SECONDS_PER_DAY,
        )
    return CapabilitySourceDTO(
        label=spec.label,
        display_name=spec.display_name,
        enabled=enabled,
        feed_url=NotBlankStr(feed_url),
        is_custom_url=feed_url != str(spec.feed_url),
        axes=tuple(spec.axes),
        licence_note=spec.licence_note,
        attribution=spec.attribution,
        cadence_note=spec.cadence_note,
        last_attempted_at=status.last_attempted_at,
        last_succeeded_at=status.last_succeeded_at,
        last_error=status.last_error,
        rows_read=status.rows_read,
        rows_skipped=status.rows_skipped,
        scores_written=status.scores_written,
        evidence_age_days=age_days,
    )


class CapabilitySourcesResponse(BaseModel):
    """Every declared source with its setting and last outcome."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    sources: tuple[CapabilitySourceDTO, ...] = Field(default=())

    @computed_field(description="Whether any source is currently answering")
    @property
    def any_healthy(self) -> bool:
        """Whether at least one enabled source produced evidence.

        Returns:
            ``True`` when the grading has at least one working source
            behind it.
        """
        return any(s.is_healthy for s in self.sources if s.enabled)


class CapabilitySourceSettingRequest(BaseModel):
    """Enable, disable, or re-point one source."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(default=True, description="Whether it contributes")
    feed_url: str = Field(
        default="",
        description=(
            "Feed URL, or empty to use the shipped default. A URL supplied "
            "here is checked against the network allowlist before anything "
            "fetches it."
        ),
    )


class CapabilitySourceRefreshRequest(BaseModel):
    """Refresh one source now."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    force: bool = Field(
        default=False,
        description=(
            "Refresh even when the source was fetched inside the configured "
            "interval. The age gate is the only thing this skips: the URL is "
            "still validated and an unreadable feed is still refused."
        ),
    )


class CapabilitySourceRowsRequest(BaseModel):
    """Ingest an operator-supplied feed document for one source."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    document: str = Field(
        min_length=1,
        description=(
            "The feed document, as text for a text format or base64 for a "
            "binary one. It takes the same parse path as an automatic "
            "refresh, so an upload cannot land rows a refresh would reject."
        ),
    )
    is_base64: bool = Field(
        default=False,
        description="Whether `document` is base64-encoded binary",
    )


__all__ = [
    "CapabilitySourceDTO",
    "CapabilitySourceRefreshRequest",
    "CapabilitySourceRowsRequest",
    "CapabilitySourceSettingRequest",
    "CapabilitySourcesResponse",
    "to_capability_source_dto",
]
