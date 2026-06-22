"""Regression: ``record_task_run`` is wired for every terminal transition.

The Prometheus task-run counter must fire on every bounded terminal
transition. The call site lives in ``task_engine_apply.py`` for the
four outcomes (``succeeded`` / ``failed`` / ``cancelled`` /
``rejected``). Rather than build a full TaskEngine stack, this test
pins:

1. The ``RECORDED_STATUS_OUTCOME`` map covers exactly the four
   bounded outcomes (extends the project's acceptance label set).
2. The map is an immutable ``MappingProxyType`` so a typo cannot
   slip a fifth outcome in without going through the gate.
3. ``record_task_run`` appears in the module's source so a future
   refactor that drops the call surfaces in code review.
"""

import ast
from pathlib import Path
from types import MappingProxyType

import pytest

from synthorg.core.task_enums import TaskStatus
from synthorg.engine.task_engine_apply_helpers import RECORDED_STATUS_OUTCOME

pytestmark = pytest.mark.unit

_APPLY_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "src"
    / "synthorg"
    / "engine"
    / "task_engine_apply.py"
)


class TestRecordedStatusOutcomeMap:
    def test_map_covers_four_bounded_outcomes(self) -> None:
        assert dict(RECORDED_STATUS_OUTCOME) == {
            TaskStatus.COMPLETED: "succeeded",
            TaskStatus.FAILED: "failed",
            TaskStatus.CANCELLED: "cancelled",
            TaskStatus.REJECTED: "rejected",
        }

    def test_map_is_immutable(self) -> None:
        assert isinstance(RECORDED_STATUS_OUTCOME, MappingProxyType)


class TestRecordTaskRunWiredAtCallSite:
    def test_call_sites_present_in_module_source(self) -> None:
        """The module MUST invoke ``record_task_run`` for the recorded outcomes.

        Parses the module AST and counts real ``Call`` nodes whose
        callee is ``record_task_run`` (either bare name or attribute
        access). A raw substring check would happily match a docstring
        or a comment mentioning the function and miss a silent drop
        of the real call site.
        """
        assert _APPLY_MODULE_PATH.exists(), (
            f"expected task_engine_apply.py at {_APPLY_MODULE_PATH}"
        )
        source = _APPLY_MODULE_PATH.read_text(encoding="utf-8")
        module = ast.parse(source)
        invocations = sum(
            1
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "record_task_run")
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "record_task_run"
                )
            )
        )
        # The transition branch (``if mutation.target_status in
        # RECORDED_STATUS_OUTCOME``) and the cancel branch are the two
        # invocation sites today. Pin the count to catch a silent drop.
        assert invocations >= 2, (
            "expected at least two record_task_run(...) invocations "
            f"(transition + cancel), found {invocations}"
        )
