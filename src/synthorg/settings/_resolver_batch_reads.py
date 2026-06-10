# module-kind: code
"""TaskGroup batch reads for the config-bridge composed getters.

Owns the parallel same-namespace settings fetch behind
``ConfigResolver._resolve_bridge_fields``: a ``TaskGroup`` fan-out over
the per-kind typed accessors plus the failed-key pinpointing that keeps
the operator log actionable when one key in a bundle fails.
"""

import asyncio
from typing import Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.observability.redaction import safe_error_description

logger = get_logger(__name__)


@runtime_checkable
class TypedSettingReads(Protocol):
    """Per-kind typed accessors the batch reader dispatches to."""

    async def get_int(self, namespace: str, key: str) -> int: ...

    async def get_float(self, namespace: str, key: str) -> float: ...

    async def get_str(self, namespace: str, key: str) -> str: ...

    async def get_json(self, namespace: str, key: str) -> object: ...


async def resolve_bridge_fields(
    reads: TypedSettingReads,
    namespace: str,
    specs: tuple[tuple[str, str], ...],
) -> dict[str, object]:
    """Resolve a bundle of same-namespace settings in parallel.

    Each spec is ``(key, kind)`` where ``kind`` is one of ``"int"``,
    ``"float"``, ``"str"``, or ``"json"``.  Returns a mapping from key
    to parsed value, suitable for passing into a Pydantic model
    constructor as keyword arguments -- which is why the value type is
    deliberately ``object``: the callers unpack the mapping into typed
    Pydantic ``__init__`` signatures that validate at runtime.

    Args:
        reads: The typed per-kind accessors (the ``ConfigResolver``).
        namespace: Setting namespace (e.g. ``"a2a"``).
        specs: Tuple of ``(key, kind)`` pairs to resolve.

    Returns:
        Dict of ``{key: parsed_value}`` for each spec.

    Raises:
        SettingNotFoundError: If a key is not in the registry.
        ValueError: If a resolved value cannot be parsed.
    """
    tasks: dict[str, asyncio.Task[object]] = {}
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = {
                key: tg.create_task(_resolve_typed(reads, namespace, key, kind))
                for key, kind in specs
            }
    except ExceptionGroup as eg:
        reraise_critical(eg)
        # Pinpoint which key(s) failed so an operator has a concrete
        # setting name in the log instead of just an ``error_count``.
        # Skip cancelled sibling tasks: ``TaskGroup`` cancels all other
        # tasks when one fails, and calling ``task.exception()`` on a
        # cancelled task would raise ``CancelledError`` -- masking the
        # original failure and polluting ``failed_keys`` with siblings
        # that didn't actually fail.
        failed_keys = [
            key
            for key, task in tasks.items()
            if task.done() and not task.cancelled() and task.exception() is not None
        ]
        first_failure = eg.exceptions[0]
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=namespace,
            key="_bridge_composed",
            error_count=len(eg.exceptions),
            failed_keys=failed_keys,
            error_type=type(first_failure).__name__,
            error=safe_error_description(first_failure),
        )
        raise first_failure from eg
    return {key: task.result() for key, task in tasks.items()}


async def _resolve_typed(
    reads: TypedSettingReads,
    namespace: str,
    key: str,
    kind: str,
) -> object:
    """Dispatch to the accessor matching ``kind``.

    Args:
        reads: The typed per-kind accessors (the ``ConfigResolver``).
        namespace: Setting namespace (e.g. ``"api"``, ``"tools"``).
        key: Setting key within the namespace.
        kind: Type discriminator; one of ``"int"``, ``"float"``,
            ``"str"``, or ``"json"``. Any other value raises
            ``ValueError`` so misuse fails loudly rather than silently
            resolving the wrong accessor.

    Returns:
        The resolved value coerced to the requested type.

    Raises:
        ValueError: If *kind* is not one of the four supported
            discriminators.
        SettingNotFoundError: If the registry does not contain *key*
            in *namespace*.
    """
    if kind == "int":
        return await reads.get_int(namespace, key)
    if kind == "float":
        return await reads.get_float(namespace, key)
    if kind == "str":
        return await reads.get_str(namespace, key)
    if kind == "json":
        return await reads.get_json(namespace, key)
    msg = f"Unsupported typed-resolve kind: {kind!r}"
    raise ValueError(msg)
