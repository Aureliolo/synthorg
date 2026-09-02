# module-kind: tests
"""A structured argument sent as JSON text is decoded, not refused.

A live planning session spent six of eleven turns resubmitting one plan
whose ``subtasks`` arrived as the text of its JSON; every refusal was right
about the type and useless about the fix. Decoding is bounded to what the
schema declares structured, so a string parameter holding JSON stays text.
"""

from typing import override

import pytest
from pydantic import JsonValue

from synthorg.providers.models import ToolCall
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools._argument_decoding import (
    declared_types,
    decode_json_encoded_arguments,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry
from tests._shared import JsonDict

pytestmark = pytest.mark.unit

_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"type": "object"}},
        "options": {"anyOf": [{"type": "object"}, {"type": "null"}]},
        "content": {"type": "string"},
        "count": {"type": "integer"},
        "command": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "cited": {"type": "boolean"},
    },
    "required": ["items"],
}


class TestDeclaredTypes:
    def test_reads_a_plain_type(self) -> None:
        assert declared_types({"type": "array"}) == {"array"}

    def test_reads_through_a_union(self) -> None:
        assert declared_types({"anyOf": [{"type": "object"}, {"type": "null"}]}) == {
            "object",
            "null",
        }

    def test_reads_a_type_list(self) -> None:
        assert declared_types({"type": ["string", "array"]}) == {"string", "array"}

    def test_a_non_schema_declares_nothing(self) -> None:
        assert declared_types("array") == frozenset()


class TestDecodeJsonEncodedArguments:
    def test_json_text_becomes_the_array_the_schema_declares(self) -> None:
        decoded, names = decode_json_encoded_arguments(
            _SCHEMA, {"items": '[{"id": "a"}, {"id": "b"}]'}
        )

        assert decoded == {"items": [{"id": "a"}, {"id": "b"}]}
        assert names == ("items",)

    def test_json_text_becomes_the_object_a_union_declares(self) -> None:
        decoded, names = decode_json_encoded_arguments(
            _SCHEMA, {"items": [], "options": '{"strict": true}'}
        )

        assert decoded["options"] == {"strict": True}
        assert names == ("options",)

    def test_a_string_parameter_holding_json_stays_text(self) -> None:
        # A file's content may well be JSON; the schema says text, so it is.
        decoded, names = decode_json_encoded_arguments(
            _SCHEMA, {"items": [], "content": '{"a": 1}'}
        )

        assert decoded["content"] == '{"a": 1}'
        assert names == ()

    def test_text_that_is_not_json_is_left_for_the_validator(self) -> None:
        decoded, names = decode_json_encoded_arguments(_SCHEMA, {"items": "a, b"})

        assert decoded == {"items": "a, b"}
        assert names == ()

    def test_json_of_the_wrong_shape_is_left_alone(self) -> None:
        # ``"[1]"`` for an object parameter is still the wrong type.
        decoded, names = decode_json_encoded_arguments(
            _SCHEMA, {"items": [], "options": "[1]"}
        )

        assert decoded["options"] == "[1]"
        assert names == ()

    def test_a_number_sent_as_text_becomes_the_number(self) -> None:
        decoded, names = decode_json_encoded_arguments(
            _SCHEMA, {"items": [], "count": "3"}
        )

        assert decoded["count"] == 3
        assert names == ("count",)

    def test_a_boolean_sent_as_text_becomes_the_boolean(self) -> None:
        decoded, names = decode_json_encoded_arguments(
            _SCHEMA, {"items": [], "cited": "false"}
        )

        assert decoded["cited"] is False
        assert names == ("cited",)

    def test_a_true_for_an_integer_stays_text(self) -> None:
        # JSON keeps booleans and integers apart even where Python does not.
        decoded, names = decode_json_encoded_arguments(
            _SCHEMA, {"items": [], "count": "true"}
        )

        assert decoded["count"] == "true"
        assert names == ()

    def test_null_text_for_a_nullable_string_stays_text(self) -> None:
        # A reviewer sent ``'null'`` for its optional command and was refused
        # for naming a command called null. The field admits text, so text is
        # what it is; the refusal is the model's own to fix, and the
        # validator's wording says so.
        decoded, names = decode_json_encoded_arguments(
            _SCHEMA, {"items": [], "command": "null"}
        )

        assert decoded["command"] == "null"
        assert names == ()

    def test_no_schema_decodes_nothing(self) -> None:
        decoded, names = decode_json_encoded_arguments(None, {"items": "[]"})

        assert decoded == {"items": "[]"}
        assert names == ()


class _CountingTool(BaseTool):
    """Returns how many items it was handed, through the JSON-Schema path."""

    def __init__(self) -> None:
        super().__init__(
            name="count_items",
            description="Counts items",
            category=ToolCategory.OTHER,
            parameters_schema={
                "type": "object",
                "properties": {"items": {"type": "array"}},
                "required": ["items"],
                "additionalProperties": False,
            },
        )

    @override
    async def execute(self, *, arguments: JsonDict) -> ToolExecutionResult:
        items = arguments["items"]
        assert isinstance(items, list)
        return ToolExecutionResult(content=str(len(items)))


class TestTheInvokerDecodesBeforeValidating:
    async def test_an_array_sent_as_text_reaches_the_tool_as_a_list(self) -> None:
        invoker = ToolInvoker(ToolRegistry([_CountingTool()]))
        call = ToolCall(id="c1", name="count_items", arguments={"items": "[1, 2, 3]"})

        result = await invoker.invoke(call)

        assert result.is_error is False
        assert result.content == "3"

    async def test_text_that_is_not_an_array_is_still_refused(self) -> None:
        invoker = ToolInvoker(ToolRegistry([_CountingTool()]))
        call = ToolCall(id="c2", name="count_items", arguments={"items": "one, two"})

        result = await invoker.invoke(call)

        assert result.is_error is True
        assert "is not of type 'array'" in result.content
