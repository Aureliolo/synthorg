"""Regression: ``MCPToolInvoker.invoke`` opens a tool-execution span.

The span is named ``mcp.tool.invoke`` and carries ``mcp.tool`` plus an
``mcp.outcome`` of ``success`` / ``error`` (and ``mcp.error_type`` on the
error path). It never calls ``span.record_exception`` (per the prompt-safety
redaction rule: frame locals / exception messages can carry secrets).

Rather than wire the OTel SDK (a one-shot global install), the module-level
``_tracer`` is patched with a lightweight recorder.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

import synthorg.meta.mcp.invoker as _invoker
from synthorg.api.state import AppState
from synthorg.meta.mcp.invoker import MCPToolInvoker
from tests._shared import mock_of
from tests.unit.meta.mcp.conftest import make_tool, registry_with

pytestmark = pytest.mark.unit


class _RecordingSpan:
    """Stand-in OTel span that records every method call."""

    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.recorded_exceptions: list[BaseException] = []

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: BaseException) -> None:  # pragma: no cover
        self.recorded_exceptions.append(exc)


class _RecordingTracer:
    """Stand-in OTel tracer that captures every start_as_current_span call."""

    def __init__(self) -> None:
        self.spans: list[_RecordingSpan] = []

    @contextmanager
    def start_as_current_span(
        self,
        name: str,
        *,
        attributes: dict[str, object] | None = None,
    ) -> Iterator[_RecordingSpan]:
        span = _RecordingSpan()
        span.attributes["span.name"] = name
        if attributes:
            span.attributes.update(attributes)
        self.spans.append(span)
        yield span


async def test_invoke_success_opens_span() -> None:
    tool = make_tool()
    registry = registry_with(tool)

    async def handler(
        *,
        app_state: object,
        arguments: dict[str, object],
        actor: object = None,
    ) -> str:
        return json.dumps({"result": "ok"})

    invoker = MCPToolInvoker(registry, {"synthorg_test_get": handler})
    tracer = _RecordingTracer()
    with patch.object(_invoker, "_tracer", tracer):
        result = await invoker.invoke(
            "synthorg_test_get", {}, app_state=mock_of[AppState]()
        )

    assert result.is_error is False
    assert len(tracer.spans) == 1
    span = tracer.spans[0]
    assert span.attributes["span.name"] == "mcp.tool.invoke"
    assert span.attributes["mcp.tool"] == "synthorg_test_get"
    assert span.attributes["mcp.outcome"] == "success"
    assert span.recorded_exceptions == []


async def test_invoke_error_marks_span_without_recording_exception() -> None:
    tool = make_tool()
    registry = registry_with(tool)

    async def bad_handler(
        *,
        app_state: object,
        arguments: dict[str, object],
        actor: object = None,
    ) -> str:
        msg = "something broke"
        raise ValueError(msg)

    invoker = MCPToolInvoker(registry, {"synthorg_test_get": bad_handler})
    tracer = _RecordingTracer()
    with patch.object(_invoker, "_tracer", tracer):
        result = await invoker.invoke(
            "synthorg_test_get", {}, app_state=mock_of[AppState]()
        )

    assert result.is_error is True
    assert len(tracer.spans) == 1
    span = tracer.spans[0]
    assert span.attributes["mcp.outcome"] == "error"
    assert span.attributes["mcp.error_type"] == "ValueError"
    # Redaction rule: the span must never carry the exception object.
    assert span.recorded_exceptions == []
