"""ArchitectureMutator backed by a small ``target``-routing dispatcher.

The rollback executor passes a target string of the form
``"<type>:<id>"`` (or ``"<type>:<id>:<sub_id>"``). The router parses
the prefix and dispatches to a per-type adapter callable. Each adapter
takes ``(target_id, previous_value)`` and applies the restore through
its underlying store.

This is a deliberately thin abstraction so operators can extend the
router with new ``<type>`` prefixes via :meth:`register_handler`
without touching the rollback executor itself.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from synthorg.meta.errors import (
    RollbackMutationDeniedError,
    UnknownArchitectureTargetError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_ROLLBACK_OPERATION_FAILED

logger = get_logger(__name__)


# An adapter takes the parsed target tail (after the ``<type>:`` prefix)
# and the previous value to restore.
ArchitectureAdapter = Callable[[str, Any], Awaitable[None]]


class RoutedArchitectureMutator:
    """Dispatches architecture-restore operations by target prefix.

    Adapters are registered per target-type prefix. Unknown prefixes
    raise :class:`UnknownArchitectureTargetError` so the rollback
    executor's audit log records the failure rather than silently
    skipping it.
    """

    def __init__(
        self,
        adapters: dict[str, ArchitectureAdapter] | None = None,
    ) -> None:
        self._adapters: dict[str, ArchitectureAdapter] = dict(adapters or {})

    def register_handler(
        self,
        target_type: str,
        adapter: ArchitectureAdapter,
    ) -> None:
        """Register or replace the adapter for ``target_type``."""
        self._adapters[target_type] = adapter

    async def restore(self, *, target: str, previous_value: Any) -> None:
        """Parse ``target`` and dispatch to the registered adapter."""
        target_type, sep, target_tail = target.partition(":")
        if not sep or not target_type or not target_tail:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_architecture",
                target=target,
                reason="invalid_target_format",
            )
            msg = f"revert_architecture target must be '<type>:<id>', got {target!r}"
            raise UnknownArchitectureTargetError(msg)
        adapter = self._adapters.get(target_type)
        if adapter is None:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_architecture",
                target=target,
                reason="unknown_target_type",
                target_type=target_type,
            )
            msg = (
                f"revert_architecture: no adapter registered for "
                f"target_type {target_type!r}"
            )
            raise UnknownArchitectureTargetError(msg)
        try:
            await adapter(target_tail, previous_value)
        except MemoryError, RecursionError:
            raise
        except UnknownArchitectureTargetError:
            raise
        except RollbackMutationDeniedError:
            raise
        except Exception as exc:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_architecture",
                target=target,
                reason="adapter_failed",
                target_type=target_type,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                f"revert_architecture rejected: adapter for "
                f"{target_type!r} failed on {target!r}"
            )
            raise RollbackMutationDeniedError(msg) from exc
