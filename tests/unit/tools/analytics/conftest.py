"""Shared fixtures for analytics tool tests."""

import pytest

from synthorg.tools.analytics.config import AnalyticsToolsConfig
from tests._shared import JsonDict


class MockAnalyticsProvider:
    """Mock analytics provider for testing."""

    def __init__(
        self,
        *,
        result: JsonDict | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = (
            {
                "total_cost": 1234.56,
                "task_count": 42,
                "active_agents": 5,
            }
            if result is None
            else result
        )
        self._error = error
        self.calls: list[JsonDict] = []

    async def query(
        self,
        *,
        metrics: list[str],
        period: str,
        group_by: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> JsonDict:
        self.calls.append(
            {
                "metrics": metrics,
                "period": period,
                "group_by": group_by,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        if self._error:
            raise self._error
        return self._result


class MockMetricSink:
    """Mock metric sink for testing."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
    ) -> None:
        self._error = error
        self.recorded: list[JsonDict] = []

    async def record(
        self,
        *,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
        unit: str | None = None,
    ) -> None:
        self.recorded.append(
            {
                "name": name,
                "value": value,
                "tags": tags,
                "unit": unit,
            }
        )
        if self._error:
            raise self._error


@pytest.fixture
def default_config() -> AnalyticsToolsConfig:
    return AnalyticsToolsConfig()


@pytest.fixture
def restricted_config() -> AnalyticsToolsConfig:
    return AnalyticsToolsConfig(allowed_metrics=frozenset({"total_cost", "task_count"}))


@pytest.fixture
def mock_provider() -> MockAnalyticsProvider:
    return MockAnalyticsProvider()


@pytest.fixture
def failing_provider() -> MockAnalyticsProvider:
    return MockAnalyticsProvider(error=RuntimeError("query failed"))


@pytest.fixture
def mock_sink() -> MockMetricSink:
    return MockMetricSink()


@pytest.fixture
def failing_sink() -> MockMetricSink:
    return MockMetricSink(error=RuntimeError("sink error"))
