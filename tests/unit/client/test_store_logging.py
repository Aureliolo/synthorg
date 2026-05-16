"""Verify ``RequestStore.get`` logs CLIENT_REQUEST_NOT_FOUND before raise."""

import pytest
import structlog

from synthorg.client.store import RequestStore
from synthorg.observability.events.client import CLIENT_REQUEST_NOT_FOUND


@pytest.mark.unit
class TestRequestStoreMissingLogs:
    async def test_get_missing_logs_before_raise(self) -> None:
        store = RequestStore()
        with structlog.testing.capture_logs() as cap, pytest.raises(KeyError):
            await store.get("missing-id")
        events = [e for e in cap if e["event"] == CLIENT_REQUEST_NOT_FOUND]
        assert len(events) == 1
        assert events[0]["log_level"] == "warning"
        assert events[0]["request_id"] == "missing-id"
        assert events[0]["operation"] == "get"
