"""Coverage check: every concrete BaseTool subclass declares ``args_model``.

Phase 4 of #1611 wires every domain tool to a typed Pydantic args
model.  This test walks the ``BaseTool`` subclass tree and asserts
every concrete subclass either:

* Declares ``args_model: ClassVar[type[BaseModel] | None]`` set to a
  concrete :class:`~pydantic.BaseModel` subclass (typed-args migrated),
  OR
* Is one of a small explicit allowlist of tools that intentionally
  defer typed-args declaration (third-party / dynamically-shaped
  tools whose schema is not known until runtime).

A new tool merged without ``args_model`` and not on the allowlist
fails this test, surfacing the regression at PR review time.
"""

import importlib
import inspect

import pytest
from pydantic import BaseModel

from synthorg.tools.base import BaseTool

# Tools that legitimately do NOT declare ``args_model`` because their
# parameter schema is dynamic at construction (set from a remote MCP
# server's tools/list response, etc.).  Adding to this set requires a
# justification in the docstring/code.
_ALLOWLIST: frozenset[str] = frozenset(
    {
        "MCPBridgeTool",  # parameters_schema mirrors a remote MCP tool
    }
)


# Modules that register concrete BaseTool subclasses.  Imported in the
# test so ``BaseTool.__subclasses__`` is fully populated.  Listed
# here as data (instead of an import block in the test body) so the
# test function stays under the 50-line limit.
_CONCRETE_TOOL_MODULES: tuple[str, ...] = (
    "synthorg.memory.tools.archival",
    "synthorg.memory.tools.core",
    "synthorg.memory.tools.knowledge_architect",
    "synthorg.memory.tools.recall",
    "synthorg.memory.tools.recall_search",
    "synthorg.memory.tools.search",
    "synthorg.ontology.injection.tool",
    "synthorg.tools.analytics.data_aggregator",
    "synthorg.tools.analytics.metric_collector",
    "synthorg.tools.analytics.report_generator",
    "synthorg.tools.approval_tool",
    "synthorg.tools.code_runner",
    "synthorg.tools.communication.async_task_tools",
    "synthorg.tools.communication.email_sender",
    "synthorg.tools.communication.notification_sender",
    "synthorg.tools.communication.template_formatter",
    "synthorg.tools.context.compact_context",
    "synthorg.tools.database.schema_inspect",
    "synthorg.tools.database.sql_query",
    "synthorg.tools.design.asset_manager",
    "synthorg.tools.design.diagram_generator",
    "synthorg.tools.design.image_generator",
    "synthorg.tools.discovery",
    "synthorg.tools.examples.echo",
    "synthorg.tools.file_system.delete_file",
    "synthorg.tools.file_system.edit_file",
    "synthorg.tools.file_system.list_directory",
    "synthorg.tools.file_system.read_file",
    "synthorg.tools.file_system.write_file",
    "synthorg.tools.git_tools",
    "synthorg.tools.terminal.shell_command",
    "synthorg.tools.web.html_parser",
    "synthorg.tools.web.http_request",
    "synthorg.tools.web.web_search",
)


def _all_concrete_subclasses(cls: type) -> set[type]:
    """Return every concrete (non-abstract) subclass of ``cls``.

    Recurses through abstract intermediates so concrete tools that
    inherit via a mixin / abstract base are discovered.  Abstract
    bases themselves are excluded from the result set.
    """
    found: set[type] = set()
    for sub in cls.__subclasses__():
        # Always recurse so concrete subclasses below an abstract
        # intermediate are discovered.
        found.update(_all_concrete_subclasses(sub))
        # Skip mixins / abstract bases (no concrete ``execute``).
        if getattr(sub, "__abstractmethods__", frozenset()):
            continue
        found.add(sub)
    return found


def _is_valid_args_model(value: object) -> bool:
    """Return True iff ``value`` is a *concrete* ``BaseModel`` subclass.

    The contract is "every BaseTool declares an args model"; an abstract
    intermediate (one with unimplemented ``@abstractmethod`` members)
    can technically inherit from ``BaseModel`` while leaving the actual
    args shape unspecified.  Reject those so the regression guard does
    not silently accept a partial contract.
    """
    return (
        isinstance(value, type)
        and issubclass(value, BaseModel)
        and value is not BaseModel
        and not inspect.isabstract(value)
    )


@pytest.mark.unit
class TestEveryToolHasArgsModel:
    """Phase 4 #1611: every BaseTool subclass declares args_model."""

    def test_all_concrete_basetools_declare_args_model(self) -> None:
        """No concrete ``BaseTool`` subclass is missing ``args_model``."""
        for module_name in _CONCRETE_TOOL_MODULES:
            importlib.import_module(module_name)

        missing: list[str] = []
        for sub in _all_concrete_subclasses(BaseTool):
            # Skip private test fixtures and tools defined inside tests.
            if sub.__name__.startswith("_") or sub.__module__.startswith("tests."):
                continue
            if sub.__name__ in _ALLOWLIST:
                continue
            if not _is_valid_args_model(getattr(sub, "args_model", None)):
                missing.append(sub.__name__)

        assert not missing, (
            "Every concrete BaseTool subclass must declare "
            "`args_model: ClassVar[type[BaseModel] | None]` set to a "
            f"BaseModel subclass (Phase 4 of #1611). Missing on: "
            f"{sorted(missing)}.  Allowlist: {sorted(_ALLOWLIST)}.  "
            "Add the tool to the allowlist with a docstring "
            "justification, or wire its typed args model."
        )
