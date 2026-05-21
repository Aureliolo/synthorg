"""Regression test for the pytest-xdist loadscope crash guard.

The root conftest installs a guard on
``LoadScopeScheduling._reschedule`` so a worker that dies during
collection (Windows + Python 3.14 intermittently does this) no longer
aborts the whole run with ``KeyError: <WorkerController>``. These tests
pin that guard so a future xdist bump or conftest edit cannot silently
drop it.
"""

import pytest

pytestmark = pytest.mark.unit


def test_guard_is_installed() -> None:
    """The conftest guard is applied to the live scheduler class."""
    pytest.importorskip("xdist")
    from xdist.scheduler.loadscope import LoadScopeScheduling

    assert getattr(LoadScopeScheduling._reschedule, "_synthorg_crash_guarded", False), (
        "conftest xdist crash guard is not installed on LoadScopeScheduling"
    )


def test_reschedule_skips_node_without_registered_collection() -> None:
    """A node absent from ``registered_collections`` is skipped, not KeyError'd.

    This is the exact crash path: a worker that vanished during
    collection is still iterated by ``schedule()``; without the guard
    ``_assign_work_unit`` indexes ``registered_collections[node]`` and
    raises, aborting the run.
    """
    pytest.importorskip("xdist")
    from xdist.scheduler.loadscope import LoadScopeScheduling

    sched = LoadScopeScheduling.__new__(LoadScopeScheduling)
    sched.registered_collections = {}
    dead_node = object()

    # Must return cleanly (the guard short-circuits) rather than raising
    # KeyError on the missing node.
    assert LoadScopeScheduling._reschedule(sched, dead_node) is None
