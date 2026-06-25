"""Unit tests for the global tool-call signal sink."""

from collections.abc import Iterator

import pytest

from synthorg.providers.tool_call_feedback.sink import (
    ToolCallOutcome,
    emit_tool_call_outcome,
    get_tool_call_signal_sink,
    install_tool_call_signal_sink,
    uninstall_tool_call_signal_sink,
)

pytestmark = pytest.mark.unit


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, ToolCallOutcome]] = []

    async def record(
        self, *, provider: str, model: str, outcome: ToolCallOutcome
    ) -> None:
        self.calls.append((provider, model, outcome))


@pytest.fixture(autouse=True)
def _clean_sink() -> Iterator[None]:
    """Ensure no installed sink leaks across tests."""
    uninstall_tool_call_signal_sink()
    yield
    uninstall_tool_call_signal_sink()


async def test_emit_is_noop_when_uninstalled() -> None:
    assert get_tool_call_signal_sink() is None
    # Must not raise with no sink installed.
    await emit_tool_call_outcome(
        provider="p", model="m", outcome=ToolCallOutcome.FAILURE
    )


async def test_emit_routes_to_installed_sink() -> None:
    sink = _RecordingSink()
    install_tool_call_signal_sink(sink)
    assert get_tool_call_signal_sink() is sink
    await emit_tool_call_outcome(
        provider="example-provider",
        model="example-large-001",
        outcome=ToolCallOutcome.SUCCESS,
    )
    assert sink.calls == [
        ("example-provider", "example-large-001", ToolCallOutcome.SUCCESS)
    ]


async def test_uninstall_clears_sink() -> None:
    install_tool_call_signal_sink(_RecordingSink())
    uninstall_tool_call_signal_sink()
    assert get_tool_call_signal_sink() is None
