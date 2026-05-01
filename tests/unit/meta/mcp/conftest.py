"""Shared fixtures and helpers for MCP unit tests."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.meta.mcp.registry import DomainToolRegistry, MCPToolDef


def make_test_actor(name: str = "test-agent") -> AgentIdentity:
    """Build a minimal ``AgentIdentity`` for handler tests.

    The MCP destructive-op guardrails only inspect ``actor.name``/``.id``,
    so fields carry neutral defaults; individual tests override ``name``
    when they need to distinguish actors.

    Args:
        name: Display name for the synthetic agent.

    Returns:
        Fully-formed ``AgentIdentity`` suitable for passing as ``actor``.
    """
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role="tester",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-medium-001"),
        hiring_date=date(2026, 1, 1),
    )


def make_tool(
    name: str = "synthorg_test_get",
    capability: str = "test:read",
    **overrides: object,
) -> MCPToolDef:
    """Create a minimal MCPToolDef for testing.

    Args:
        name: Tool name.
        capability: Capability tag.
        **overrides: Override any MCPToolDef field.
    """
    defaults: dict[str, object] = {
        "name": name,
        "description": "A test tool",
        "parameters": {"type": "object", "properties": {}},
        "capability": capability,
        "handler_key": name,
    }
    defaults.update(overrides)
    return MCPToolDef(**defaults)  # type: ignore[arg-type]


def registry_with(*tools: MCPToolDef) -> DomainToolRegistry:
    """Create a frozen registry populated with the given tools."""
    registry = DomainToolRegistry()
    for t in tools:
        registry.register(t)
    registry.freeze()
    return registry


@pytest.fixture  # lint-allow: orphan-fixture -- reserved for empty-registry tests
def empty_registry() -> DomainToolRegistry:
    """Return a fresh empty frozen registry."""
    registry = DomainToolRegistry()
    registry.freeze()
    return registry
