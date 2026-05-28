"""Tests for department controller."""

import json
from typing import Any

import pytest

from synthorg.config.schema import RootConfig
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)


@pytest.mark.unit
class TestDepartmentController:
    async def test_list_departments_empty(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/departments")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_get_department_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/departments/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_oversized_department_name_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        long_name = "x" * 129
        resp = await async_test_client.get(f"/api/v1/departments/{long_name}")
        assert resp.status_code == 400


@pytest.mark.integration
class TestDepartmentControllerDbOverride:
    """Test that DB-stored settings override YAML departments."""

    async def test_db_departments_override_config(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        from synthorg.api.app import create_app
        from synthorg.api.auth.service import AuthService
        from synthorg.budget.tracker import CostTracker
        from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

        config = RootConfig(company_name="test")
        auth_service: AuthService = _make_test_auth_service()
        _seed_test_users(fake_persistence, auth_service)
        settings_service = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
        )

        db_depts = [
            {"name": "db-dept", "head": "alice"},
        ]
        await settings_service.set("company", "departments", json.dumps(db_depts))

        app = create_app(
            config=config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            cost_tracker=CostTracker(),
            auth_service=auth_service,
            settings_service=settings_service,
        )
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = await client.get("/api/v1/departments")
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"][0]["name"] == "db-dept"

            detail_resp = await client.get("/api/v1/departments/db-dept")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["data"]["name"] == "db-dept"


@pytest.mark.integration
class TestDepartmentCeremonyPolicyCas:
    """Ceremony-policy overrides use settings-service CAS for cross-worker safety.

    Two concurrent writers must both land without lost updates, and a
    persistent CAS miss must surface as ``VersionConflictError`` after
    the bounded retry exhausts.
    """

    async def test_concurrent_overrides_both_land_no_lost_update(
        self,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Writer A and writer B both complete; final state contains both."""
        import asyncio

        from synthorg.api.controllers.departments import (
            _load_dept_policies_versioned,
            _mutate_dept_policies_with_retry,
        )

        settings_service = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
        )
        from tests._shared import make_app_state

        app_state = make_app_state(settings_service=settings_service)

        policy_a: dict[str, Any] = {"strategy": "task_driven"}
        policy_b: dict[str, Any] = {"strategy": "calendar"}

        # Drive both mutations concurrently.  One must win CAS first; the
        # loser observes VersionConflictError internally and retries.
        await asyncio.gather(
            _mutate_dept_policies_with_retry(app_state, "dept-a", policy_a),  # type: ignore[arg-type]
            _mutate_dept_policies_with_retry(app_state, "dept-b", policy_b),  # type: ignore[arg-type]
        )

        final, _ = await _load_dept_policies_versioned(app_state)  # type: ignore[arg-type]
        assert final == {"dept-a": policy_a, "dept-b": policy_b}

    async def test_retry_recovers_after_transient_version_conflict(
        self,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """A single CAS miss triggers retry + success, not a hard failure.

        Wraps ``settings_service.set`` with a side-effect that raises
        ``VersionConflictError`` on the first call and delegates to the
        real implementation afterwards.  This exercises the retry loop
        deterministically (no thread timing) and asserts the second
        attempt actually persists the mutation.
        """
        from unittest.mock import AsyncMock

        from synthorg.api.controllers.departments import (
            _load_dept_policies_versioned,
            _mutate_dept_policies_with_retry,
        )
        from synthorg.core.domain_errors import VersionConflictError

        settings_service = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
        )
        from tests._shared import make_app_state

        app_state = make_app_state(settings_service=settings_service)
        policy = {"strategy": "task_driven"}

        original_set = settings_service.set
        call_count = {"n": 0}

        async def flaky_set(*args: Any, **kwargs: Any) -> Any:
            call_count["n"] += 1
            if call_count["n"] == 1:
                msg = "transient conflict"
                raise VersionConflictError(msg)
            return await original_set(*args, **kwargs)

        settings_service.set = AsyncMock(  # type: ignore[method-assign]
            side_effect=flaky_set,
        )

        await _mutate_dept_policies_with_retry(
            app_state,  # type: ignore[arg-type]
            "dept-a",
            policy,
        )

        final, _ = await _load_dept_policies_versioned(app_state)  # type: ignore[arg-type]
        # ``dept-a`` must be persisted after the successful retry; any
        # entries from a prior test on the same fixture can coexist
        # because the focus of this test is retry semantics, not
        # tear-down.
        assert final.get("dept-a") == policy
        # Exactly one retry -- the first call conflicted, the second
        # landed.  Guards against a regression that either fails fast
        # without retrying or spins past the conflict.
        assert call_count["n"] == 2

    async def test_retry_exhausted_surfaces_version_conflict(
        self,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Sustained CAS misses surface the last conflict after retry cap.

        Also asserts the retry loop is bounded by
        ``_DEPT_POLICY_CAS_MAX_ATTEMPTS``.
        """
        from unittest.mock import AsyncMock

        from synthorg.api.controllers.departments import (
            _DEPT_POLICY_CAS_FALLBACK_ATTEMPTS,
            _mutate_dept_policies_with_retry,
        )
        from synthorg.core.domain_errors import VersionConflictError

        settings_service = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
        )
        # Force every set() to raise VersionConflictError so the retry
        # loop runs to exhaustion.
        set_mock = AsyncMock(side_effect=VersionConflictError("forced conflict"))
        settings_service.set = set_mock  # type: ignore[method-assign]
        # No config_resolver wired so the retry helper falls back to
        # the registered default attempts count, matching the
        # standalone construction path the test simulates.
        from tests._shared import make_app_state

        app_state = make_app_state(settings_service=settings_service)

        with pytest.raises(VersionConflictError):
            await _mutate_dept_policies_with_retry(
                app_state,  # type: ignore[arg-type]
                "dept-a",
                {"strategy": "task_driven"},
            )

        # Retry loop must be bounded exactly by the configured cap
        # (fallback constant since no resolver is wired).
        assert set_mock.await_count == _DEPT_POLICY_CAS_FALLBACK_ATTEMPTS

    async def test_resolver_provided_attempt_count_is_honored(
        self,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """CAS retry loop respects a resolver-provided budget.

        When ``app_state.config_resolver`` returns 7 for the dept
        policy CAS retry attempts setting, the mutation helper must
        run exactly 7 attempts before surfacing
        ``VersionConflictError``.
        """
        from unittest.mock import AsyncMock

        from synthorg.api.controllers.departments import (
            _mutate_dept_policies_with_retry,
        )
        from synthorg.core.domain_errors import VersionConflictError

        settings_service = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
        )
        set_mock = AsyncMock(side_effect=VersionConflictError("forced conflict"))
        settings_service.set = set_mock  # type: ignore[method-assign]

        resolver = AsyncMock()
        resolver.get_int = AsyncMock(return_value=7)
        from tests._shared import make_app_state

        app_state = make_app_state(
            settings_service=settings_service,
            config_resolver=resolver,
        )

        with pytest.raises(VersionConflictError):
            await _mutate_dept_policies_with_retry(
                app_state,  # type: ignore[arg-type]
                "dept-a",
                {"strategy": "task_driven"},
            )

        assert set_mock.await_count == 7
        resolver.get_int.assert_awaited_once_with(
            "coordination", "department_policy_cas_retry_attempts"
        )
