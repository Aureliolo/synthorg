"""Regression guard: tool tests run under ``ProactorEventLoop`` on Windows.

Mirror of ``tests/unit/scripts/test_run_affected_tests.py::
test_unit_tier_uses_selector_event_loop_on_windows`` for the
Proactor-shadowing side. The unit-tier root conftest pins
``SelectorEventLoop`` via the ``pytest_asyncio_loop_factories`` hook;
``tests/unit/tools/conftest.py`` shadows that with ``ProactorEventLoop``
because the tool tests drive ``asyncio.create_subprocess_exec``, which
the Selector loop on Windows cannot dispatch (no IOCP integration).
A regression that breaks pluggy's reverse-order ``firstresult=True``
invocation, or a typo in the deeper conftest's hook, would silently
fall back to the unit-tier Selector loop and hang every subprocess
call until the per-test 30s timeout fires; this test catches that.
"""

import asyncio
import sys

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific guard")
async def test_tool_tier_uses_proactor_event_loop_on_windows() -> None:
    loop_class_name = type(asyncio.get_running_loop()).__name__
    assert "Proactor" in loop_class_name, (
        f"tool tier ran under {loop_class_name}; "
        f"expected ProactorEventLoop (deeper-conftest hook shadow broken?)"
    )
