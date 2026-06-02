"""Tool-call accumulation helpers for the LiteLLM driver.

Handles streaming ``ToolCall`` delta merging, argument-length
truncation, and final ``ToolCall`` construction.  Isolating this
subsystem keeps ``litellm_driver.py`` focused on the ``acompletion``
dispatch path.
"""

import json

from pydantic import JsonValue

from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
    PROVIDER_TOOL_CALL_ARGUMENTS_TRUNCATED,
    PROVIDER_TOOL_CALL_INCOMPLETE,
)
from synthorg.providers.enums import StreamEventType
from synthorg.providers.models import StreamChunk, ToolCall

logger = get_logger(__name__)


class _ToolCallAccumulator:
    """Accumulates streaming tool call deltas into a ``ToolCall``."""

    _MAX_ARGUMENTS_LEN: int = 1_048_576

    id: str
    name: str
    arguments: str
    _truncated: bool

    def __init__(self) -> None:
        self.id = ""
        self.name = ""
        self.arguments = ""
        self._truncated = False

    def update(self, delta: object) -> None:
        """Merge a single tool call delta."""
        call_id = getattr(delta, "id", None)
        if call_id:
            self.id = str(call_id)
        func = getattr(delta, "function", None)
        if func is not None:
            name = getattr(func, "name", None)
            if name:
                self.name = str(name)
            args = getattr(func, "arguments", None)
            if args:
                if self._truncated:
                    return
                fragment = str(args)
                if len(self.arguments) + len(fragment) > self._MAX_ARGUMENTS_LEN:
                    logger.warning(
                        PROVIDER_TOOL_CALL_ARGUMENTS_TRUNCATED,
                        max_bytes=self._MAX_ARGUMENTS_LEN,
                    )
                    self._truncated = True
                    return
                self.arguments += fragment

    def build(self) -> ToolCall | None:
        """Build a ``ToolCall`` if enough data accumulated.

        Returns ``None`` if either ``id`` or ``name`` is still empty
        (malformed/incomplete streaming deltas), if the argument JSON could
        not be parsed, if it does not parse to a JSON object, or if the
        arguments contain non-finite floats that would not round-trip
        through ``ToolCall``'s ``allow_inf_nan=False`` constraint.

        Returns:
            A fully assembled ``ToolCall`` when ``id`` and ``name`` are both
            set and the arguments are a finite, JSON-serialisable object, or
            ``None`` otherwise.
        """
        if not self.id or not self.name:
            if self.arguments:
                logger.warning(
                    PROVIDER_TOOL_CALL_INCOMPLETE,
                    tool_id=self.id,
                    tool_name=self.name,
                    args_len=len(self.arguments),
                )
            return None
        try:
            parsed = json.loads(self.arguments) if self.arguments else {}
        except ValueError:
            logger.warning(
                PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
                tool_name=self.name,
                tool_id=self.id,
                args_length=len(self.arguments) if self.arguments else 0,
            )
            return None
        if not isinstance(parsed, dict):
            logger.warning(
                PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
                tool_name=self.name,
                tool_id=self.id,
                parsed_type=type(parsed).__name__,
                reason="non_object_json_value",
            )
            return None
        args: dict[str, JsonValue] = parsed
        # ``ToolCall.arguments`` forbids non-finite floats (allow_inf_nan=False);
        # ``json.loads`` accepts the ``NaN`` / ``Infinity`` literals, so drop the
        # tool call when its arguments will not round-trip rather than raising a
        # ValidationError that would abort the whole stream.
        try:
            json.dumps(args, allow_nan=False)
        except ValueError, TypeError:
            logger.warning(
                PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
                tool_name=self.name,
                tool_id=self.id,
                reason="non_finite_or_unserialisable",
            )
            return None
        return ToolCall(id=self.id, name=self.name, arguments=args)


def accumulate_tool_call_deltas(
    raw_deltas: list[object],
    pending: dict[int, _ToolCallAccumulator],
) -> None:
    """Merge streaming tool call deltas into accumulators."""
    for tc_delta in raw_deltas:
        idx: int = getattr(tc_delta, "index", 0)
        if idx not in pending:
            pending[idx] = _ToolCallAccumulator()
        pending[idx].update(tc_delta)


def emit_pending_tool_calls(
    pending: dict[int, _ToolCallAccumulator],
) -> list[StreamChunk]:
    """Build ``TOOL_CALL_DELTA`` chunks from accumulated data.

    Although the event type is ``TOOL_CALL_DELTA``, each chunk contains
    a fully assembled ``ToolCall`` (not a partial delta).  The stream
    protocol reuses the delta event type for final tool call delivery.

    Returns:
        A list of ``TOOL_CALL_DELTA`` ``StreamChunk`` objects (one per
        successfully built ``ToolCall``), ordered by accumulator index.
    """
    result: list[StreamChunk] = []
    for idx in sorted(pending):
        tc = pending[idx].build()
        if tc is not None:
            result.append(
                StreamChunk(
                    event_type=StreamEventType.TOOL_CALL_DELTA,
                    tool_call_delta=tc,
                )
            )
    return result
