"""Daily webhook-receipt cleanup loop: per-connection retention semantics.

The loop is modelled on :func:`_audit_retention_loop` but iterates
connections individually because each connection can override the
global retention window via ``Connection.webhook_receipt_retention_days``.

These tests cover the per-tick behaviour (resolution + per-connection
sweep + failure isolation), not the wall-clock loop scheduling.
"""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api import webhook_cleanup
from synthorg.core.types import NotBlankStr
from synthorg.persistence.connection_protocol import (
    ConnectionRepository,
    WebhookReceiptRepository,
)
from synthorg.settings.resolver import ConfigResolver
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


def _make_connection(
    name: str,
    *,
    retention_days: int | None = None,
) -> SimpleNamespace:
    """Stand-in :class:`Connection` carrying only the fields the loop reads."""
    return SimpleNamespace(
        name=NotBlankStr(name),
        webhook_receipt_retention_days=retention_days,
    )


def _build_app_state(  # noqa: PLR0913 -- each kwarg controls a distinct stub axis
    *,
    has_persistence: bool = True,
    has_config_resolver: bool = True,
    default_retention_days: int = 90,
    connections: list[Any] | None = None,
    cleanup_side_effects: dict[str, type[BaseException] | BaseException] | None = None,
    list_all_side_effect: type[BaseException] | BaseException | None = None,
) -> SimpleNamespace:
    """Build a minimal AppState stand-in for the cleanup loop."""
    config_resolver = AsyncMock(spec=ConfigResolver)
    config_resolver.get_int.return_value = default_retention_days
    connections_repo = AsyncMock(spec=ConnectionRepository)
    if list_all_side_effect is not None:
        connections_repo.list_all.side_effect = list_all_side_effect
    else:
        connections_repo.list_all.return_value = tuple(connections or [])
    webhook_repo = AsyncMock(spec=WebhookReceiptRepository)

    side_effects = cleanup_side_effects or {}

    async def _cleanup(connection_name: NotBlankStr, retention_days: int) -> int:
        side = side_effects.get(str(connection_name))
        if side is not None:
            raise side
        return 1

    webhook_repo.cleanup_old_for_connection = AsyncMock(side_effect=_cleanup)
    persistence = SimpleNamespace(
        connections=connections_repo,
        webhook_receipts=webhook_repo,
    )
    return SimpleNamespace(
        config_resolver=config_resolver,
        has_config_resolver=has_config_resolver,
        has_persistence=has_persistence,
        persistence=persistence,
    )


async def test_tick_skips_when_persistence_absent() -> None:
    """``has_persistence=False`` short-circuits without touching anything."""
    app_state = _build_app_state(has_persistence=False)

    await webhook_cleanup._webhook_receipt_cleanup_tick(app_state)  # type: ignore[arg-type]

    app_state.persistence.connections.list_all.assert_not_awaited()
    app_state.persistence.webhook_receipts.cleanup_old_for_connection.assert_not_awaited()


async def test_tick_uses_global_default_for_unconfigured_connection() -> None:
    app_state = _build_app_state(
        default_retention_days=90,
        connections=[_make_connection("github-bot", retention_days=None)],
    )

    await webhook_cleanup._webhook_receipt_cleanup_tick(app_state)  # type: ignore[arg-type]

    repo = app_state.persistence.webhook_receipts
    repo.cleanup_old_for_connection.assert_awaited_once()
    args, _ = repo.cleanup_old_for_connection.call_args
    assert str(args[0]) == "github-bot"
    assert args[1] == 90


async def test_tick_applies_per_connection_override() -> None:
    app_state = _build_app_state(
        default_retention_days=90,
        connections=[
            _make_connection("github-bot", retention_days=14),
            _make_connection("slack-bot", retention_days=None),
        ],
    )

    await webhook_cleanup._webhook_receipt_cleanup_tick(app_state)  # type: ignore[arg-type]

    repo = app_state.persistence.webhook_receipts
    assert repo.cleanup_old_for_connection.await_count == 2
    invocations = sorted(
        (str(c.args[0]), c.args[1])
        for c in repo.cleanup_old_for_connection.call_args_list
    )
    assert invocations == [("github-bot", 14), ("slack-bot", 90)]


async def test_tick_skips_zero_retention_connection() -> None:
    """``retention_days = 0`` opts the connection out entirely."""
    app_state = _build_app_state(
        default_retention_days=90,
        connections=[
            _make_connection("opt-out", retention_days=0),
            _make_connection("normal", retention_days=None),
        ],
    )

    await webhook_cleanup._webhook_receipt_cleanup_tick(app_state)  # type: ignore[arg-type]

    repo = app_state.persistence.webhook_receipts
    repo.cleanup_old_for_connection.assert_awaited_once()
    args, _ = repo.cleanup_old_for_connection.call_args
    assert str(args[0]) == "normal"


