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
import pkgutil
from types import ModuleType

import pytest
from pydantic import BaseModel

import synthorg.memory.tools as _memory_tools_pkg
import synthorg.ontology.injection as _ontology_injection_pkg
import synthorg.tools as _tools_pkg
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


# Package roots that contain concrete BaseTool subclasses.  We walk
# them via ``pkgutil.walk_packages`` so any new tool module added under
# these roots is auto-discovered -- a tool added without an explicit
# entry on a curated list would otherwise slip past the regression
# guard (the original failure mode this test now defends against).
_TOOL_PACKAGE_ROOTS: tuple[ModuleType, ...] = (
    _tools_pkg,
    _memory_tools_pkg,
    _ontology_injection_pkg,
)


def _import_all_modules_under(package: ModuleType) -> None:
    """Import every module under ``package`` (recursively).

    ``BaseTool.__subclasses__()`` only sees classes whose modules have
    been imported.  Walking the package tree ensures every concrete
    tool registered in any new submodule shows up.
    """
    paths = getattr(package, "__path__", None)
    if paths is None:
        return
    base_name = package.__name__
    for info in pkgutil.walk_packages(paths, prefix=f"{base_name}."):
        # Skip test packages and known test-only modules so test-only
        # ``BaseTool`` subclasses don't pollute the discovery set.
        if ".tests." in info.name or info.name.endswith(".tests"):
            continue
        importlib.import_module(info.name)


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
        for package in _TOOL_PACKAGE_ROOTS:
            _import_all_modules_under(package)

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
