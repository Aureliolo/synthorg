"""Agent-level mutations for ``OrgMutationService``.

Extracted from ``org_mutations.py`` to keep that module focused on
the service orchestration.
"""

import json
from typing import TYPE_CHECKING, Any

from synthorg.api.concurrency import check_if_match, compute_etag
from synthorg.config.schema import AgentConfig
from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from synthorg.core.enums import SeniorityLevel
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AGENT_CREATED,
    API_AGENT_DELETED,
    API_AGENT_UPDATED,
    API_AGENTS_REORDERED,
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
    API_VALIDATION_FAILED,
)

if TYPE_CHECKING:
    from synthorg.api.dto_org import (
        CreateAgentOrgRequest,
        ReorderAgentsRequest,
        UpdateAgentOrgRequest,
    )

logger = get_logger(__name__)


class OrgAgentMutationsMixin:
    """Agent CRUD + reorder for ``OrgMutationService``."""

    async def _read_setting_versioned(  # pragma: no cover - see concrete
        self, namespace: str, key: str
    ) -> tuple[str, str]:
        raise NotImplementedError

    async def _read_departments(  # pragma: no cover - see concrete
        self,
    ) -> tuple[Any, ...]:
        raise NotImplementedError

    async def _read_agents(  # pragma: no cover - see concrete
        self,
    ) -> tuple[AgentConfig, ...]:
        raise NotImplementedError

    async def _write_agents(  # pragma: no cover - see concrete
        self,
        agents: tuple[AgentConfig, ...],
        *,
        expected_updated_at: str | None = None,
    ) -> None:
        raise NotImplementedError

    async def _snapshot_company(  # pragma: no cover - see concrete
        self, saved_by: str
    ) -> None:
        raise NotImplementedError

    def _find_department(  # pragma: no cover - see concrete
        self, departments: tuple[Any, ...], name: str
    ) -> Any | None:
        raise NotImplementedError

    def _find_agent(  # pragma: no cover - see concrete
        self, agents: tuple[AgentConfig, ...], name: str
    ) -> AgentConfig | None:
        raise NotImplementedError

    def _validate_permutation(  # pragma: no cover - see concrete
        self,
        current_names: tuple[str, ...],
        requested_names: tuple[str, ...],
        entity: str,
    ) -> None:
        raise NotImplementedError

    async def create_agent(
        self,
        data: CreateAgentOrgRequest,
        *,
        saved_by: str = "api",
    ) -> AgentConfig:
        """Create a new agent in the org config."""
        captured: dict[str, AgentConfig] = {}

        async def read() -> tuple[tuple[AgentConfig, ...], str]:
            _, version = await self._read_setting_versioned("company", "agents")
            departments = await self._read_departments()
            if not self._find_department(departments, data.department):
                msg = f"Department {data.department!r} does not exist"
                logger.warning(
                    API_VALIDATION_FAILED,
                    reason=msg,
                    department=data.department,
                )
                raise ValidationError(msg)

            agents = await self._read_agents()
            if self._find_agent(agents, data.name):
                msg = f"Agent {data.name!r} already exists"
                logger.warning(
                    API_RESOURCE_CONFLICT,
                    reason=msg,
                    agent=data.name,
                )
                raise ConflictError(msg)

            model_dict: dict[str, Any] = {}
            if data.model_provider is not None:
                model_dict = {
                    "provider": str(data.model_provider),
                    "model_id": str(data.model_id),
                }

            agent = AgentConfig(
                name=data.name,
                role=data.role,
                department=data.department,
                level=data.level,
                model=model_dict,
            )
            captured["agent"] = agent
            return (*agents, agent), version

        async def write(
            new_agents: tuple[AgentConfig, ...],
            version: str,
        ) -> None:
            await self._write_agents(new_agents, expected_updated_at=version)
            await self._snapshot_company(saved_by=saved_by)

        await CASRetryHandler(resource="org_mutation").execute(read, write)

        agent = captured["agent"]
        logger.info(
            API_AGENT_CREATED,
            agent=data.name,
            department=data.department,
            level=data.level.value,
        )
        return agent

    async def _validate_agent_update(
        self,
        name: str,
        data: UpdateAgentOrgRequest,
        agents: tuple[AgentConfig, ...],
    ) -> dict[str, Any]:
        """Validate agent update and collect field changes."""
        updates: dict[str, Any] = {}
        fields_set = data.model_fields_set

        if "name" in fields_set and data.name is not None:
            if self._find_agent(
                tuple(a for a in agents if a.name.lower() != name.lower()),
                str(data.name),
            ):
                msg = f"Agent {data.name!r} already exists"
                logger.warning(
                    API_RESOURCE_CONFLICT, reason=msg, agent_name=str(data.name)
                )
                raise ConflictError(msg)
            updates["name"] = data.name

        if "role" in fields_set and data.role is not None:
            updates["role"] = data.role

        if "department" in fields_set and data.department is not None:
            departments = await self._read_departments()
            if not self._find_department(departments, str(data.department)):
                msg = f"Department {data.department!r} does not exist"
                logger.warning(
                    API_VALIDATION_FAILED, reason=msg, department=str(data.department)
                )
                raise ValidationError(msg)
            updates["department"] = data.department

        if "level" in fields_set and data.level is not None:
            updates["level"] = data.level

        if "autonomy_level" in fields_set:
            updates["autonomy_level"] = data.autonomy_level

        if "model_provider" in fields_set:
            updates["model_provider"] = data.model_provider
        if "model_id" in fields_set:
            updates["model_id"] = data.model_id

        return updates

    async def update_agent(
        self,
        name: str,
        data: UpdateAgentOrgRequest,
        *,
        if_match: str | None = None,
        saved_by: str = "api",
    ) -> AgentConfig:
        """Update an existing agent."""
        captured: dict[str, Any] = {}

        async def read() -> tuple[tuple[AgentConfig, ...], str]:
            _, version = await self._read_setting_versioned("company", "agents")
            agents = await self._read_agents()
            existing = self._find_agent(agents, name)
            if existing is None:
                msg = f"Agent {name!r} not found"
                logger.warning(API_RESOURCE_NOT_FOUND, reason=msg, agent=name)
                raise NotFoundError(msg)

            if if_match:
                cur = json.dumps(
                    existing.model_dump(mode="json"),
                    sort_keys=True,
                )
                check_if_match(if_match, compute_etag(cur, ""), f"agent:{name}")

            updates = await self._validate_agent_update(name, data, agents)
            updated = existing.model_copy(update=updates, deep=True)
            new_agents = tuple(
                updated if a.name.lower() == name.lower() else a for a in agents
            )
            captured["updates"] = updates
            captured["updated"] = updated
            return new_agents, version

        async def write(
            new_agents: tuple[AgentConfig, ...],
            version: str,
        ) -> None:
            await self._write_agents(new_agents, expected_updated_at=version)
            await self._snapshot_company(saved_by=saved_by)

        await CASRetryHandler(resource="org_mutation").execute(read, write)

        logger.info(
            API_AGENT_UPDATED,
            agent=name,
            updated_fields=list(captured["updates"].keys()),
        )
        return captured["updated"]

    async def delete_agent(self, name: str, *, saved_by: str = "api") -> None:
        """Delete an agent from the org config."""

        async def read() -> tuple[tuple[AgentConfig, ...], str]:
            _, version = await self._read_setting_versioned("company", "agents")
            agents = await self._read_agents()
            existing = self._find_agent(agents, name)
            if existing is None:
                msg = f"Agent {name!r} not found"
                logger.warning(API_RESOURCE_NOT_FOUND, reason=msg, agent=name)
                raise NotFoundError(msg)

            if (
                existing.level == SeniorityLevel.C_SUITE
                and existing.role.lower() == "ceo"
            ):
                msg = f"Cannot delete CEO agent {name!r} -- reassign or demote first"
                logger.warning(
                    API_RESOURCE_CONFLICT,
                    reason=msg,
                    agent=name,
                    level=existing.level.value,
                    role=existing.role,
                )
                raise ConflictError(msg)

            new_agents = tuple(a for a in agents if a.name.lower() != name.lower())
            return new_agents, version

        async def write(
            new_agents: tuple[AgentConfig, ...],
            version: str,
        ) -> None:
            await self._write_agents(new_agents, expected_updated_at=version)
            await self._snapshot_company(saved_by=saved_by)

        await CASRetryHandler(resource="org_mutation").execute(read, write)
        logger.info(API_AGENT_DELETED, agent=name)

    async def reorder_agents(
        self,
        dept_name: str,
        data: ReorderAgentsRequest,
        *,
        saved_by: str = "api",
    ) -> tuple[AgentConfig, ...]:
        """Reorder agents within a department."""
        captured: dict[str, tuple[AgentConfig, ...]] = {}

        async def read() -> tuple[tuple[AgentConfig, ...], str]:
            _, version = await self._read_setting_versioned("company", "agents")
            departments = await self._read_departments()
            if not self._find_department(departments, dept_name):
                msg = f"Department {dept_name!r} not found"
                logger.warning(
                    API_RESOURCE_NOT_FOUND,
                    reason=msg,
                    department=dept_name,
                )
                raise NotFoundError(msg)

            agents = await self._read_agents()
            dept_agents = tuple(
                a for a in agents if a.department.lower() == dept_name.lower()
            )
            current_names = tuple(a.name for a in dept_agents)
            self._validate_permutation(current_names, data.agent_names, "agent")

            agent_by_lower = {a.name.lower(): a for a in dept_agents}
            reordered_dept = tuple(agent_by_lower[n.lower()] for n in data.agent_names)
            captured["reordered_dept"] = reordered_dept

            new_agents: list[AgentConfig] = []
            dept_inserted = False
            dept_lower = dept_name.lower()
            for a in agents:
                if a.department.lower() == dept_lower:
                    if not dept_inserted:
                        new_agents.extend(reordered_dept)
                        dept_inserted = True
                else:
                    new_agents.append(a)
            if not dept_inserted:
                new_agents.extend(reordered_dept)

            return tuple(new_agents), version

        async def write(
            new_agents: tuple[AgentConfig, ...],
            version: str,
        ) -> None:
            await self._write_agents(new_agents, expected_updated_at=version)
            await self._snapshot_company(saved_by=saved_by)

        await CASRetryHandler(resource="org_mutation").execute(read, write)

        reordered_dept = captured["reordered_dept"]
        logger.info(
            API_AGENTS_REORDERED,
            department=dept_name,
            order=[a.name for a in reordered_dept],
        )
        return reordered_dept
