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

import pytest

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


def _all_concrete_subclasses(cls: type) -> set[type]:
    """Return every concrete (non-abstract) subclass of ``cls``."""
    found: set[type] = set()
    for sub in cls.__subclasses__():
        # Skip mixins / abstract bases (no concrete ``execute`` method).
        if getattr(sub, "__abstractmethods__", frozenset()):
            continue
        found.add(sub)
        found.update(_all_concrete_subclasses(sub))
    return found


@pytest.mark.unit
class TestEveryToolHasArgsModel:
    """Phase 4 #1611: every BaseTool subclass declares args_model."""

    def test_all_concrete_basetools_declare_args_model(self) -> None:
        """No concrete ``BaseTool`` subclass is missing ``args_model``."""
        # Force-import the modules that register every concrete tool so
        # ``BaseTool.__subclasses__`` is populated.  Imports are
        # deliberately concentrated here rather than at module scope to
        # keep the test discovery cost minimal.
        import synthorg.memory.tools.archival
        import synthorg.memory.tools.core
        import synthorg.memory.tools.knowledge_architect
        import synthorg.memory.tools.recall
        import synthorg.memory.tools.recall_search
        import synthorg.memory.tools.search
        import synthorg.ontology.injection.tool
        import synthorg.tools.analytics.data_aggregator
        import synthorg.tools.analytics.metric_collector
        import synthorg.tools.analytics.report_generator
        import synthorg.tools.approval_tool
        import synthorg.tools.code_runner
        import synthorg.tools.communication.async_task_tools
        import synthorg.tools.communication.email_sender
        import synthorg.tools.communication.notification_sender
        import synthorg.tools.communication.template_formatter
        import synthorg.tools.context.compact_context
        import synthorg.tools.database.schema_inspect
        import synthorg.tools.database.sql_query
        import synthorg.tools.design.asset_manager
        import synthorg.tools.design.diagram_generator
        import synthorg.tools.design.image_generator
        import synthorg.tools.discovery
        import synthorg.tools.examples.echo
        import synthorg.tools.file_system.delete_file
        import synthorg.tools.file_system.edit_file
        import synthorg.tools.file_system.list_directory
        import synthorg.tools.file_system.read_file
        import synthorg.tools.file_system.write_file
        import synthorg.tools.git_tools
        import synthorg.tools.terminal.shell_command
        import synthorg.tools.web.html_parser
        import synthorg.tools.web.http_request
        import synthorg.tools.web.web_search  # noqa: F401

        missing: list[str] = []
        for sub in _all_concrete_subclasses(BaseTool):
            # Skip private test fixtures and tools defined inside tests.
            if sub.__name__.startswith("_") or sub.__module__.startswith("tests."):
                continue
            if sub.__name__ in _ALLOWLIST:
                continue
            args_model = getattr(sub, "args_model", None)
            if args_model is None:
                missing.append(sub.__name__)

        assert not missing, (
            "Every concrete BaseTool subclass must declare "
            "`args_model: ClassVar[type[BaseModel] | None]` (Phase 4 of "
            f"#1611). Missing on: {sorted(missing)}.  Allowlist: "
            f"{sorted(_ALLOWLIST)}.  Add the tool to the allowlist with "
            "a docstring justification, or wire its typed args model."
        )
