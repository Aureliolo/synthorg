"""Tests for the ``synthorg_query_feature_map`` MCP handler.

The handler builds the feature index in-memory from ``discover_features()``
and returns it as an MCP ok envelope; an optional ``name`` arg filters to
one feature. Unknown names return an empty feature list (read tools never
4xx on a clean filter miss).
"""

import json

import pytest

from synthorg.meta.mcp.handlers.meta import META_HANDLERS

pytestmark = pytest.mark.unit

_TOOL = "synthorg_meta_query_feature_map"


async def _invoke(arguments: dict[str, object]) -> dict[str, object]:
    """Call the handler with an empty app_state and return the parsed envelope."""
    handler = META_HANDLERS[_TOOL]
    raw = await handler(app_state=object(), arguments=arguments, actor=None)
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


async def test_query_without_filter_returns_full_index() -> None:
    envelope = await _invoke({})
    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert isinstance(data, dict)
    assert isinstance(data["features"], list)
    names = {feature["name"] for feature in data["features"]}
    assert "charter" in names
    assert "engine" in names


async def test_query_filters_by_exact_name() -> None:
    envelope = await _invoke({"name": "charter"})
    assert envelope["status"] == "ok"
    features = envelope["data"]["features"]
    assert len(features) == 1
    assert features[0]["name"] == "charter"
    assert features[0]["settings_namespace"] == "charter"
    assert "CharterController" in features[0]["controllers"]


async def test_query_unknown_name_returns_empty_features() -> None:
    envelope = await _invoke({"name": "no_such_feature_anywhere"})
    assert envelope["status"] == "ok"
    assert envelope["data"]["features"] == []


async def test_query_args_model_rejects_blank_name() -> None:
    """The blank-name guard lives on the args model; the invoker validates it.

    The handler itself never sees a blank string because
    :class:`MetaQueryFeatureMapArgs` declares ``name: NotBlankStr | None``;
    confirming the model directly keeps the contract close to the test.
    """
    from pydantic import ValidationError

    from synthorg.meta.mcp.domains._simple_args import MetaQueryFeatureMapArgs

    with pytest.raises(ValidationError):
        MetaQueryFeatureMapArgs(name="")
