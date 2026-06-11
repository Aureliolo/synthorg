"""Verify ``ClientPool`` raises log the rejection event before bubbling."""

import pytest
import structlog

from synthorg.client.models import (
    ClientFeedback,
    ClientProfile,
    GenerationContext,
    ReviewContext,
    TaskRequirement,
)
from synthorg.client.pool import ClientPool
from synthorg.observability.events.client import CLIENT_NOT_FOUND


class _StubClient:
    def __init__(self, profile: ClientProfile) -> None:
        self.profile = profile

    async def submit_requirement(
        self,
        context: GenerationContext,
    ) -> TaskRequirement | None:
        del context
        return None

    async def review_deliverable(
        self,
        context: ReviewContext,
    ) -> ClientFeedback:
        del context
        return ClientFeedback(
            task_id="stub",
            client_id=self.profile.client_id,
            accepted=True,
        )


def _profile(client_id: str = "c-1") -> ClientProfile:
    return ClientProfile(
        client_id=client_id,
        name=f"Client {client_id}",
        persona="test",
        expertise_domains=(),
        strictness_level=0.5,
    )


@pytest.mark.unit
class TestPoolMissingClientLogs:
    """Every missing-client raise emits CLIENT_NOT_FOUND with the operation."""

    @pytest.mark.parametrize(
        ("operation", "call"),
        [
            ("remove", lambda pool: pool.remove("missing")),
            ("deactivate", lambda pool: pool.deactivate("missing")),
            ("reactivate", lambda pool: pool.reactivate("missing")),
            ("is_active", lambda pool: pool.is_active("missing")),
            ("get_profile", lambda pool: pool.get_profile("missing")),
        ],
    )
    async def test_logs_before_raise(
        self,
        operation: str,
        call: object,
    ) -> None:
        pool = ClientPool()
        with structlog.testing.capture_logs() as cap, pytest.raises(KeyError):
            await call(pool)  # type: ignore[operator]
        events = [e for e in cap if e["event"] == CLIENT_NOT_FOUND]
        assert len(events) == 1
        assert events[0]["log_level"] == "warning"
        assert events[0]["client_id"] == "missing"
        assert events[0]["operation"] == operation

    async def test_present_client_does_not_log(self) -> None:
        pool = ClientPool()
        profile = _profile("c-1")
        await pool.add(profile=profile, client=_StubClient(profile))
        with structlog.testing.capture_logs() as cap:
            await pool.get_profile("c-1")
        assert not [e for e in cap if e["event"] == CLIENT_NOT_FOUND]
