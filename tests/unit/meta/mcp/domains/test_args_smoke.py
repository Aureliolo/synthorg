"""Parametrized smoke tests for the bulk MCP args modules.

The per-tool args tests in ``test_simple_args.py`` /
``test_tasks_args.py`` / ``test_agents_args.py`` cover representative
shapes; this file fills the gap for ``_remaining_args.py`` (~104
models) and ``_workflows_org_args.py`` (~36 models) where authoring a
hand-written test per model would be wasteful.

For every model in those modules we assert the shared invariants from
``_ArgsBase`` (frozen, ``extra="forbid"``, NaN/Inf rejection) plus a
JSON-Schema sanity check.  A new args model added without those
invariants fails this test, surfacing the regression at PR review time.
"""

import importlib
import inspect
import pkgutil
from types import ModuleType

import pytest
from pydantic import BaseModel

from synthorg.meta.mcp.domains import _remaining_args, _workflows_org_args


def _iter_module_and_submodules(module: ModuleType) -> list[ModuleType]:
    """Return ``module`` plus every direct submodule it owns.

    For a flat ``.py`` module returns just ``[module]``.  For a package,
    walks the package's direct submodules via :func:`pkgutil.iter_modules`
    so models defined in submodules are discovered even when not
    re-exported from the package's ``__init__``.
    """
    modules: list[ModuleType] = [module]
    paths = getattr(module, "__path__", None)
    if paths is None:
        return modules
    base_name = module.__name__
    for info in pkgutil.iter_modules(paths):
        submodule = importlib.import_module(f"{base_name}.{info.name}")
        modules.append(submodule)
    return modules


def _models_in(module: ModuleType) -> list[type[BaseModel]]:
    """Return every concrete public Pydantic model defined in ``module``.

    When ``module`` is a package, walks its direct submodules via
    :func:`pkgutil.iter_modules` and inspects each submodule's classes
    directly (rather than relying on re-exports).  This keeps the smoke
    test honest: a model added to ``_remaining_args/_communication.py``
    without a matching re-export in ``__init__`` is still validated.
    """
    found: dict[str, type[BaseModel]] = {}
    for inspected in _iter_module_and_submodules(module):
        owner = inspected.__name__
        for _name, obj in inspect.getmembers(inspected, inspect.isclass):
            if not issubclass(obj, BaseModel):
                continue
            if obj is BaseModel:
                continue
            # Skip private intermediates (e.g. ``_AgentNameArgs`` mixins).
            if obj.__name__.startswith("_"):
                continue
            # Only models defined in the inspected module itself.
            if obj.__module__ != owner:
                continue
            found.setdefault(obj.__qualname__, obj)
    return list(found.values())


_REMAINING_MODELS = _models_in(_remaining_args)
_WORKFLOWS_ORG_MODELS = _models_in(_workflows_org_args)


@pytest.mark.unit
class TestRemainingArgsSmoke:
    """Every model in ``_remaining_args.py`` shares the args base invariants."""

    def test_collection_is_non_empty(self) -> None:
        """Sanity: the module exports models we can iterate over."""
        assert len(_REMAINING_MODELS) > 0

    @pytest.mark.parametrize(
        "model",
        _REMAINING_MODELS,
        ids=lambda m: m.__name__,
    )
    def test_frozen(self, model: type[BaseModel]) -> None:
        """Args models are frozen so callers cannot mutate after construction."""
        assert model.model_config.get("frozen") is True

    @pytest.mark.parametrize(
        "model",
        _REMAINING_MODELS,
        ids=lambda m: m.__name__,
    )
    def test_forbids_extra(self, model: type[BaseModel]) -> None:
        """No model accepts unknown keys."""
        assert model.model_config.get("extra") == "forbid"

    @pytest.mark.parametrize(
        "model",
        _REMAINING_MODELS,
        ids=lambda m: m.__name__,
    )
    def test_rejects_inf_nan(self, model: type[BaseModel]) -> None:
        """No model accepts NaN/Inf in numeric fields."""
        assert model.model_config.get("allow_inf_nan") is False

    @pytest.mark.parametrize(
        "model",
        _REMAINING_MODELS,
        ids=lambda m: m.__name__,
    )
    def test_json_schema_round_trip(self, model: type[BaseModel]) -> None:
        """Every model emits a JSON schema with declared object shape."""
        schema = model.model_json_schema()
        assert schema.get("type") == "object"
        assert "properties" in schema or schema.get("title") is not None


@pytest.mark.unit
class TestWorkflowsOrgArgsSmoke:
    """Every model in ``_workflows_org_args.py`` shares the args base invariants."""

    def test_collection_is_non_empty(self) -> None:
        """Sanity: the module exports models we can iterate over."""
        assert len(_WORKFLOWS_ORG_MODELS) > 0

    @pytest.mark.parametrize(
        "model",
        _WORKFLOWS_ORG_MODELS,
        ids=lambda m: m.__name__,
    )
    def test_frozen(self, model: type[BaseModel]) -> None:
        """Args models are frozen so callers cannot mutate after construction."""
        assert model.model_config.get("frozen") is True

    @pytest.mark.parametrize(
        "model",
        _WORKFLOWS_ORG_MODELS,
        ids=lambda m: m.__name__,
    )
    def test_forbids_extra(self, model: type[BaseModel]) -> None:
        """No model accepts unknown keys."""
        assert model.model_config.get("extra") == "forbid"

    @pytest.mark.parametrize(
        "model",
        _WORKFLOWS_ORG_MODELS,
        ids=lambda m: m.__name__,
    )
    def test_rejects_inf_nan(self, model: type[BaseModel]) -> None:
        """No model accepts NaN/Inf in numeric fields."""
        assert model.model_config.get("allow_inf_nan") is False

    @pytest.mark.parametrize(
        "model",
        _WORKFLOWS_ORG_MODELS,
        ids=lambda m: m.__name__,
    )
    def test_json_schema_round_trip(self, model: type[BaseModel]) -> None:
        """Every model emits a JSON schema with declared object shape."""
        schema = model.model_json_schema()
        assert schema.get("type") == "object"
        assert "properties" in schema or schema.get("title") is not None
