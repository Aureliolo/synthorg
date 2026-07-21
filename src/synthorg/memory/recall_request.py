# module-kind: declarative
"""Structured retrieval context for one memory recall.

A task title alone is a terse query, and terse queries under-retrieve:
the memory that would have helped is often phrased in the vocabulary of
the objective, the role or the project rather than of the task line
itself, so recall composes from the full work context, not the title.

Carrying the context as fields rather than pre-joined text keeps the
composition in one place, so every caller produces the same query for
the same work and cached embeddings stay valid.
"""

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.namespace_scope import (
    DEFAULT_MEMORY_NAMESPACE,
    PROJECT_NAMESPACE_PREFIX,
    read_namespaces,
)

__all__ = [
    "DEFAULT_MEMORY_NAMESPACE",
    "PROJECT_NAMESPACE_PREFIX",
    "MemoryRecallRequest",
]


class MemoryRecallRequest(BaseModel):
    """Everything one recall needs to know about the work in hand.

    Attributes:
        agent_id: The agent requesting memories.
        task_title: The task being executed, the primary retrieval anchor.
        objective: The parent objective this task serves, when known.
        role: The agent's role title, when known.
        department: The agent's department, when known.
        project_id: The project the task belongs to, when scoped.
        token_budget: Maximum tokens the injected memory may occupy.
        categories: Restrict recall to these categories; empty means all.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="The agent requesting memories")
    task_title: NotBlankStr = Field(description="Task being executed")
    objective: str = Field(default="", description="Parent objective, when known")
    role: str = Field(default="", description="Agent role title, when known")
    department: str = Field(default="", description="Agent department, when known")
    project_id: NotBlankStr | None = Field(
        default=None, description="Owning project, when scoped"
    )
    token_budget: int = Field(
        default=0,
        ge=0,
        description="Maximum tokens the injected memory may occupy",
    )
    categories: frozenset[MemoryCategory] = Field(
        default_factory=frozenset,
        description="Restrict recall to these categories; empty means all",
    )

    @computed_field
    @property
    def query_text(self) -> str:
        """The retrieval query composed from the work context.

        The task leads because it is the strongest anchor; the remaining
        context follows to widen recall toward memories phrased in the
        vocabulary of the objective, role or department. Blank fields are
        dropped rather than joined, so an absent field contributes no
        separator noise to the embedding.

        The project id is deliberately absent: it is an opaque
        identifier, not vocabulary, so embedding it would inject noise
        rather than signal. The project instead scopes recall through
        :attr:`namespaces`.

        Returns:
            The composed query text.
        """
        parts = (
            self.task_title,
            self.objective,
            self.role,
            self.department,
        )
        return ". ".join(part.strip() for part in parts if part.strip())

    @computed_field
    @property
    def namespaces(self) -> frozenset[NotBlankStr] | None:
        """Storage namespaces recall is scoped to.

        ``None`` (all namespaces) for unscoped work. For a project, the
        agent's own default namespace unioned with the project's, so an
        agent working inside a project recalls both its personal memories
        and that project's, and never another project's.

        Returns:
            The namespace scope, or ``None`` when the work is unscoped.
        """
        return read_namespaces(self.project_id)
