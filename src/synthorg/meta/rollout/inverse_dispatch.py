"""Inverse-action dispatch for rollback plans.

Each ``RollbackOperation`` carries an ``operation_type`` discriminator.
The rollback executor looks up the matching ``RollbackHandler`` and
delegates. Handlers are thin adapters over mutator protocols so that
the rollout subsystem stays decoupled from the concrete config,
prompt, architecture, and code services.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_ROLLBACK_ARCHITECTURE_REVERTED,
    META_ROLLBACK_CODE_REVERTED,
    META_ROLLBACK_CONFIG_REVERTED,
    META_ROLLBACK_OPERATION_FAILED,
    META_ROLLBACK_PROMPT_REVERTED,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.meta.models import RollbackOperation

logger = get_logger(__name__)


class UnknownRollbackOperationError(
    ValueError,
):  # lint-allow: domain-error-hierarchy -- internal dispatch precondition
    """Raised when a ``RollbackOperation`` has no registered handler."""


@runtime_checkable
class RollbackHandler(Protocol):
    """Applies the inverse action for a single ``RollbackOperation``."""

    async def revert(self, operation: RollbackOperation) -> int:
        """Apply the inverse action.

        Returns:
            Number of underlying changes applied (1 per operation
            unless the handler batches).
        """
        ...


# -- Mutator protocols ----------------------------------------------------


@runtime_checkable
class ConfigMutator(Protocol):
    """Writes a value at a dotted config path."""

    async def set(self, *, path: str, value: object) -> None:
        """Restore the config leaf at ``path`` to ``value``."""
        ...


@runtime_checkable
class PromptMutator(Protocol):
    """Restores an org-wide prompt principle."""

    async def restore_principle(self, *, scope: str, text: str) -> None:
        """Install ``text`` as the principle for ``scope``."""
        ...


@runtime_checkable
class ArchitectureMutator(Protocol):
    """Restores an org-structure entity (role, department, workflow)."""

    async def restore(self, *, target: str, previous_value: object) -> None:
        """Restore entity ``target`` to ``previous_value``."""
        ...


@runtime_checkable
class CodeMutator(Protocol):
    """Reverts a source file to previous contents."""

    async def revert_file(self, *, path: str, content: str) -> None:
        """Write ``content`` to ``path``."""
        ...


# -- Concrete handlers ----------------------------------------------------


class RevertConfigHandler:
    """Rollback handler for ``revert_config`` operations."""

    def __init__(self, *, mutator: ConfigMutator) -> None:
        self._mutator = mutator

    async def revert(self, operation: RollbackOperation) -> int:
        """Restore config path ``operation.target`` to ``previous_value``.

        The mutator call is wrapped so a failing underlying service
        (validation, write, remote I/O) is surfaced with the operation
        target + error class before re-raising. ``MemoryError`` and
        ``RecursionError`` propagate unchanged so catastrophic system
        errors are never swallowed by the rollback executor's generic
        ``except Exception`` branch.

        Returns:
            Resulting integer.

        Raises:
            Exception: Raised on the corresponding failure path.
        """
        try:
            await self._mutator.set(
                path=str(operation.target),
                value=operation.previous_value,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                META_ROLLBACK_OPERATION_FAILED,
                exc,
                operation_type="revert_config",
                target=str(operation.target),
            )
            raise
        logger.info(
            META_ROLLBACK_CONFIG_REVERTED,
            target=str(operation.target),
        )
        return 1


class RestorePromptHandler:
    """Rollback handler for ``restore_prompt`` operations."""

    def __init__(self, *, mutator: PromptMutator) -> None:
        self._mutator = mutator

    async def revert(self, operation: RollbackOperation) -> int:
        """Reinstall the previous prompt principle.

        Returns:
            Resulting integer.

        Raises:
            ValueError: Raised on the corresponding failure path.
            Exception: Raised on the corresponding failure path.
        """
        text = operation.previous_value
        if not isinstance(text, str):
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="restore_prompt",
                target=str(operation.target),
                reason="non_string_previous_value",
                got_type=type(text).__name__,
            )
            msg = (
                f"restore_prompt requires a string previous_value; "
                f"got {type(text).__name__}"
            )
            raise ValueError(msg)  # noqa: TRY004 -- semantic validation, not a type error
        try:
            await self._mutator.restore_principle(
                scope=str(operation.target),
                text=text,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                META_ROLLBACK_OPERATION_FAILED,
                exc,
                operation_type="restore_prompt",
                target=str(operation.target),
            )
            raise
        logger.info(
            META_ROLLBACK_PROMPT_REVERTED,
            target=str(operation.target),
        )
        return 1


class RevertArchitectureHandler:
    """Rollback handler for ``revert_architecture`` operations."""

    def __init__(self, *, mutator: ArchitectureMutator) -> None:
        self._mutator = mutator

    async def revert(self, operation: RollbackOperation) -> int:
        """Restore the structural entity to its previous value.

        Returns:
            Resulting integer.

        Raises:
            Exception: Raised on the corresponding failure path.
        """
        try:
            await self._mutator.restore(
                target=str(operation.target),
                previous_value=operation.previous_value,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                META_ROLLBACK_OPERATION_FAILED,
                exc,
                operation_type="revert_architecture",
                target=str(operation.target),
            )
            raise
        logger.info(
            META_ROLLBACK_ARCHITECTURE_REVERTED,
            target=str(operation.target),
        )
        return 1


class RevertCodeHandler:
    """Rollback handler for ``revert_code`` operations."""

    def __init__(self, *, mutator: CodeMutator) -> None:
        self._mutator = mutator

    async def revert(self, operation: RollbackOperation) -> int:
        """Write ``previous_value`` back to the file at ``target``.

        Returns:
            Resulting integer.

        Raises:
            ValueError: Raised on the corresponding failure path.
            Exception: Raised on the corresponding failure path.
        """
        content = operation.previous_value
        if not isinstance(content, str):
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_code",
                target=str(operation.target),
                reason="non_string_previous_value",
                got_type=type(content).__name__,
            )
            msg = (
                f"revert_code requires a string previous_value (file "
                f"contents); got {type(content).__name__}"
            )
            raise ValueError(msg)  # noqa: TRY004 -- semantic validation, not a type error
        try:
            await self._mutator.revert_file(
                path=str(operation.target),
                content=content,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                META_ROLLBACK_OPERATION_FAILED,
                exc,
                operation_type="revert_code",
                target=str(operation.target),
            )
            raise
        logger.info(
            META_ROLLBACK_CODE_REVERTED,
            target=str(operation.target),
        )
        return 1


# -- Factory --------------------------------------------------------------


def default_rollback_handlers(
    *,
    config: ConfigMutator,
    prompt: PromptMutator,
    architecture: ArchitectureMutator,
    code: CodeMutator,
) -> Mapping[NotBlankStr, RollbackHandler]:
    """Build the default handler mapping keyed by ``operation_type``.

    Returns:
        ``Mapping[NotBlankStr, RollbackHandler]`` instance.
    """
    return MappingProxyType(
        {
            NotBlankStr("revert_config"): RevertConfigHandler(mutator=config),
            NotBlankStr("restore_prompt"): RestorePromptHandler(mutator=prompt),
            NotBlankStr("revert_architecture"): RevertArchitectureHandler(
                mutator=architecture,
            ),
            NotBlankStr("revert_code"): RevertCodeHandler(mutator=code),
        }
    )
