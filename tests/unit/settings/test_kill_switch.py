"""Coverage for the shared kill-switch resolver helper."""

from unittest.mock import AsyncMock

import pytest

from synthorg.settings.kill_switch import (
    resolve_bool_with_fallback,
    resolve_float_with_fallback,
    resolve_model_with_fallback,
    resolve_str_with_fallback,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of

pytestmark = pytest.mark.unit


async def test_returns_fallback_when_resolver_missing() -> None:
    result = await resolve_bool_with_fallback(
        resolver=None,
        namespace="engine",
        key="evolution_enabled",
        fallback=True,
    )
    assert result is True


async def test_returns_resolver_value_when_wired() -> None:
    resolver = mock_of[ConfigResolverProtocol](get_bool=AsyncMock(return_value=False))
    result = await resolve_bool_with_fallback(
        resolver=resolver,
        namespace="engine",
        key="evolution_enabled",
        fallback=True,
    )
    assert result is False
    resolver.get_bool.assert_awaited_once_with("engine", "evolution_enabled")


async def test_resolver_outage_falls_back() -> None:
    resolver = mock_of[ConfigResolverProtocol](
        get_bool=AsyncMock(side_effect=RuntimeError("transient"))
    )
    result = await resolve_bool_with_fallback(
        resolver=resolver,
        namespace="engine",
        key="evolution_enabled",
        fallback=True,
    )
    assert result is True


async def test_float_returns_fallback_when_resolver_missing() -> None:
    result = await resolve_float_with_fallback(
        resolver=None,
        namespace="hr",
        key="eval_loop_cycle_interval_seconds",
        fallback=86400.0,
    )
    assert result == 86400.0


async def test_float_returns_resolver_value_when_wired() -> None:
    resolver = mock_of[ConfigResolverProtocol](get_float=AsyncMock(return_value=120.0))
    result = await resolve_float_with_fallback(
        resolver=resolver,
        namespace="hr",
        key="eval_loop_cycle_interval_seconds",
        fallback=86400.0,
    )
    assert result == 120.0
    resolver.get_float.assert_awaited_once_with(
        "hr", "eval_loop_cycle_interval_seconds"
    )


async def test_float_resolver_outage_falls_back() -> None:
    resolver = mock_of[ConfigResolverProtocol](
        get_float=AsyncMock(side_effect=RuntimeError("transient"))
    )
    result = await resolve_float_with_fallback(
        resolver=resolver,
        namespace="hr",
        key="eval_loop_cycle_window_hours",
        fallback=168.0,
    )
    assert result == 168.0


@pytest.mark.parametrize(
    "exc_type",
    [MemoryError, RecursionError],
    ids=["memory_error", "recursion_error"],
)
async def test_system_errors_propagate(exc_type: type[BaseException]) -> None:
    """Both ``MemoryError`` and ``RecursionError`` re-raise unchanged.

    The PEP 758 ``except MemoryError, RecursionError: raise`` clause in
    ``resolve_bool_with_fallback`` is the contract that lets the
    interpreter surface unrecoverable conditions; covering each type
    independently catches a regression that would otherwise demote one
    of them to a silent fallback.
    """
    resolver = mock_of[ConfigResolverProtocol](
        get_bool=AsyncMock(side_effect=exc_type())
    )
    with pytest.raises(exc_type):
        await resolve_bool_with_fallback(
            resolver=resolver,
            namespace="engine",
            key="evolution_enabled",
            fallback=True,
        )


async def test_str_returns_fallback_when_resolver_missing() -> None:
    result = await resolve_str_with_fallback(
        resolver=None,
        namespace="chief_of_staff",
        key="chat_model",
        fallback="baked-model",
    )
    assert result == "baked-model"


async def test_str_returns_resolver_value_when_wired() -> None:
    resolver = AsyncMock()
    resolver.get_str = AsyncMock(return_value="live-model")
    result = await resolve_str_with_fallback(
        resolver=resolver,
        namespace="chief_of_staff",
        key="chat_model",
        fallback="baked-model",
    )
    assert result == "live-model"
    resolver.get_str.assert_awaited_once_with("chief_of_staff", "chat_model")


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"], ids=["empty", "spaces", "ws"])
async def test_str_blank_resolves_to_fallback(blank: str) -> None:
    """A blank setting means "keep the built-in default".

    The overlay skips a blank model override, so the live read must agree:
    a blank value falls back to the baked model rather than blanking the
    active model identifier.
    """
    resolver = AsyncMock()
    resolver.get_str = AsyncMock(return_value=blank)
    result = await resolve_str_with_fallback(
        resolver=resolver,
        namespace="chief_of_staff",
        key="chat_model",
        fallback="baked-model",
    )
    assert result == "baked-model"


async def test_str_resolver_outage_falls_back() -> None:
    resolver = AsyncMock()
    resolver.get_str = AsyncMock(side_effect=RuntimeError("transient"))
    result = await resolve_str_with_fallback(
        resolver=resolver,
        namespace="chief_of_staff",
        key="chat_model",
        fallback="baked-model",
    )
    assert result == "baked-model"


@pytest.mark.parametrize(
    "exc_type",
    [MemoryError, RecursionError],
    ids=["memory_error", "recursion_error"],
)
async def test_str_system_errors_propagate(exc_type: type[BaseException]) -> None:
    resolver = AsyncMock()
    resolver.get_str = AsyncMock(side_effect=exc_type())
    with pytest.raises(exc_type):
        await resolve_str_with_fallback(
            resolver=resolver,
            namespace="chief_of_staff",
            key="chat_model",
            fallback="baked-model",
        )


@pytest.mark.parametrize(
    "exc_type",
    [MemoryError, RecursionError],
    ids=["memory_error", "recursion_error"],
)
async def test_float_system_errors_propagate(exc_type: type[BaseException]) -> None:
    """The float helper re-raises ``MemoryError`` / ``RecursionError`` unchanged.

    Mirrors the bool-helper contract: an unrecoverable interpreter condition
    must never be demoted to the last-known-good cadence fallback.
    """
    resolver = mock_of[ConfigResolverProtocol](
        get_float=AsyncMock(side_effect=exc_type())
    )
    with pytest.raises(exc_type):
        await resolve_float_with_fallback(
            resolver=resolver,
            namespace="hr",
            key="eval_loop_cycle_interval_seconds",
            fallback=86400.0,
        )


async def test_model_returns_clean_live_value() -> None:
    resolver = AsyncMock()
    resolver.get_str = AsyncMock(return_value="provider/live-model:tag")
    result = await resolve_model_with_fallback(
        resolver=resolver,
        namespace="chief_of_staff",
        key="chat_model",
        fallback="baked-model",
    )
    assert result == "provider/live-model:tag"


async def test_model_blank_resolves_to_fallback() -> None:
    resolver = AsyncMock()
    resolver.get_str = AsyncMock(return_value="   ")
    result = await resolve_model_with_fallback(
        resolver=resolver,
        namespace="chief_of_staff",
        key="chat_model",
        fallback="baked-model",
    )
    assert result == "baked-model"


@pytest.mark.parametrize(
    "malformed",
    [
        "live\nmodel",
        "model\twith\ttabs",
        " surrounded ",
        "provider/ model",
        "gpt 4.1",
        "x" * 257,
    ],
    ids=["newline", "tabs", "surrounding_ws", "embedded_space", "spaced", "too_long"],
)
async def test_model_malformed_falls_back(malformed: str) -> None:
    """A control-laden / oversized / untrimmed model id falls back, not passes.

    The structural guard keeps a corrupted settings store from injecting a
    malformed identifier straight into a provider call; a clean custom token
    still passes (operators are not constrained to an allowlist).
    """
    resolver = AsyncMock()
    resolver.get_str = AsyncMock(return_value=malformed)
    result = await resolve_model_with_fallback(
        resolver=resolver,
        namespace="chief_of_staff",
        key="chat_model",
        fallback="baked-model",
    )
    assert result == "baked-model"
