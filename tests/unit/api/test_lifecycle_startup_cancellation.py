"""Every abort path in ``_run_startup`` survives a mid-boot shutdown.

Each auto-wire section awaits, so a shutdown arriving while one is in
flight raises ``CancelledError`` inside its handler. That derives from
``BaseException``, so a handler written ``except Exception`` misses it and
the boot unwinds having started services nobody stops: the settings
dispatcher, the task engine, the message bus, the persistence connection.

Two layers, because neither alone is enough. The runtime test proves a
real cancellation reaches the teardown, but only for the one section whose
awaited dependency it injects into; reaching the other three needs a
different failure at a different dependency each. The AST test covers all
of them at once, and sections added later, by reading what each handler
declares and where it hands off.
"""

import ast
import asyncio
import inspect
from typing import Any
from unittest import mock

import pytest

from synthorg.api import lifecycle_runner_startup
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle import _safe_shutdown
from synthorg.api.lifecycle_builder import _build_lifecycle
from synthorg.config.schema import RootConfig
from tests._shared import make_app_state
from tests.unit.api.conftest import FakePersistenceBackend

pytestmark = pytest.mark.unit

_STARTUP_FUNC = "_run_startup"
_ABORT_HELPER = "_abort_wired"

#: One per auto-wire section, taken from the ``detail=`` each handler
#: passes. Named rather than counted: a count survives a real abort path
#: being deleted and an unrelated handler appearing, which is exactly the
#: regression this guards.
_EXPECTED_ABORT_DETAILS = frozenset(
    {
        "settings_auto_wire_failed",
        "workflow_observer_auto_wire_failed",
        "approval_gate_auto_wire_failed",
        "memory_backend_auto_wire_failed",
    }
)


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


def _own_body_nodes(node: ast.AST) -> list[ast.AST]:
    """Every node under *node* that is not inside a nested definition.

    A call written inside a closure defined in the handler runs when that
    closure is called, which may be never. Counting one as teardown would
    let a handler satisfy the assertion below without ever tearing
    anything down.

    Returns:
        The nodes, unordered.
    """
    found: list[ast.AST] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(
            child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef
        ):
            continue
        found.append(child)
        found.extend(_own_body_nodes(child))
    return found


def _abort_details(handler: ast.ExceptHandler) -> set[str]:
    """The ``detail=`` values this handler hands the abort helper.

    Returns:
        One entry per call, empty when the handler does not abort.
    """
    details: set[str] = set()
    for node in _own_body_nodes(handler):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == _ABORT_HELPER
        ):
            continue
        details.update(
            keyword.value.value
            for keyword in node.keywords
            if keyword.arg == "detail"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        )
    return details


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
        for node in _own_body_nodes(_startup_tree())
        if isinstance(node, ast.ExceptHandler) and _abort_details(node)
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

    def test_every_auto_wire_section_still_aborts(self) -> None:
        """Names the sections, so deleting one cannot be masked by adding another."""
        found = {detail for h in _abort_handlers() for detail in _abort_details(h)}
        assert found == _EXPECTED_ABORT_DETAILS


class TestCancellationReachesTeardown:
    """A real cancellation, not a shape: the memory wire is the injection point.

    It is the last of the four sections, so by the time it runs every
    service the teardown is responsible for is actually started, and a
    handler that let the cancellation past would leak all of them.
    """

    async def test_a_cancelled_memory_wire_tears_down_and_propagates(self) -> None:
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app_state = make_app_state(
            config=RootConfig(company_name="startup-cancellation"),
            approval_store=ApprovalStore(),
            persistence=persistence,
        )
        startup, _shutdown = _build_lifecycle(
            persistence=persistence,
            message_bus=None,
            bridge=None,
            settings_dispatcher=None,
            task_engine=None,
            meeting_scheduler=None,
            backup_service=None,
            approval_timeout_scheduler=None,
            app_state=app_state,
        )

        async def _cancelled(*_args: Any, **_kwargs: Any) -> None:  # type: ignore[explicit-any]  # patched seam takes the real signature
            raise asyncio.CancelledError

        with (
            mock.patch(
                "synthorg.api.lifecycle_helpers.memory_backend_wiring"
                ".wire_memory_backend",
                _cancelled,
            ),
            mock.patch(
                "synthorg.api.lifecycle_runner_startup._safe_shutdown",
                mock.AsyncMock(spec=_safe_shutdown),
            ) as safe_shutdown,
            pytest.raises(asyncio.CancelledError),
        ):
            await startup[0]()

        assert safe_shutdown.await_count == 1, (
            "a shutdown arriving while the memory wire awaited its embedder "
            "probe escaped the handler without tearing down the services "
            "already started above it"
        )
        await persistence.disconnect()
