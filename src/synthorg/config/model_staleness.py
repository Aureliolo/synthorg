"""Staleness marker for a configured model.

Separated from :mod:`synthorg.config.provider_schema` so the provider
schema module stays under its size budget.  ``ModelStaleness`` is an
operational lifecycle record stamped on a
:class:`~synthorg.config.provider_schema.ProviderModelConfig` by the
periodic model-refresh service when a configured id is no longer
advertised by its provider.  It is deliberately distinct from
:class:`~synthorg.config.model_metadata.ModelMetadata`: metadata is a
capability/provenance record enriched at ingest, whereas staleness is
flipped by the reconcile service on its own cadence and never deletes
the model (so an operator can still see and re-point away from it).
"""

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

StalenessReason = Literal["removed_from_catalog", "deprecated"]
"""Why a configured model was flagged stale.

``removed_from_catalog`` (no longer advertised by the provider's live
catalogue), ``deprecated`` (still advertised but marked deprecated).
"""


class ModelStaleness(BaseModel):
    """Operational staleness marker for a single configured model.

    Attributes:
        reason: Why the model was flagged.
        flagged_at: When the reconcile service first flagged it.
        last_seen: Last date the id was observed in the live catalogue,
            when known.
        successor_model_id: Suggested in-family replacement, when one
            could be identified.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    reason: StalenessReason
    flagged_at: datetime
    last_seen: date | None = Field(
        default=None,
        description="Last date the id was seen in the live catalogue",
    )
    successor_model_id: NotBlankStr | None = Field(
        default=None,
        description="Suggested in-family replacement id, when identified",
    )

    @model_validator(mode="after")
    def _last_seen_not_after_flagged(self) -> Self:
        """Reject a ``last_seen`` later than ``flagged_at``.

        A model cannot have last been observed in the catalogue after the
        moment it was flagged as gone from it.

        Returns:
            The validated marker.

        Raises:
            ValueError: If ``last_seen`` post-dates ``flagged_at``.
        """
        if self.last_seen is not None and self.last_seen > self.flagged_at.date():
            msg = "last_seen cannot be after flagged_at"
            raise ValueError(msg)
        return self
