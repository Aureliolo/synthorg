"""Builder for :class:`MemoryRecallRequest` in tests.

Injection tests care about one or two fields at a time (usually the
query and the budget). Constructing the full request at every call site
would bury that intent, so this builder supplies the rest.
"""

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.recall_request import MemoryRecallRequest


def recall_request(  # noqa: PLR0913 -- a builder; each field is one test axis
    *,
    agent_id: str = "agent-1",
    query: str = "query",
    token_budget: int = 1000,
    categories: frozenset[MemoryCategory] | None = None,
    objective: str = "",
    role: str = "",
    department: str = "",
    project_id: str | None = None,
) -> MemoryRecallRequest:
    """Build a recall request from the fields a test actually varies.

    ``query`` lands on ``task_title``, so with the other context fields
    left blank the composed ``query_text`` is exactly ``query``.

    Returns:
        The constructed request.
    """
    return MemoryRecallRequest(
        agent_id=NotBlankStr(agent_id),
        task_title=NotBlankStr(query),
        objective=objective,
        role=role,
        department=department,
        project_id=NotBlankStr(project_id) if project_id else None,
        token_budget=token_budget,
        categories=categories if categories is not None else frozenset(),
    )
