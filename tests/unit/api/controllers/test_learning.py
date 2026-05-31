# module-kind: tests
"""Tests for the learning controller -- ``GET /learning/curve``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.meta.learning_curve import ScorecardSummary, append_summary
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient, build_test_app
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    _make_test_auth_service,
    _seed_test_users,
    make_auth_headers,
)

pytestmark = pytest.mark.unit

_HEADERS = make_auth_headers("ceo")
_BASE = datetime(2026, 1, 1, tzinfo=UTC)
_MAX_TOTAL = 100
_PASS_FLOOR = 60


def _write_history(history_dir: Path, totals: list[int]) -> None:
    """Write one rising/falling run summary per total into *history_dir*."""
    for index, total in enumerate(totals):
        append_summary(
            history_dir,
            ScorecardSummary(
                run_label=NotBlankStr(f"run-{index:03d}"),
                generated_at=_BASE + timedelta(hours=index),
                total=total,
                max_total=_MAX_TOTAL,
                is_passing=total >= _PASS_FLOOR,
            ),
        )


@asynccontextmanager
async def _client_with_history(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    *,
    history_dir: Path,
) -> AsyncIterator[LoopAsyncClient]:
    """Yield a client whose ``meta.scorecard_history_dir`` points at *history_dir*."""
    auth_service = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )
    await settings_service.set("meta", "scorecard_history_dir", str(history_dir))
    app = build_test_app(
        config=RootConfig(company_name="test"),
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        settings_service=settings_service,
    )
    async with LoopAsyncClient(app) as client:
        yield client


class TestLearningController:
    async def test_curve_empty_when_history_unset(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """An unset history directory yields an empty curve, not a 503."""
        resp = await async_test_client.get("/api/v1/learning/curve", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["points"] == []
        assert data["has_regression"] is False
        assert data["latest_total"] is None

    async def test_requires_read_access(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/learning/curve",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401

    async def test_curve_returns_recorded_runs(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
        tmp_path: Path,
    ) -> None:
        """Recorded runs come back chronologically with deltas + score fractions."""
        _write_history(tmp_path, [20, 50, 80])
        async with _client_with_history(
            fake_persistence, fake_message_bus, history_dir=tmp_path
        ) as client:
            resp = await client.get("/api/v1/learning/curve", headers=_HEADERS)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [p["total"] for p in data["points"]] == [20, 50, 80]
        assert [p["delta"] for p in data["points"]] == [0, 30, 30]
        assert data["points"][-1]["score_fraction"] == pytest.approx(0.8)
        assert data["has_regression"] is False
        assert data["latest_total"] == 80

    async def test_curve_flags_regression(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
        tmp_path: Path,
    ) -> None:
        """A sharp drop between runs is surfaced as a regression."""
        _write_history(tmp_path, [20, 80, 10])
        async with _client_with_history(
            fake_persistence, fake_message_bus, history_dir=tmp_path
        ) as client:
            resp = await client.get("/api/v1/learning/curve", headers=_HEADERS)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["has_regression"] is True
        assert data["points"][-1]["is_regression"] is True
        assert data["points"][1]["is_regression"] is False
