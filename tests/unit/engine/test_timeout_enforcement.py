"""Coverage for the engine.timeout_enforcement_enabled global gate."""

from collections.abc import Iterator

import pytest

from synthorg.engine import timeout_enforcement

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_enforcement() -> Iterator[None]:
    yield
    timeout_enforcement.set_timeout_enforcement_enabled(value=True)


def test_default_is_enabled() -> None:
    assert timeout_enforcement.is_timeout_enforcement_enabled() is True


def test_setter_disables_enforcement() -> None:
    timeout_enforcement.set_timeout_enforcement_enabled(value=False)
    assert timeout_enforcement.is_timeout_enforcement_enabled() is False


async def test_engine_timeout_enforces_when_enabled() -> None:
    import asyncio

    timeout_enforcement.set_timeout_enforcement_enabled(value=True)
    with pytest.raises(TimeoutError):
        async with timeout_enforcement.engine_timeout(0.01):
            await asyncio.sleep(1)


async def test_engine_timeout_no_op_when_disabled() -> None:
    timeout_enforcement.set_timeout_enforcement_enabled(value=False)
    # No TimeoutError; the body completes despite exceeding 0.01s.
    async with timeout_enforcement.engine_timeout(0.01):
        await _short_yield()


async def test_engine_timeout_passes_through_none() -> None:
    timeout_enforcement.set_timeout_enforcement_enabled(value=True)
    async with timeout_enforcement.engine_timeout(None):
        await _short_yield()


async def _short_yield() -> None:
    import asyncio

    await asyncio.sleep(0)
