# mypy: disable-error-code="explicit-any,unused-awaitable"
"""Unit tests for budget domain MCP handlers."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.meta.mcp.handlers.budget import BUDGET_HANDLERS

pytestmark = pytest.mark.unit


@pytest.fixture
def config() -> SimpleNamespace:
    return SimpleNamespace(
        currency="EUR",
        model_dump=lambda mode="json": {"currency": "EUR"},
    )


@pytest.fixture
def fake_app_state(config: SimpleNamespace) -> SimpleNamespace:
    tracker = AsyncMock()
    tracker.get_records.return_value = ()
    tracker.get_agent_cost.return_value = 12.34

    config_resolver = AsyncMock()
    config_resolver.get_budget_config.return_value = config

    versions_repo = AsyncMock()
    versions_repo.list_versions.return_value = ()
    versions_repo.count_versions.return_value = 0
    versions_repo.get_version.return_value = None

    persistence = SimpleNamespace(budget_config_versions=versions_repo)

    return SimpleNamespace(
        cost_tracker=tracker,
        config_resolver=config_resolver,
        persistence=persistence,
    )


def _parse(result: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(result)
    assert body["status"] in {"ok", "error"}
    return body


class TestBudgetGetConfig:
    async def test_happy(self, fake_app_state: SimpleNamespace) -> None:
        body = _parse(
            await BUDGET_HANDLERS["synthorg_budget_get_config"](
                app_state=fake_app_state,
                arguments={},
                actor=None,
            ),
        )
        assert body["status"] == "ok"
        assert body["data"] == {"currency": "EUR"}


class TestBudgetListRecords:
    async def test_empty(self, fake_app_state: SimpleNamespace) -> None:
        body = _parse(
            await BUDGET_HANDLERS["synthorg_budget_list_records"](
                app_state=fake_app_state,
                arguments={},
                actor=None,
            ),
        )
        assert body["status"] == "ok"
        assert body["data"] == []

    async def test_invalid_agent_id(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        body = _parse(
            await BUDGET_HANDLERS["synthorg_budget_list_records"](
                app_state=fake_app_state,
                arguments={"agent_id": "   "},
                actor=None,
            ),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"


class TestBudgetGetAgentSpending:
    async def test_happy(self, fake_app_state: SimpleNamespace) -> None:
        body = _parse(
            await BUDGET_HANDLERS["synthorg_budget_get_agent_spending"](
                app_state=fake_app_state,
                arguments={"agent_id": "alpha"},
                actor=None,
            ),
        )
        assert body["status"] == "ok"
        assert body["data"] == {
            "agent_id": "alpha",
            "total_cost": 12.34,
            "currency": "EUR",
        }

    async def test_missing_agent_id(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        body = _parse(
            await BUDGET_HANDLERS["synthorg_budget_get_agent_spending"](
                app_state=fake_app_state,
                arguments={},
                actor=None,
            ),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"


class TestBudgetVersionsList:
    async def test_empty(self, fake_app_state: SimpleNamespace) -> None:
        body = _parse(
            await BUDGET_HANDLERS["synthorg_budget_versions_list"](
                app_state=fake_app_state,
                arguments={},
                actor=None,
            ),
        )
        assert body["status"] == "ok"


class TestBudgetVersionsGet:
    async def test_not_found(self, fake_app_state: SimpleNamespace) -> None:
        body = _parse(
            await BUDGET_HANDLERS["synthorg_budget_versions_get"](
                app_state=fake_app_state,
                arguments={"version_num": 42},
                actor=None,
            ),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "not_found"

    async def test_missing_version_num(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        body = _parse(
            await BUDGET_HANDLERS["synthorg_budget_versions_get"](
                app_state=fake_app_state,
                arguments={},
                actor=None,
            ),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"

    async def test_rejects_zero_version(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        body = _parse(
            await BUDGET_HANDLERS["synthorg_budget_versions_get"](
                app_state=fake_app_state,
                arguments={"version_num": 0},
                actor=None,
            ),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"
