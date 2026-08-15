# module-kind: code
"""Who leads an initiative, and who worked it.

One seam for the two questions the retrospective asks about the same thing,
so its consumer holds a participant reader rather than a roster and a task
store it then has to know how to combine. Each question is delegated: the
lead to :func:`resolve_initiative_lead`, the contributors to
:func:`initiative_contributors`.

The two answer different questions about the lead, deliberately. Leading is a
role the roster confers, so a recorded lead who has left it resolves to
nobody and the session that would have run as them declines. Having worked an
initiative is a fact about what happened, which leaving the roster does not
undo, so the contributor list carries the recorded id exactly as it carries
every assignee's, and filtering only this one member by present employment
would be the odd answer.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.contributors import initiative_contributors
from synthorg.engine.initiative.lead import resolve_initiative_lead
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence.task_protocol import TaskRepository


class InitiativeParticipants:
    """Reads who leads an initiative and who has worked it.

    Args:
        registry: The live agent roster, which resolves the recorded lead.
        task_repository: The initiative's tasks, which is where the
            contributor list is derived from.
    """

    __slots__ = ("_registry", "_task_repository")

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        task_repository: TaskRepository,
    ) -> None:
        self._registry = registry
        self._task_repository = task_repository

    async def lead(self, project: Project) -> AgentIdentity | None:
        """Resolve the identity accountable for *project*.

        Returns:
            The lead identity, or ``None`` when the project carries none.
        """
        return await resolve_initiative_lead(self._registry, project)

    async def contributors(self, project: Project) -> tuple[NotBlankStr, ...]:
        """Resolve everyone who worked *project*, plus its lead.

        Returns:
            Sorted, deduplicated agent ids.
        """
        return await initiative_contributors(
            self._task_repository,
            project_id=NotBlankStr(str(project.id)),
            lead_id=NotBlankStr(project.lead) if project.lead else None,
        )


__all__ = ["InitiativeParticipants"]
