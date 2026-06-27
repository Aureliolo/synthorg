"""Persistence protocol for in-flight hiring requests.

Durably backs :class:`synthorg.hr.hiring_service.HiringService` so an
approved request survives a restart between approval and instantiation
rather than leaving a dangling approval with no record to instantiate
against. The request moves through a status lifecycle (pending ->
approved/rejected -> instantiated), so the repository composes
:class:`IdKeyedRepository` (full upsert per step, serialised by the
service's per-request lock) plus :class:`FilteredQueryRepository` for
status enumeration.

The nested ``HiringRequest`` (candidate cards, skills) round-trips
through a single JSON ``payload`` column; ``status`` / ``requested_by``
/ ``department`` / ``role`` / ``created_at`` are promoted to columns.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import HiringRequestStatus
from synthorg.hr.models import HiringRequest
from synthorg.persistence._generics import (
    FilteredQueryRepository,
    IdKeyedRepository,
)


class HiringRequestFilterSpec(BaseModel):
    """Filter spec for :meth:`HiringRequestRepository.query`.

    Attributes:
        status: Restrict to one lifecycle status. ``None`` reads all.
        requested_by: Restrict to one requester. ``None`` reads all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    status: HiringRequestStatus | None = Field(
        default=None,
        description="Restrict to one lifecycle status; None reads all",
    )
    requested_by: NotBlankStr | None = Field(
        default=None,
        description="Restrict to one requester; None reads all",
    )


@runtime_checkable
class HiringRequestRepository(
    IdKeyedRepository[HiringRequest, NotBlankStr],
    FilteredQueryRepository[HiringRequest, HiringRequestFilterSpec],
    Protocol,
):
    """CRUD + status-filtered query for in-flight hiring requests.

    :class:`HiringService` upserts the full request on every lifecycle
    transition (under its per-request lock) and rehydrates the in-flight
    set from :meth:`list_items` at startup.
    """
