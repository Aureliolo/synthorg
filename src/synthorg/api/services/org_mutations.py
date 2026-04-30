"""Org configuration mutation service.

Encapsulates read-modify-write operations on the company, department,
and agent configuration stored in the settings system.  Mutations use
compare-and-swap (CAS) via ``expected_updated_at`` on settings writes
to prevent lost updates under concurrent access, with a single retry
on version conflict.
"""

import json
import math
from typing import TYPE_CHECKING, Any

from synthorg.api.concurrency import check_if_match, compute_etag
from synthorg.api.dto_org import (  # noqa: TC001
    UpdateCompanyRequest,
    UpdateDepartmentRequest,
)
from synthorg.api.services._org_agent_mutations import OrgAgentMutationsMixin
from synthorg.api.services._org_department_mutations import OrgDepartmentMutationsMixin
from synthorg.config.schema import AgentConfig  # noqa: TC001
from synthorg.core.company import Company, Department
from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.domain_errors import ValidationError
from synthorg.core.persistence_errors import PersistenceError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_COMPANY_UPDATED,
    API_VALIDATION_FAILED,
)
from synthorg.observability.events.versioning import VERSION_SNAPSHOT_FAILED
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.resolver import ConfigResolver  # noqa: TC001
from synthorg.settings.service import SettingsService  # noqa: TC001
from synthorg.versioning import VersioningService

if TYPE_CHECKING:
    from synthorg.budget.config import BudgetConfig
    from synthorg.persistence.version_repo import VersionRepository

logger = get_logger(__name__)

_BUDGET_PERCENT_CAP = 100.0


