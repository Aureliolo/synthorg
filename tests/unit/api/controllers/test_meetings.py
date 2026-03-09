"""Tests for meeting controller."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002


@pytest.mark.unit
class TestMeetingController:
    def test_list_meetings_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/meetings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    def test_get_meeting_stub(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/meetings/any-id")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
