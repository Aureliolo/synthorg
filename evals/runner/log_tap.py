# module-kind: code
"""Collecting one run's log events without disturbing anyone else's.

A brief's score depends on events the engine logs while it runs: the penalty
table counts process facts, and the prompt-class tally counts one
``PROVIDER_PROMPT_PURPOSE_INVOKED`` per LLM call. Reading them back off the log
stream is the only place they exist as a stream.

``structlog.testing.capture_logs`` is the obvious way to do that and is wrong
here, because it swaps the *global* processor chain for the duration: it
collects every event the whole process emits, and drops them all rather than
letting them reach their sinks. That was harmless while a brief was the only
thing running. It stopped being harmless when the A/B recorder started serving
its own gateway in the same process: the gateway dispatch a native loop's own
driver just made opens its own ``cost_recording_scope`` and logs the same
prompt-purpose event server-side, so one LLM call would be counted twice, and
every unrelated warning the server emitted for the length of a brief (minutes,
on the container leg) would vanish.

So the tap is a processor rather than a chain replacement. It appends to a sink
held in a :class:`~contextvars.ContextVar`, which only the task that opened the
scope and its children can see, and it always returns the event unchanged so
every sink still receives it. Work started elsewhere -- a server's
request-handling task -- was created outside the scope and carries no sink, so
it is neither collected nor suppressed.

The processor sits at the head of the chain, ahead of ``filter_by_level``,
because the prompt-purpose event is logged at DEBUG and a run has to count it
whatever level the operator configured their sinks at.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import structlog
from structlog.typing import EventDict, WrappedLogger

_SINK: ContextVar[list[EventDict] | None] = ContextVar(
    "evals_log_tap_sink", default=None
)


def _tap(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Copy the event into the calling task's sink, if it opened one.

    Args:
        logger: The wrapped logger (unused; structlog processor signature).
        method_name: The log method name (unused; processor signature).
        event_dict: The event being processed.

    Returns:
        The event, unchanged, so the rest of the chain still runs.
    """
    del logger, method_name
    sink = _SINK.get()
    if sink is not None:
        # Copied because the chain mutates the mapping as it runs, and a
        # reader of these entries wants what the call site passed.
        sink.append(dict(event_dict))
    return event_dict


def _install() -> None:
    """Put the tap at the head of the configured processor chain.

    Idempotent, and re-checked on every scope because the observability setup
    reconfigures structlog when sinks are rebuilt, which would drop the tap.
    """
    config = structlog.get_config()
    processors = list(config.get("processors", []))
    if processors and processors[0] is _tap:
        return
    structlog.configure(processors=[_tap, *processors])


@contextmanager
def capture_run_logs() -> Iterator[list[EventDict]]:
    """Collect the log events emitted by the calling task while open.

    Yields:
        The list events are appended to. It fills as the run proceeds, so read
        it after the scope closes.
    """
    _install()
    entries: list[EventDict] = []
    token = _SINK.set(entries)
    try:
        yield entries
    finally:
        _SINK.reset(token)


__all__ = ["capture_run_logs"]
