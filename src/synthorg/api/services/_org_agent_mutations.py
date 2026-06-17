"""Agent-level mutations for ``OrgMutationService``.

Extracted from ``org_mutations.py`` to keep that module focused on
the service orchestration.
"""

import json
from collections.abc import Mapping

from pydantic import JsonValue

from synthorg.api._model_validation import validate_provider_model_pair
from synthorg.api.concurrency import check_if_match, compute_etag
from synthorg.config.agent_schema import AgentConfig
from synthorg.config.schema import ProviderConfig
from synthorg.core.company_departments import Department
from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from synthorg.core.normalization import compare_ci, normalize_identifier
from synthorg.core.types import stable_agent_id
from synthorg.hr.seniority import SeniorityLevel
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
from synthorg.organization.models import (
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
        """Read a setting value together with its concurrency version token."""
        raise NotImplementedError

    async def _read_departments(  # pragma: no cover - see concrete
        self,
    ) -> tuple[Department, ...]:
        """Read the company's departments."""
        raise NotImplementedError

    async def _read_agents(  # pragma: no cover - see concrete
        self,
    ) -> tuple[AgentConfig, ...]:
        """Read the company's agents."""
        raise NotImplementedError

    async def _read_provider_configs(  # pragma: no cover - see concrete
        self,
    ) -> Mapping[str, ProviderConfig]:
        """Read the resolved provider configs for catalog validation."""
        raise NotImplementedError

    async def _write_agents(  # pragma: no cover - see concrete
        self,
        agents: tuple[AgentConfig, ...],
        *,
        expected_updated_at: str | None = None,
    ) -> None:
        """Persist the agent roster under optimistic-concurrency control."""
        raise NotImplementedError

    async def _snapshot_company(  # pragma: no cover - see concrete
        self, saved_by: str
    ) -> None:
        """Record a company snapshot attributed to the saver."""
        raise NotImplementedError

    def _find_department(  # pragma: no cover - see concrete
        self, departments: tuple[Department, ...], name: str
    ) -> Department | None:
        """Find a department by name within the given tuple."""
        raise NotImplementedError

    def _find_agent(  # pragma: no cover - see concrete
        self, agents: tuple[AgentConfig, ...], name: str
    ) -> AgentConfig | None:
        """Find an agent by name within the given tuple."""
        raise NotImplementedError

    def _validate_permutation(  # pragma: no cover - see concrete
        self,
        current_names: tuple[str, ...],
        requested_names: tuple[str, ...],
        entity: str,
    ) -> None:
        """Validate that requested names are a permutation of current names."""
        raise NotImplementedError

    async def create_agent(
        self,
        data: CreateAgentOrgRequest,
        *,
        saved_by: str = "api",
    ) -> AgentConfig:
        """Create a new agent in the org config.

        Returns:
            ``AgentConfig`` instance.

        Raises:
            ValidationError: Raised on the corresponding failure path.
            ConflictError: Raised on the corresponding failure path.
        """
        captured: dict[str, AgentConfig] = {}

        async def read() -> tuple[tuple[AgentConfig, ...], str]:
            """Return read.

            Raises:
                ConflictError: Raised on the corresponding failure path.
                ValidationError: Raised on the corresponding failure path.
            """
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

            model_dict: dict[str, JsonValue] = {}
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

        # Log post-commit values (the persisted model can normalise /
        # coerce input, e.g. case-folding the name); the request-payload
        # values can drift from stored truth.
        agent = captured["agent"]
        logger.info(
            API_AGENT_CREATED,
            agent=agent.name,
            department=agent.department,
            level=agent.level.value,
        )
        return agent

    async def _validate_agent_update(
        self,
        name: str,
        data: UpdateAgentOrgRequest,
        agents: tuple[AgentConfig, ...],
    ) -> dict[str, object]:
        """Validate agent update and collect field changes.

        Returns:
            Mapping with the declared key/value types.

        Raises:
            ConflictError: Raised on the corresponding failure path.
            ValidationError: Raised on the corresponding failure path.
        """
        updates: dict[str, object] = {}
        fields_set = data.model_fields_set

        if "name" in fields_set and data.name is not None:
            if self._find_agent(
                tuple(a for a in agents if not compare_ci(a.name, name)),
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
        # Catalog-existence check: a concrete pair must reference a
        # provider + model the live catalogue still exposes (the setup
        # endpoint already validates this; the post-setup PATCH path now
        # closes the same gap so an agent cannot be repointed at a model
        # no provider serves).
        if data.model_provider is not None and data.model_id is not None:
            providers = await self._read_provider_configs()
            validate_provider_model_pair(
                providers,
                str(data.model_provider),
                str(data.model_id),
            )

        return updates

    async def update_agent(
        self,
        name: str,
        data: UpdateAgentOrgRequest,
        *,
        if_match: str | None = None,
        saved_by: str = "api",
    ) -> AgentConfig:
        """Update an existing agent.

        Returns:
            ``AgentConfig`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        captured: dict[str, AgentConfig] = {}
        captured_updates: dict[str, object] = {}

        async def read() -> tuple[tuple[AgentConfig, ...], str]:
            """Return read.

            Raises:
                NotFoundError: Raised on the corresponding failure path.
            """
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
            if "name" in updates:
                # ``model_copy`` bypasses the before-validator that derives the
                # stable id from the name, so a rename would otherwise return
                # and persist the old name's id (now unroutable). Re-stamp it
                # so the row is addressable under its new name.
                updates = {
                    **updates,
                    "id": stable_agent_id(str(updates["name"])),
                }
            updated = existing.model_copy(update=updates, deep=True)
            new_agents = tuple(
                updated if compare_ci(a.name, name) else a for a in agents
            )
            captured_updates.update(updates)
            captured["updated"] = updated
            return new_agents, version

        async def write(
            new_agents: tuple[AgentConfig, ...],
            version: str,
        ) -> None:
            await self._write_agents(new_agents, expected_updated_at=version)
            await self._snapshot_company(saved_by=saved_by)

        await CASRetryHandler(resource="org_mutation").execute(read, write)

        # Always log the post-commit canonical name; the row's stored
        # identifier is authoritative even when the request didn't
        # rename it (the persisted model can still normalise case /
        # whitespace) and the conditional form added no benefit.
        committed_agent = captured["updated"]
        logger.info(
            API_AGENT_UPDATED,
            agent=committed_agent.name,
            updated_fields=list(captured_updates.keys()),
        )
        return committed_agent

    async def delete_agent(self, name: str, *, saved_by: str = "api") -> None:
        """Delete an agent from the org config.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
            ConflictError: Raised on the corresponding failure path.
        """
        captured: dict[str, AgentConfig] = {}

        async def read() -> tuple[tuple[AgentConfig, ...], str]:
            """Return read.

            Raises:
                ConflictError: Raised on the corresponding failure path.
                NotFoundError: Raised on the corresponding failure path.
            """
            _, version = await self._read_setting_versioned("company", "agents")
            agents = await self._read_agents()
            existing = self._find_agent(agents, name)
            if existing is None:
                msg = f"Agent {name!r} not found"
                logger.warning(API_RESOURCE_NOT_FOUND, reason=msg, agent=name)
                raise NotFoundError(msg)

            if existing.level == SeniorityLevel.C_SUITE and compare_ci(
                existing.role, "ceo"
            ):
                msg = f"Cannot delete CEO agent {name!r} -- reassign or demote first"
                logger.warning(
                    API_RESOURCE_CONFLICT,
                    reason=msg,
                    agent=existing.name,
                    level=existing.level.value,
                    role=existing.role,
                )
                raise ConflictError(msg)

            captured["resolved"] = existing
            new_agents = tuple(a for a in agents if not compare_ci(a.name, name))
            return new_agents, version

        async def write(
            new_agents: tuple[AgentConfig, ...],
            version: str,
        ) -> None:
            await self._write_agents(new_agents, expected_updated_at=version)
            await self._snapshot_company(saved_by=saved_by)

        await CASRetryHandler(resource="org_mutation").execute(read, write)
        # Log the resolved agent's persisted identifier rather than the
        # caller-supplied ``name``; the lookup is case-insensitive so
        # the two can differ in case / whitespace, and audit consistency
        # benefits from always emitting the canonical row id.
        resolved = captured.get("resolved")
        agent_for_log = resolved.name if resolved is not None else name
        logger.info(API_AGENT_DELETED, agent=agent_for_log)

    async def reorder_agents(
        self,
        dept_name: str,
        data: ReorderAgentsRequest,
        *,
        saved_by: str = "api",
    ) -> tuple[AgentConfig, ...]:
        """Reorder agents within a department.

        Returns:
            Tuple of the declared element types.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        captured: dict[str, tuple[AgentConfig, ...]] = {}

        async def read() -> tuple[tuple[AgentConfig, ...], str]:
            """Return read.

            Raises:
                NotFoundError: Raised on the corresponding failure path.
            """
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
                a for a in agents if compare_ci(a.department, dept_name)
            )
            current_names = tuple(a.name for a in dept_agents)
            self._validate_permutation(current_names, data.agent_names, "agent")

            agent_by_normalised = {normalize_identifier(a.name): a for a in dept_agents}
            reordered_dept = tuple(
                agent_by_normalised[normalize_identifier(n)] for n in data.agent_names
            )
            captured["reordered_dept"] = reordered_dept

            new_agents: list[AgentConfig] = []
            dept_inserted = False
            for a in agents:
                if compare_ci(a.department, dept_name):
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
