"""Every abort path in ``_run_startup`` survives a mid-boot shutdown.

Each auto-wire section awaits, so a shutdown arriving while one is in
flight raises ``CancelledError`` inside its handler. That derives from
``BaseException``, so a handler written ``except Exception`` misses it and
the boot unwinds having started services nobody stops: the settings
dispatcher, the task engine, the message bus, the persistence connection.

Asserted from the AST rather than by driving a boot: the defect is a
handler's declared exception tuple, and each of the four sections needs a
different failure injected at a different awaited dependency to reach its
handler at all. Reading the shape directly covers every section, including
ones added later.
"""

import ast
import inspect

import pytest

from synthorg.api import lifecycle_runner_startup

pytestmark = pytest.mark.unit

_STARTUP_FUNC = "_run_startup"
_ABORT_HELPER = "_abort_wired"


def _startup_tree() -> ast.AsyncFunctionDef:
    """The parsed ``_run_startup`` definition.

    Returns:
        Its AST node.
    """
    module = ast.parse(inspect.getsource(lifecycle_runner_startup))
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == _STARTUP_FUNC:
            return node
    msg = f"{_STARTUP_FUNC} not found in lifecycle_runner_startup"
    raise AssertionError(msg)


def _calls_abort(handler: ast.ExceptHandler) -> bool:
    """Whether *handler* tears down through the abort helper.

    Returns:
        ``True`` when the helper is called anywhere in the handler body.
    """
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == _ABORT_HELPER
        for node in ast.walk(handler)
    )


def _caught_names(handler: ast.ExceptHandler) -> set[str]:
    """Dotted names the handler catches.

    Returns:
        e.g. ``{"Exception", "asyncio.CancelledError"}``.
    """
    caught = handler.type
    if caught is None:
        return set()
    parts = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return {ast.unparse(part) for part in parts}


def _abort_handlers() -> list[ast.ExceptHandler]:
    """Every handler in ``_run_startup`` that aborts the boot.

    Returns:
        The handlers, in source order.
    """
    return [
        node
        for node in ast.walk(_startup_tree())
        if isinstance(node, ast.ExceptHandler) and _calls_abort(node)
    ]


class TestStartupAbortHandlers:
    def test_every_abort_handler_catches_cancellation(self) -> None:
        missing = [
            handler.lineno
            for handler in _abort_handlers()
            if "asyncio.CancelledError" not in _caught_names(handler)
        ]
        assert not missing, (
            f"{_STARTUP_FUNC} handlers at lines {missing} call {_ABORT_HELPER} "
            f"but do not catch asyncio.CancelledError, so a shutdown arriving "
            f"mid-boot escapes past them and skips the teardown entirely"
        )

    def test_the_abort_paths_are_still_wired(self) -> None:
        """Guards the assertion above against silently covering nothing.

        A refactor that renames the helper, or moves the sections out of
        ``_run_startup``, would leave the handler list empty and the real
        assertion vacuously green.
        """
        assert len(_abort_handlers()) >= 4
