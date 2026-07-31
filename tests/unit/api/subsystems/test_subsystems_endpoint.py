"""Tests for the subsystem status endpoint."""

import pytest

from synthorg.api.subsystems.spec import SubsystemPhase
from tests._shared import LoopAsyncClient


@pytest.mark.unit
class TestSubsystemsEndpoint:
    """``/subsystems`` reports what is up and what each waiter needs."""

    async def test_rejects_unauthenticated(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # The set of subsystems a deployment is missing describes its
        # topology, so it sits behind auth like the health detail does.
        response = await async_test_client.get(
            "/api/v1/subsystems",
            headers={"Authorization": "Bearer not.a.valid.token"},
        )
        assert response.status_code in {401, 403}

    async def test_reports_every_declared_subsystem(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.get("/api/v1/subsystems")
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["subsystems"]
        counted = body["active"] + body["waiting"] + body["blocked"] + body["failed"]
        assert counted <= len(body["subsystems"])

    async def test_a_waiting_subsystem_names_what_it_needs(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.get("/api/v1/subsystems")
        waiting = [
            entry
            for entry in response.json()["data"]["subsystems"]
            if entry["phase"] == SubsystemPhase.WAITING.value
        ]
        # Whether anything is waiting depends on how much the test app
        # wires, but a waiting entry that names nothing would leave an
        # operator with the same dead end the bare 503 gave them. A
        # subsystem that has every dependency and declined anyway reports
        # ``blocked`` instead, precisely so it never lands here empty.
        for entry in waiting:
            assert entry["waiting_on"], entry["name"]

    async def test_reading_does_not_activate(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # A read that reconciled would make refreshing a page a cause of
        # change, so two reads in a row must report the same thing.
        first = await async_test_client.get("/api/v1/subsystems")
        second = await async_test_client.get("/api/v1/subsystems")
        assert first.json()["data"] == second.json()["data"]
