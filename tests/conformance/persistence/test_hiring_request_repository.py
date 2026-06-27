"""Conformance tests for ``HiringRequestRepository``."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import HiringRequestStatus
from synthorg.hr.models import CandidateCard, HiringRequest
from synthorg.hr.seniority import SeniorityLevel
from synthorg.persistence.hiring_request_protocol import (
    HiringRequestFilterSpec,
)
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)


def _candidate(*, department: str, role: str) -> CandidateCard:
    return CandidateCard(
        name="Casey Candidate",
        role=NotBlankStr(role),
        department=NotBlankStr(department),
        level=SeniorityLevel.MID,
        rationale="strong fit",
        estimated_monthly_cost=1000.0,
    )


def _request(
    *,
    requested_by: str = "founder",
    department: str = "Engineering",
    role: str = "Backend Engineer",
    status: HiringRequestStatus = HiringRequestStatus.PENDING,
    when: datetime = _NOW,
) -> HiringRequest:
    # APPROVED/INSTANTIATED requests must carry a selected candidate that
    # references one of the request's candidates (HiringRequest invariant).
    needs_candidate = status in (
        HiringRequestStatus.APPROVED,
        HiringRequestStatus.INSTANTIATED,
    )
    candidates: tuple[CandidateCard, ...] = ()
    selected: str | None = None
    if needs_candidate:
        card = _candidate(department=department, role=role)
        candidates = (card,)
        selected = str(card.id)
    return HiringRequest(
        requested_by=NotBlankStr(requested_by),
        department=NotBlankStr(department),
        role=NotBlankStr(role),
        level=SeniorityLevel.MID,
        required_skills=(NotBlankStr("python"), NotBlankStr("postgres")),
        reason=NotBlankStr("scale the platform team"),
        status=status,
        candidates=candidates,
        selected_candidate_id=selected,
        created_at=when,
    )


class TestHiringRequestRepository:
    async def test_save_get_round_trip(self, backend: PersistenceBackend) -> None:
        request = _request()
        await backend.hiring_requests.save(request)

        result = await backend.hiring_requests.get(NotBlankStr(str(request.id)))
        assert result is not None
        assert result.id == request.id
        assert result.role == "Backend Engineer"
        assert result.required_skills == ("python", "postgres")
        assert result.status is HiringRequestStatus.PENDING

    async def test_save_upserts_status_transition(
        self, backend: PersistenceBackend
    ) -> None:
        request = _request(status=HiringRequestStatus.PENDING)
        await backend.hiring_requests.save(request)
        card = _candidate(department="Engineering", role="Backend Engineer")
        approved = request.model_copy(
            update={
                "status": HiringRequestStatus.APPROVED,
                "candidates": (card,),
                "selected_candidate_id": str(card.id),
            }
        )
        await backend.hiring_requests.save(approved)

        result = await backend.hiring_requests.get(NotBlankStr(str(request.id)))
        assert result is not None
        assert result.status is HiringRequestStatus.APPROVED

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        missing = NotBlankStr(str(as_uuid("missing-hiring-request")))
        assert await backend.hiring_requests.get(missing) is None

    async def test_delete(self, backend: PersistenceBackend) -> None:
        request = _request()
        await backend.hiring_requests.save(request)
        rid = NotBlankStr(str(request.id))
        assert await backend.hiring_requests.delete(rid)
        assert await backend.hiring_requests.get(rid) is None
        assert not await backend.hiring_requests.delete(rid)

    async def test_query_by_status(self, backend: PersistenceBackend) -> None:
        await backend.hiring_requests.save(_request(status=HiringRequestStatus.PENDING))
        await backend.hiring_requests.save(
            _request(
                status=HiringRequestStatus.APPROVED,
                when=_NOW + timedelta(hours=1),
            )
        )

        pending = await backend.hiring_requests.query(
            HiringRequestFilterSpec(status=HiringRequestStatus.PENDING)
        )
        assert all(r.status is HiringRequestStatus.PENDING for r in pending)
        assert len(pending) >= 1

    async def test_count(self, backend: PersistenceBackend) -> None:
        await backend.hiring_requests.save(
            _request(requested_by="counter", status=HiringRequestStatus.PENDING)
        )
        count = await backend.hiring_requests.count(
            HiringRequestFilterSpec(requested_by=NotBlankStr("counter"))
        )
        assert count == 1

    async def test_list_items(self, backend: PersistenceBackend) -> None:
        await backend.hiring_requests.save(_request(when=_NOW))
        await backend.hiring_requests.save(_request(when=_NOW + timedelta(hours=2)))
        results = await backend.hiring_requests.list_items()
        assert len(results) >= 2
