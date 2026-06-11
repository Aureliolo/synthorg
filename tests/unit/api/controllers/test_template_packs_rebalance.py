"""Tests for budget rebalancing on template pack application."""

import json
from dataclasses import dataclass, field
from unittest.mock import patch

import httpx
import pytest

from synthorg.templates.schema import TemplateDepartmentConfig
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers


async def _seed_departments(
    async_test_client: LoopAsyncClient,
    depts: list[dict[str, str | float]],
) -> None:
    """Seed departments into settings."""
    resp = await async_test_client.put(
        "/api/v1/settings/company/departments",
        json={"value": json.dumps(depts)},
        headers=make_auth_headers("ceo"),
    )
    assert resp.status_code == 200, f"Failed to seed departments: {resp.text}"


def _dept(name: str, budget: float) -> dict[str, str | float]:
    return {"name": name, "budget_percent": budget}


_FAKE_PACK_DEPT_BUDGET = 8.0
_FAKE_PACK_NAME = "test-pack"


@dataclass(frozen=True)
class _FakeTemplate:
    """Minimal stand-in for CompanyTemplate with departments + agents."""

    departments: tuple[TemplateDepartmentConfig, ...] = ()
    agents: tuple[object, ...] = ()


@dataclass(frozen=True)
class _FakeLoadedTemplate:
    """Minimal stand-in for LoadedTemplate."""

    template: _FakeTemplate = field(default_factory=_FakeTemplate)
    raw_yaml: str = ""
    source_name: str = "test"


def _make_fake_loaded(
    dept_budget: float = _FAKE_PACK_DEPT_BUDGET,
) -> _FakeLoadedTemplate:
    """Create a fake LoadedTemplate with one department."""
    dept = TemplateDepartmentConfig(
        name="test-dept",
        budget_percent=dept_budget,
    )
    return _FakeLoadedTemplate(
        template=_FakeTemplate(departments=(dept,)),
    )


@pytest.mark.unit
class TestPackApplyRebalance:
    """Tests for rebalance_mode on POST /template-packs/apply."""

    async def _apply(
        self,
        async_test_client: LoopAsyncClient,
        pack_name: str = _FAKE_PACK_NAME,
        rebalance_mode: str | None = None,
        dept_budget: float = _FAKE_PACK_DEPT_BUDGET,
    ) -> httpx.Response:
        """Apply a template pack with optional rebalance_mode."""
        body: dict[str, str] = {"pack_name": pack_name}
        if rebalance_mode is not None:
            body["rebalance_mode"] = rebalance_mode
        fake = _make_fake_loaded(dept_budget)
        with (
            patch(
                "synthorg.api.controllers.template_packs.load_pack",
                return_value=fake,
            ),
            patch(
                "synthorg.api.controllers.template_packs.expand_template_agents",
                return_value=[],
            ),
        ):
            return await async_test_client.post(
                "/api/v1/template-packs/apply",
                json=body,
            )

    async def test_default_mode_is_scale_existing(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept("eng", 60), _dept("prod", 40)],
        )
        resp = await self._apply(async_test_client)
        assert resp.status_code == 201
        body = resp.json()["data"]
        assert body["rebalance_mode"] == "scale_existing"
        assert body["scale_factor"] is not None

    async def test_scale_existing_reduces_budgets(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept("eng", 60), _dept("prod", 40)],
        )
        resp = await self._apply(async_test_client, rebalance_mode="scale_existing")
        assert resp.status_code == 201
        body = resp.json()["data"]
        assert body["budget_before"] == pytest.approx(100.0, abs=0.1)
        assert body["budget_after"] == pytest.approx(100.0, abs=0.1)
        assert body["scale_factor"] == pytest.approx(0.92, abs=0.01)

    async def test_scale_existing_no_scaling_when_under_budget(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept("eng", 50), _dept("prod", 30)],
        )
        resp = await self._apply(async_test_client, rebalance_mode="scale_existing")
        assert resp.status_code == 201
        body = resp.json()["data"]
        assert body["scale_factor"] == 1.0
        assert body["budget_after"] == pytest.approx(88.0, abs=0.1)

    async def test_reject_if_over_returns_409(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept("eng", 60), _dept("prod", 40)],
        )
        resp = await self._apply(async_test_client, rebalance_mode="reject_if_over")
        assert resp.status_code == 409

    async def test_reject_if_over_under_100_succeeds(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept("eng", 50), _dept("prod", 30)],
        )
        resp = await self._apply(async_test_client, rebalance_mode="reject_if_over")
        assert resp.status_code == 201

    async def test_none_mode_no_adjustment(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(
            async_test_client,
            [_dept("eng", 60), _dept("prod", 40)],
        )
        resp = await self._apply(async_test_client, rebalance_mode="none")
        assert resp.status_code == 201
        body = resp.json()["data"]
        assert body["scale_factor"] is None
        assert body["budget_after"] == pytest.approx(108.0, abs=0.1)

    async def test_response_includes_budget_fields(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await _seed_departments(async_test_client, [_dept("eng", 60)])
        resp = await self._apply(async_test_client)
        assert resp.status_code == 201
        body = resp.json()["data"]
        assert "budget_before" in body
        assert "budget_after" in body
        assert "rebalance_mode" in body
        assert "scale_factor" in body

    async def test_backward_compatible_without_rebalance_mode(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Omitting rebalance_mode defaults to scale_existing."""
        await _seed_departments(
            async_test_client,
            [_dept("eng", 60), _dept("prod", 40)],
        )
        resp = await self._apply(async_test_client)
        assert resp.status_code == 201
        assert resp.json()["data"]["rebalance_mode"] == "scale_existing"

    async def test_no_existing_departments(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await self._apply(async_test_client)
        assert resp.status_code == 201
        body = resp.json()["data"]
        assert body["budget_before"] == 0
        assert body["budget_after"] == pytest.approx(
            _FAKE_PACK_DEPT_BUDGET,
            abs=0.1,
        )