class OrgMutationService(OrgAgentMutationsMixin, OrgDepartmentMutationsMixin):
    """Read-modify-write mutations on company/department/agent config.

    Args:
        settings_service: Settings persistence layer.
        config_resolver: Config resolution (DB > env > YAML > code).
        budget_config_versions: Optional repo for BudgetConfig
            version snapshots.  When provided, budget mutations
            automatically create version snapshots.
        company_versions: Optional repo for Company version
            snapshots.  When provided, company/department/agent
            mutations automatically create version snapshots.
    """

    def __init__(
        self,
        settings_service: SettingsService,
        config_resolver: ConfigResolver,
        *,
        budget_config_versions: VersionRepository[BudgetConfig] | None = None,
        company_versions: VersionRepository[Company] | None = None,
    ) -> None:
        self._settings = settings_service
        self._resolver = config_resolver
        self._budget_versioning: VersioningService[BudgetConfig] | None = (
            VersioningService(budget_config_versions)
            if budget_config_versions is not None
            else None
        )
        self._company_versioning: VersioningService[Company] | None = (
            VersioningService(company_versions)
            if company_versions is not None
            else None
        )

    # ── Versioning helpers ────────────────────────────────────

    async def _snapshot_budget_config(self, saved_by: str) -> None:
        """Snapshot the current BudgetConfig if content changed.

        Best-effort: versioning failures are logged but do not
        block the mutation.
        """
        if self._budget_versioning is None:
            return
        try:
            budget = await self._resolver.get_budget_config()
            await self._budget_versioning.snapshot_if_changed(
                entity_id="default",
                snapshot=budget,
                saved_by=saved_by,
            )
        except PersistenceError, SettingNotFoundError, ValueError:
            logger.exception(
                VERSION_SNAPSHOT_FAILED,
                entity_type="BudgetConfig",
                entity_id="default",
            )

    async def _snapshot_company(self, saved_by: str) -> None:
        """Snapshot the current Company structure if content changed."""
        if self._company_versioning is None:
            return
        try:
            name = await self._get_str_safe("company", "company_name")
            departments = await self._read_departments()
            company = Company(
                name=name or "unnamed",
                departments=departments,
            )
            await self._company_versioning.snapshot_if_changed(
                entity_id="default",
                snapshot=company,
                saved_by=saved_by,
            )
        except Exception:
            logger.exception(
                VERSION_SNAPSHOT_FAILED,
                entity_type="Company",
                entity_id="default",
            )

    # ── Internal helpers ──────────────────────────────────────

    async def _read_setting_versioned(
        self,
        namespace: str,
        key: str,
    ) -> tuple[str, str]:
        """Read a setting value and its ``updated_at`` for CAS."""
        result: tuple[str, str] = await self._settings.get_versioned(namespace, key)
        return result

    async def _read_departments(self) -> tuple[Department, ...]:
        return await self._resolver.get_departments()

    async def _write_departments(
        self,
        departments: tuple[Department, ...],
        *,
        expected_updated_at: str | None = None,
    ) -> None:
        """Serialise and persist the department list with CAS."""
        payload = json.dumps(
            [d.model_dump(mode="json") for d in departments],
            separators=(",", ":"),
        )
        await self._settings.set(
            "company",
            "departments",
            payload,
            expected_updated_at=expected_updated_at,
        )

    async def _read_agents(self) -> tuple[AgentConfig, ...]:
        return await self._resolver.get_agents()

    async def _write_agents(
        self,
        agents: tuple[AgentConfig, ...],
        *,
        expected_updated_at: str | None = None,
    ) -> None:
        """Serialise and persist the agent list with CAS."""
        payload = json.dumps(
            [a.model_dump(mode="json") for a in agents],
            separators=(",", ":"),
        )
        await self._settings.set(
            "company",
            "agents",
            payload,
            expected_updated_at=expected_updated_at,
        )

    def _find_department(
        self,
        departments: tuple[Department, ...],
        name: str,
    ) -> Department | None:
        """Case-insensitive department lookup."""
        lower = name.lower()
        for dept in departments:
            if dept.name.lower() == lower:
                return dept
        return None

    @staticmethod
    def _collect_department_updates(
        data: UpdateDepartmentRequest,
    ) -> dict[str, Any]:
        """Extract set fields from an update request."""
        updates: dict[str, Any] = {}
        if "head" in data.model_fields_set:
            updates["head"] = data.head
        if "budget_percent" in data.model_fields_set:
            updates["budget_percent"] = data.budget_percent
        if "autonomy_level" in data.model_fields_set:
            updates["autonomy_level"] = data.autonomy_level
        if "teams" in data.model_fields_set:
            updates["teams"] = tuple(data.teams) if data.teams else ()
        if "ceremony_policy" in data.model_fields_set:
            updates["ceremony_policy"] = data.ceremony_policy
        return updates

    def _find_agent(
        self,
        agents: tuple[AgentConfig, ...],
        name: str,
    ) -> AgentConfig | None:
        """Case-insensitive agent lookup."""
        lower = name.lower()
        for agent in agents:
            if agent.name.lower() == lower:
                return agent
        return None

    def _validate_permutation(
        self,
        current_names: tuple[str, ...],
        requested_names: tuple[str, ...],
        entity: str,
    ) -> None:
        """Ensure requested names are an exact permutation of current."""
        current_set = frozenset(n.lower() for n in current_names)
        requested_set = frozenset(n.lower() for n in requested_names)
        if current_set != requested_set or len(requested_names) != len(
            current_names,
        ):
            msg = f"Reorder must be an exact permutation of existing {entity} names"
            logger.warning(
                API_VALIDATION_FAILED,
                entity=entity,
                current_names=list(current_names),
                requested_names=list(requested_names),
            )
            raise ValidationError(msg)

    def _check_budget_sum(
        self,
        departments: tuple[Department, ...],
    ) -> None:
        """Log a warning if department budgets exceed 100%."""
        total = math.fsum(d.budget_percent for d in departments)
        if total > _BUDGET_PERCENT_CAP:
            logger.warning(
                API_COMPANY_UPDATED,
                note="budget_percent_sum_exceeds_100",
                total=round(total, 2),
            )

    # ── Company ───────────────────────────────────────────────

    async def _get_str_safe(self, namespace: str, key: str) -> str:
        """Get a setting string, returning empty string if not set."""
        try:
            return await self._resolver.get_str(namespace, key)
        except SettingNotFoundError:
            return ""

    async def _company_snapshot_etag(self) -> str:
        """Compute ETag for the full company snapshot."""
        name = await self._get_str_safe("company", "company_name")
        autonomy = await self._get_str_safe("company", "autonomy_level")
        budget = await self._get_str_safe("company", "total_monthly")
        comm = await self._get_str_safe("company", "communication_pattern")
        agents = await self._read_agents()
        depts = await self._read_departments()
        snapshot = {
            "company_name": name,
            "autonomy_level": autonomy,
            "budget_monthly": budget,
            "communication_pattern": comm,
            "agents": [a.model_dump(mode="json") for a in agents],
            "departments": [d.model_dump(mode="json") for d in depts],
        }
        return compute_etag(json.dumps(snapshot, sort_keys=True), "")

    async def update_company(
        self,
        data: UpdateCompanyRequest,
        *,
        if_match: str | None = None,
        saved_by: str = "api",
    ) -> tuple[dict[str, Any], str]:
        """Update individual company scalar settings."""
        captured: dict[str, Any] = {"updated": {}, "new_etag": ""}

        async def read() -> tuple[UpdateCompanyRequest, str]:
            # Pre-write precondition runs every attempt: between
            # attempts a concurrent writer may have changed the
            # snapshot etag, invalidating the operator's If-Match.
            if if_match:
                cur_etag = await self._company_snapshot_etag()
                check_if_match(if_match, cur_etag, "company")
            return data, ""

        async def write(payload: UpdateCompanyRequest, _version: str) -> None:
            captured["updated"] = await self._apply_company_scalars(payload)
            captured["new_etag"] = await self._company_snapshot_etag()
            if "budget_monthly" in captured["updated"]:
                await self._snapshot_budget_config(saved_by=saved_by)
            await self._snapshot_company(saved_by=saved_by)

        await CASRetryHandler(resource="org_mutation").execute(read, write)
        logger.info(API_COMPANY_UPDATED, fields=list(captured["updated"].keys()))
        return captured["updated"], captured["new_etag"]

    async def _apply_company_scalars(
        self,
        data: UpdateCompanyRequest,
    ) -> dict[str, Any]:
        """Atomically write all changed company scalars via set_many."""
        items: list[tuple[str, str, str]] = []
        updated: dict[str, Any] = {}
        if data.company_name is not None:
            items.append(("company", "company_name", data.company_name))
            updated["company_name"] = data.company_name
        if data.autonomy_level is not None:
            items.append(("company", "autonomy_level", data.autonomy_level.value))
            updated["autonomy_level"] = data.autonomy_level.value
        if data.budget_monthly is not None:
            items.append(("company", "total_monthly", str(data.budget_monthly)))
            updated["budget_monthly"] = data.budget_monthly
        if data.communication_pattern is not None:
            items.append(
                ("company", "communication_pattern", data.communication_pattern),
            )
            updated["communication_pattern"] = data.communication_pattern

        if not items:
            return updated

        expected_map: dict[tuple[str, str], str] = {}
        for namespace, key, _value in items:
            _, version = await self._read_setting_versioned(namespace, key)
            expected_map[(namespace, key)] = version

        await self._settings.set_many(
            items,
            expected_updated_at_map=expected_map,
        )
        return updated
