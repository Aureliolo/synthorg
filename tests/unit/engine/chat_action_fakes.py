"""Shared test doubles for direct chat-action tests.

The in-memory parked-context repository and the benign analytics tool are
consumed by both the engine unit suite (``test_run_chat_action``) and the
acceptance e2e (``test_direct_mcp_e2e``), so they live here to keep the
two in lockstep (mirrors ``group_chat_fakes`` for the group-chat tests).
"""

from typing import override

from synthorg.core.types import NotBlankStr
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.timeout.parked_context import ParkedContext
from synthorg.tools.base import BaseTool, ToolExecutionResult


class InMemoryParkedRepo:
    """Stateful in-memory ``ParkedContextRepository`` for the round-trip."""

    def __init__(self) -> None:
        self._by_id: dict[str, ParkedContext] = {}

    async def save(self, entity: ParkedContext) -> None:
        self._by_id[str(entity.id)] = entity

    async def get(self, entity_id: NotBlankStr) -> ParkedContext | None:
        return self._by_id.get(entity_id)

    async def get_by_approval(
        self,
        approval_id: NotBlankStr,
    ) -> ParkedContext | None:
        for parked in self._by_id.values():
            if parked.approval_id == approval_id:
                return parked
        return None

    async def get_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        rows = [p for p in self._by_id.values() if p.agent_id == agent_id]
        return tuple(rows[offset : offset + limit])

    async def list_items(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        rows = list(self._by_id.values())
        return tuple(rows[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._by_id.pop(entity_id, None) is not None


class QueryTool(BaseTool):
    """Benign analytics tool (permitted at STANDARD) that records calls."""

    def __init__(self) -> None:
        super().__init__(
            name="query_metrics",
            description="Query a business metric over a window.",
            category=ToolCategory.ANALYTICS,
            action_type="analytics:query",
            parameters_schema={
                "type": "object",
                "properties": {"window": {"type": "string"}},
                "additionalProperties": True,
            },
        )
        self.calls: list[dict[str, object]] = []

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        self.calls.append(dict(arguments))
        return ToolExecutionResult(content="revenue up 4%", is_error=False)
