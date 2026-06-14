"""Unit tests for quality MCP handlers.

Covers 9 tools: quality (3), reviews (4), evaluation versions (2).
"""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.api.state import AppState
from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.engine.quality.mcp_services import ReviewFacadeService
from synthorg.engine.state import EngineStateSlice
from synthorg.infrastructure.state import FacadesStateSlice
from synthorg.meta.mcp.handlers.quality import QUALITY_HANDLERS
from tests._shared import make_app_state
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_quality() -> AsyncMock:
    service = AsyncMock()
    service.get_summary = AsyncMock(return_value={"avg": 8.0})
    service.get_agent_quality = AsyncMock(return_value={"agent_id": "a1"})
    service.list_scores = AsyncMock(return_value=((), 0))
    return service


@pytest.fixture
def fake_eval_versions() -> AsyncMock:
    service = AsyncMock()
    service.list_versions = AsyncMock(return_value=())
    service.get_version = AsyncMock(return_value=None)
    return service


@pytest.fixture
def real_reviews() -> ReviewFacadeService:
    return ReviewFacadeService()


@pytest.fixture
def fake_app_state(
    fake_quality: AsyncMock,
    real_reviews: ReviewFacadeService,
    fake_eval_versions: AsyncMock,
) -> AppState:
    return make_app_state(
        slices={
            FacadesStateSlice: {
                "quality_facade_service": fake_quality,
                "review_facade_service": real_reviews,
            },
            EngineStateSlice: {"evaluation_version_service": fake_eval_versions},
        },
    )


class TestQuality:
    async def test_summary(self, fake_app_state: AppState) -> None:
        handler = QUALITY_HANDLERS["synthorg_quality_get_summary"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_agent_quality(self, fake_app_state: AppState) -> None:
        handler = QUALITY_HANDLERS["synthorg_quality_get_agent_quality"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"agent_id": "a1"},
        )
        assert json.loads(response)["status"] == "ok"

    async def test_list_scores(self, fake_app_state: AppState) -> None:
        handler = QUALITY_HANDLERS["synthorg_quality_list_scores"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"] == []

    async def test_summary_capability_gap(
        self,
        fake_app_state: AppState,
        fake_quality: AsyncMock,
    ) -> None:
        fake_quality.get_summary = AsyncMock(
            side_effect=CapabilityNotSupportedError("quality_summary", "x"),
        )
        handler = QUALITY_HANDLERS["synthorg_quality_get_summary"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["domain_code"] == "not_supported"


class TestReviews:
    async def test_create_and_get(self, fake_app_state: AppState) -> None:
        create = QUALITY_HANDLERS["synthorg_reviews_create"]
        response = await create(
            app_state=fake_app_state,
            arguments={"task_id": "t1", "verdict": "approved"},
            actor=make_test_actor(),
        )
        data = json.loads(response)["data"]
        review_id = data["id"]
        get_handler = QUALITY_HANDLERS["synthorg_reviews_get"]
        response_get = await get_handler(
            app_state=fake_app_state,
            arguments={"review_id": review_id},
        )
        assert json.loads(response_get)["status"] == "ok"

    async def test_list(self, fake_app_state: AppState) -> None:
        handler = QUALITY_HANDLERS["synthorg_reviews_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"] == []

    async def test_update_not_found(self, fake_app_state: AppState) -> None:
        handler = QUALITY_HANDLERS["synthorg_reviews_update"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"review_id": str(uuid4()), "verdict": "rejected"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "not_found"


class TestEvaluationVersions:
    async def test_list(self, fake_app_state: AppState) -> None:
        handler = QUALITY_HANDLERS["synthorg_evaluation_versions_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"] == []

    async def test_get_not_found(self, fake_app_state: AppState) -> None:
        handler = QUALITY_HANDLERS["synthorg_evaluation_versions_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"version_id": "v1"},
        )
        assert json.loads(response)["domain_code"] == "not_found"
