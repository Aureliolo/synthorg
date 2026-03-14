"""Checkpoint callback type alias.

The callback is invoked after each completed turn with the current
``AgentContext``.  The implementation decides whether to persist
based on configuration (e.g. every N turns).
"""

from collections.abc import Callable, Coroutine
from typing import Any

from synthorg.engine.context import AgentContext

CheckpointCallback = Callable[[AgentContext], Coroutine[Any, Any, None]]
"""Async callback invoked after each turn; may skip persistence based on config."""
