"""Checkpoint callback type alias.

The callback is invoked after each completed turn with the current
``AgentContext``.  The implementation decides whether to persist
based on configuration (e.g. every N turns).
"""

from collections.abc import Awaitable, Callable

from synthorg.engine.context import AgentContext

CheckpointCallback = Callable[[AgentContext], Awaitable[None]]
"""Async callback invoked after each turn; may skip persistence based on config."""
