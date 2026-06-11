"""Shared fixtures for design tool tests."""

import pytest

from synthorg.tools.design.image_generator import ImageResult
from tests._shared import JsonDict


class MockImageProvider:
    """Mock image provider for testing."""

    def __init__(
        self,
        *,
        result: ImageResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result or ImageResult(
            data="iVBORw0KGgo=",
            content_type="image/png",
            width=1024,
            height=1024,
        )
        self._error = error
        self.calls: list[JsonDict] = []

    async def generate(
        self,
        *,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        style: str = "realistic",
        quality: str = "standard",
    ) -> ImageResult:
        self.calls.append(
            {
                "prompt": prompt,
                "width": width,
                "height": height,
                "style": style,
                "quality": quality,
            }
        )
        if self._error:
            raise self._error
        return self._result


@pytest.fixture
def mock_provider() -> MockImageProvider:
    return MockImageProvider()


@pytest.fixture
def failing_provider() -> MockImageProvider:
    return MockImageProvider(error=RuntimeError("provider unavailable"))
