"""Tests for the subsystem status endpoint."""

import pytest

from synthorg.api.controllers.subsystems import SubsystemReport
from synthorg.api.subsystems.reconciler import SubsystemReconciler
from synthorg.api.subsystems.registry import SUBSYSTEMS
from synthorg.api.subsystems.spec import SubsystemPhase
from tests._shared import LoopAsyncClient

_PHASE_COUNT_KEYS = (
    "active",
    "degraded",
    "waiting",
    "blocked",
    "failed",
    "disabled",
)


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

        reported = [entry["name"] for entry in body["subsystems"]]
        assert set(reported) == {spec.name for spec in SUBSYSTEMS}
        assert len(reported) == len(SUBSYSTEMS)

    async def test_phase_counts_account_for_every_subsystem(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        body = (await async_test_client.get("/api/v1/subsystems")).json()["data"]

        # Every phase is counted, so the counts partition the list exactly.
        # A subsystem in a phase no counter covers would be invisible on the
        # summary an operator reads first.
        expected = {
            key: sum(1 for entry in body["subsystems"] if entry["phase"] == key)
            for key in _PHASE_COUNT_KEYS
        }
        assert {key: body[key] for key in _PHASE_COUNT_KEYS} == expected
        assert sum(expected.values()) == len(body["subsystems"])

    async def test_a_waiting_subsystem_names_what_it_needs(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.get("/api/v1/subsystems")
        subsystems = response.json()["data"]["subsystems"]
        waiting = [
            entry
            for entry in subsystems
            if entry["phase"] == SubsystemPhase.WAITING.value
        ]
        # Asserted non-empty so the per-entry check below cannot pass by
        # having nothing to check: the test app wires no provider registry,
        # so the subsystems that need one must be waiting on it.
        assert waiting, [entry["phase"] for entry in subsystems]
        # A waiting entry that names nothing would leave an operator with the
        # same dead end the bare 503 gave them. A subsystem that has every
        # dependency and declined anyway reports ``blocked`` instead,
        # precisely so it never lands here empty.
        for entry in waiting:
            assert entry["waiting_on"], entry["name"]

    def test_a_degraded_report_may_name_its_missing_requirement(self) -> None:
        # A degraded subsystem is up with a requirement gone, so it carries
        # the same waiting_on a waiting one does. A response model that
        # allowed it only on waiting would reject the report and turn the
        # endpoint into a 500 in exactly the case an operator opened it for.
        report = SubsystemReport(
            name="memory_backend",
            phase=SubsystemPhase.DEGRADED,
            waiting_on=("persistence",),
        )
        assert report.waiting_on == ("persistence",)

    async def test_reading_does_not_activate(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Equal responses are not enough: reconciliation is idempotent, so a
        # read that reconciled would return the same body and still make
        # refreshing a page a cause of change. Refuse the call outright.
        async def _refuse(*_args: object, **_kwargs: object) -> None:
            msg = "the status endpoint must not reconcile"
            raise AssertionError(msg)

        monkeypatch.setattr(SubsystemReconciler, "reconcile", _refuse)

        first = await async_test_client.get("/api/v1/subsystems")
        second = await async_test_client.get("/api/v1/subsystems")

        assert first.status_code == 200
        assert first.json()["data"] == second.json()["data"]
