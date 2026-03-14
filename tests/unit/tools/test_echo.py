"""Tests for EchoTool."""

import pytest

from synthorg.providers.models import ToolCall, ToolDefinition
from synthorg.tools.examples.echo import EchoTool
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestEchoToolProperties:
    """Tests for EchoTool name, description, schema."""

    def test_name(self) -> None:
        tool = EchoTool()
        assert tool.name == "echo"

    def test_description(self) -> None:
        tool = EchoTool()
        assert tool.description == "Echoes the input message back"

    def test_schema(self) -> None:
        tool = EchoTool()
        schema = tool.parameters_schema
        assert schema is not None
        assert schema["type"] == "object"
        assert "message" in schema["properties"]
        assert schema["required"] == ["message"]
        assert schema["additionalProperties"] is False

    def test_to_definition(self) -> None:
        tool = EchoTool()
        defn = tool.to_definition()
        assert isinstance(defn, ToolDefinition)
        assert defn.name == "echo"


@pytest.mark.unit
class TestEchoToolExecution:
    """Tests for EchoTool execute method."""

    async def test_echoes_message(self) -> None:
        tool = EchoTool()
        result = await tool.execute(arguments={"message": "hello world"})
        assert result.content == "hello world"

    async def test_is_not_error(self) -> None:
        tool = EchoTool()
        result = await tool.execute(arguments={"message": "test"})
        assert result.is_error is False

    async def test_metadata_empty(self) -> None:
        tool = EchoTool()
        result = await tool.execute(arguments={"message": "test"})
        assert result.metadata == {}


@pytest.mark.unit
class TestEchoToolIntegration:
    """Integration test: registry -> invoker -> invoke with ToolCall."""

    async def test_full_pipeline(self) -> None:
        tool = EchoTool()
        registry = ToolRegistry([tool])
        invoker = ToolInvoker(registry)
        call = ToolCall(
            id="call_echo_001",
            name="echo",
            arguments={"message": "integration test"},
        )
        result = await invoker.invoke(call)
        assert result.tool_call_id == "call_echo_001"
        assert result.content == "integration test"
        assert result.is_error is False
