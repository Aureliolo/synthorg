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

import inspect

import pytest
from pydantic import BaseModel

from synthorg.meta.mcp.domains import _remaining_args, _workflows_org_args


def _models_in(module: object) -> list[type[BaseModel]]:
    """Return every concrete public Pydantic model defined in ``module``."""
    found: list[type[BaseModel]] = []
    module_name = getattr(module, "__name__", None)
    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if not issubclass(obj, BaseModel):
            continue
        if obj is BaseModel:
            continue
        # Skip private intermediates (e.g. ``_AgentNameArgs`` mixins).
        if obj.__name__.startswith("_"):
            continue
        # Only models defined in this module, not re-exports.
        if obj.__module__ != module_name:
            continue
        found.append(obj)
    return found


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