async def test_tick_skips_all_when_global_default_is_zero() -> None:
    """Setting ``integrations.webhook_receipt_retention_days=0`` disables everything."""
    app_state = _build_app_state(
        default_retention_days=0,
        connections=[
            _make_connection("a", retention_days=None),
            _make_connection("b", retention_days=None),
        ],
    )

    await webhook_cleanup._webhook_receipt_cleanup_tick(app_state)  # type: ignore[arg-type]

    app_state.persistence.webhook_receipts.cleanup_old_for_connection.assert_not_awaited()


async def test_tick_failure_in_one_connection_does_not_abort_others() -> None:
    class _BoomError(RuntimeError):
        pass

    app_state = _build_app_state(
        default_retention_days=30,
        connections=[
            _make_connection("flaky", retention_days=None),
            _make_connection("healthy", retention_days=None),
        ],
        cleanup_side_effects={"flaky": _BoomError("repo died")},
    )

    await webhook_cleanup._webhook_receipt_cleanup_tick(app_state)  # type: ignore[arg-type]

    repo = app_state.persistence.webhook_receipts
    # Both connections were attempted, even though the first raised.
    assert repo.cleanup_old_for_connection.await_count == 2


async def test_tick_memory_error_propagates() -> None:
    app_state = _build_app_state(
        default_retention_days=30,
        connections=[_make_connection("any", retention_days=None)],
        cleanup_side_effects={"any": MemoryError},
    )

    with pytest.raises(MemoryError):
        await webhook_cleanup._webhook_receipt_cleanup_tick(app_state)  # type: ignore[arg-type]


async def test_tick_cancellation_propagates() -> None:
    app_state = _build_app_state(
        default_retention_days=30,
        connections=[_make_connection("any", retention_days=None)],
        cleanup_side_effects={"any": asyncio.CancelledError},
    )

    with pytest.raises(asyncio.CancelledError):
        await webhook_cleanup._webhook_receipt_cleanup_tick(app_state)  # type: ignore[arg-type]


async def test_tick_swallows_list_all_failure() -> None:
    """Failure to list connections logs and returns; loop survives."""
    app_state = _build_app_state(
        list_all_side_effect=RuntimeError("list connections failed"),
    )

    await webhook_cleanup._webhook_receipt_cleanup_tick(app_state)  # type: ignore[arg-type]

    app_state.persistence.webhook_receipts.cleanup_old_for_connection.assert_not_awaited()


async def test_resolve_falls_back_when_no_config_resolver() -> None:
    app_state = _build_app_state(has_config_resolver=False)

    days = await webhook_cleanup._resolve_webhook_receipt_retention(app_state)  # type: ignore[arg-type]

    assert days == webhook_cleanup._DEFAULT_WEBHOOK_RECEIPT_RETENTION_DAYS


async def test_resolve_falls_back_on_resolver_error() -> None:
    config_resolver = SimpleNamespace(
        get_int=AsyncMock(side_effect=RuntimeError("settings backend down")),
    )
    app_state = SimpleNamespace(
        has_config_resolver=True,
        config_resolver=config_resolver,
    )

    days = await webhook_cleanup._resolve_webhook_receipt_retention(app_state)  # type: ignore[arg-type]

    assert days == webhook_cleanup._DEFAULT_WEBHOOK_RECEIPT_RETENTION_DAYS


async def test_loop_drives_tick_at_each_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the loop deterministically via :class:`FakeClock`.

    Uses the project's standard ``Clock`` seam (``synthorg.core.clock``)
    rather than monkey-patching ``asyncio.sleep`` so the test exercises
    the same injection path production code uses for time-driven
    behaviour.
    """
    clock = FakeClock()
    tick_count = 0

    async def _stub_tick(_app_state: Any) -> None:
        nonlocal tick_count
        tick_count += 1

    monkeypatch.setattr(
        "synthorg.api.webhook_cleanup._webhook_receipt_cleanup_tick",
        _stub_tick,
    )

    app_state = _build_app_state()
    task = asyncio.create_task(
        webhook_cleanup._webhook_receipt_cleanup_loop(
            app_state,  # type: ignore[arg-type]
            clock=clock,
        ),
    )
    # Allow the first tick to run, then drive two cycles of sleep.
    await asyncio.sleep(0)
    assert tick_count == 1
    await clock.advance_async(webhook_cleanup._WEBHOOK_RECEIPT_CLEANUP_TICK_SECONDS)
    assert tick_count == 2
    await clock.advance_async(webhook_cleanup._WEBHOOK_RECEIPT_CLEANUP_TICK_SECONDS)
    assert tick_count == 3
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
