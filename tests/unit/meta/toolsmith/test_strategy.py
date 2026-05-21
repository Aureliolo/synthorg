"""Unit tests for the LLM tool blueprint generator."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.config import ToolsmithConfig
from synthorg.meta.toolsmith.errors import (
    ToolAuthoringError,
    ToolCapabilityNotAllowedError,
)
from synthorg.meta.toolsmith.models import (
    CapabilityGap,
    ToolBlueprintState,
    ToolSandboxBackend,
)
from synthorg.meta.toolsmith.strategy import LLMToolBlueprintGenerator
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)

_VALID_RESPONSE = json.dumps(
    {
        "description": "Slugify text deterministically.",
        "action_type": "code:read",
        "parameters_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "script_body": (
            "import os, json, re\n"
            "args = json.loads(os.environ['SYNTHORG_TOOL_ARGS'])\n"
            "print(json.dumps({'slug': re.sub(r'\\\\W+', '-', "
            "args['text']).strip('-').lower()}))"
        ),
    }
)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeProvider:
    """Structural BaseCompletionProvider stand-in returning canned content."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    async def complete(
        self, *, messages: Any, model: str, config: Any
    ) -> _FakeResponse:
        del messages, model, config
        self.calls += 1
        return _FakeResponse(self._content)


def _gap(signature: str = "textkit:slugify", occurrences: int = 3) -> CapabilityGap:
    return CapabilityGap(
        signature=NotBlankStr(signature),
        occurrences=occurrences,
        first_seen=_NOW,
        last_seen=_NOW,
    )


def _generator(
    *,
    content: str = _VALID_RESPONSE,
    allowed: tuple[str, ...] = ("textkit:slugify",),
) -> LLMToolBlueprintGenerator:
    config = ToolsmithConfig(
        enabled=True,
        allowed_capabilities=tuple(NotBlankStr(c) for c in allowed),
        sandbox_backend=ToolSandboxBackend.DOCKER,
    )
    return LLMToolBlueprintGenerator(
        config=config,
        provider=_FakeProvider(content),  # type: ignore[arg-type]
        clock=FakeClock(start=_NOW),
    )


class TestLLMToolBlueprintGenerator:
    async def test_authors_valid_blueprint(self) -> None:
        gen = _generator()
        bp = await gen.author(_gap(), existing_capabilities=())
        assert bp.name == "synthorg_textkit_slugify"
        assert bp.capability == "textkit:slugify"
        assert bp.action_type == "code:read"
        assert bp.state is ToolBlueprintState.PENDING
        assert bp.sandbox_backend is ToolSandboxBackend.DOCKER
        assert bp.requires_network is False
        assert bp.created_at == _NOW
        assert "SYNTHORG_TOOL_ARGS" in bp.script_body

    async def test_name_derived_from_capability_not_model(self) -> None:
        # The tool name is derived from the capability so name/capability
        # always align, regardless of anything the model emits.
        gen = _generator(allowed=("datakit:parse",))
        bp = await gen.author(
            _gap(signature="datakit:parse"),
            existing_capabilities=(),
        )
        assert bp.name == "synthorg_datakit_parse"
        assert bp.capability == "datakit:parse"

    async def test_capability_not_allowed_rejected(self) -> None:
        gen = _generator(allowed=("other:thing",))
        with pytest.raises(ToolCapabilityNotAllowedError):
            await gen.author(_gap(), existing_capabilities=())

    async def test_invalid_json_raises_authoring_error(self) -> None:
        gen = _generator(content="not json")
        with pytest.raises(ToolAuthoringError):
            await gen.author(_gap(), existing_capabilities=())

    async def test_missing_field_raises_authoring_error(self) -> None:
        partial = json.dumps({"description": "x", "action_type": "code:read"})
        gen = _generator(content=partial)
        with pytest.raises(ToolAuthoringError):
            await gen.author(_gap(), existing_capabilities=())

    async def test_bad_action_type_raises_authoring_error(self) -> None:
        bad = json.dumps(
            {
                "description": "x",
                "action_type": "invalid",
                "parameters_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                "script_body": "print('{}')",
            }
        )
        gen = _generator(content=bad)
        with pytest.raises(ToolAuthoringError):
            await gen.author(_gap(), existing_capabilities=())
